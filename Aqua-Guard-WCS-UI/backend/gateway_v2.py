"""Protocol v2 MQTT handlers: gateway info, registry mirror, numbered-node telemetry, heartbeat counters, pairing events, command results.

The GATEWAY registry (NVS) is the source of truth for which nodes exist.  This module only mirrors what the gateway publishes
(retained `<prefix>/<gw>/nodes/registry`) and stores readings.  Nothing here invents data: unknown fields stay NULL.
All functions take a connection that is already inside a transaction (database.db_session).
"""
import json
import re
from datetime import datetime, timedelta

import ingest
from ingest import IngestError, fmt_ts, parse_ts, utc_now
from protocol_v2 import cmd_tag, parse_cmd_key  # noqa: F401  (cmd_tag used by tests)

TYPES = {"flow": 1, "rain": 2}
TX_LEVELS_X10 = (85, 110, 130, 150)                  # allowed per-node TX power levels, 0.1 dBm (8.5 / 11 / 13 / 15 dBm)
TX_BITS = {85: 0x03, 110: 0x04, 130: 0x06, 150: 0x08}   # cfgFlags the gateway stores (bits 1..3 = level code; 8.5 also keeps the legacy bit 0)
_MAC_RE = re.compile(r"[0-9a-f]{12}")


def norm_mac(s) -> str:
    m = re.sub(r"[:\-\s.]", "", str(s or "")).lower()
    if not _MAC_RE.fullmatch(m):
        raise IngestError(f"bad mac {s!r}")
    return m


def fmt_mac(m: str) -> str:
    return ":".join(m[i:i + 2] for i in range(0, 12, 2)).upper()


def _gid(s) -> str:
    g = str(s or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", g):
        raise IngestError(f"bad gateway id {g!r}")
    return g


# ------------------------------------------------------------------ gateways
def gateway_row(conn, gid: str, now: datetime, create: bool = True):
    r = conn.execute("SELECT * FROM gateways WHERE gateway_id=?", (gid,)).fetchone()
    if r is None and create:
        conn.execute("INSERT INTO gateways (gateway_id, name, first_seen) VALUES (?,?,?)", (gid, gid, fmt_ts(now)))
        r = conn.execute("SELECT * FROM gateways WHERE gateway_id=?", (gid,)).fetchone()
    return r


def sole_gateway(conn):
    """The one and only gateway, or None when there are zero or several.  Topics that carry no gateway id (retained status, legacy node topics, pairing
    events) may only be attributed to a gateway when there is no doubt which one; with several gateways we never guess "the most recently seen"."""
    rows = conn.execute("SELECT * FROM gateways ORDER BY id LIMIT 2").fetchall()
    return rows[0] if len(rows) == 1 else None


def primary_gateway(cfg, conn, now: datetime):
    """The gateway the Overview should show: an online one (the freshest heartbeat if several), else the one heard from most recently, flagged by its own state."""
    rows = conn.execute("SELECT * FROM gateways ORDER BY COALESCE(last_heartbeat_at, last_info_at, registry_at, first_seen) DESC, id DESC").fetchall()
    return next((g for g in rows if gateway_state(cfg, g, now)["state"] == "online"), rows[0] if rows else None)


def handle_info(conn, gid: str, d: dict, now: datetime) -> dict:
    gid = _gid(gid)
    g = gateway_row(conn, gid, now)
    conn.execute("UPDATE gateways SET fw=?, proto=?, mode=?, ip=?, channel=?, rssi=?, setup_done=?, time_synced=?, tls=?, tls_verified=?, remote_add=?,"
                 " last_epoch=?, uptime_s=?, last_info_at=? WHERE id=?",
                 (str(d.get("fw", ""))[:16], d.get("proto"), str(d.get("mode", ""))[:8], str(d.get("ip", ""))[:40], d.get("channel"), d.get("rssi"),
                  int(bool(d.get("setupDone"))), int(bool(d.get("timeSynced"))), int(bool(d.get("tls"))), int(bool(d.get("tlsVerified"))),
                  int(bool(d.get("remoteAdd"))), d.get("epoch"), d.get("uptime"), fmt_ts(now), g["id"]))
    return {"result": "stored", "gateway": gid}


def handle_registry(conn, gid: str, d: dict, now: datetime) -> dict:
    """Mirror the gateway registry.  Nodes the gateway no longer lists are marked 'removed' (their history is kept)."""
    gid = _gid(gid)
    g = gateway_row(conn, gid, now)
    nodes = d.get("nodes")
    if not isinstance(nodes, list):
        raise IngestError("registry: nodes must be a list")
    seen = set()
    r_at = fmt_ts(now)
    for n in nodes[:64]:
        if not isinstance(n, dict) or n.get("type") not in TYPES:
            continue
        number = ingest._int(n, "number", 1, 250)
        mac = norm_mac(n.get("mac"))
        seen.add((n["type"], number))
        state = "paired" if n.get("state") == "paired" else "pending"
        batt = n.get("batteryMv") if isinstance(n.get("batteryMv"), int) and not isinstance(n.get("batteryMv"), bool) else None
        ppl = ingest._int(n, "pplX10", 0, 60000, required=False)
        row = conn.execute("SELECT id, mac FROM nodes WHERE gateway_ref=? AND type=? AND number=?", (g["id"], n["type"], number)).fetchone()
        last_epoch = ingest._int(n, "lastSeenEpoch", 0, 1 << 40, required=False)
        tx = ingest._int(n, "txDbmX10", 0, 300, required=False)
        tx = tx if tx in TX_LEVELS_X10 else 85           # old gateway firmware reports no level: every node runs at the default 8.5 dBm
        if row is None:
            cur = conn.execute("INSERT INTO nodes (gateway_ref,type,number,mac,name,state,online,battery_mv,rssi,last_seen_epoch,ppl_x10,tx_reduce,tx_dbm_x10,added_epoch,registry_at,created_at)"
                               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (g["id"], n["type"], number, mac, str(n.get("name", ""))[:24], state, int(bool(n.get("online"))), batt, n.get("rssi"),
                                last_epoch or None, ppl or None, int(tx <= 85), tx, n.get("addedEpoch"), r_at, r_at))
            ref = cur.lastrowid
        else:
            ref = row["id"]
            conn.execute("UPDATE nodes SET mac=?, name=?, state=?, online=?, battery_mv=CASE WHEN ? THEN ? ELSE battery_mv END, rssi=COALESCE(?,rssi),"
                         " last_seen_epoch=COALESCE(?,last_seen_epoch), ppl_x10=?, tx_reduce=?, tx_dbm_x10=?, added_epoch=?, registry_at=?, removed_at=NULL WHERE id=?",
                         (mac, str(n.get("name", ""))[:24], state, int(bool(n.get("online"))), int("batteryMv" in n), batt, n.get("rssi"), last_epoch or None,
                          ppl or None, int(tx <= 85), tx, n.get("addedEpoch"), r_at, ref))
        note_hw(conn, ref, mac, r_at)
    for r in conn.execute("SELECT id, type, number FROM nodes WHERE gateway_ref=? AND state!='removed'", (g["id"],)).fetchall():
        if (r["type"], r["number"]) not in seen:
            conn.execute("UPDATE nodes SET state='removed', online=0, removed_at=? WHERE id=?", (r_at, r["id"]))
    conn.execute("UPDATE gateways SET registry_rev=?, registry_at=? WHERE id=?", (d.get("rev"), r_at, g["id"]))
    return {"result": "stored", "gateway": gid, "nodes": len(seen)}


def note_hw(conn, node_ref: int, mac: str, r_at: str):
    """Remember which physical device (MAC) stood behind this node number - history stays continuous after a Replace."""
    conn.execute("INSERT INTO node_hw_history (node_ref, mac, first_seen, last_seen) VALUES (?,?,?,?)"
                 " ON CONFLICT(node_ref, mac) DO UPDATE SET last_seen=excluded.last_seen", (node_ref, mac, r_at, r_at))


# ------------------------------------------------------------------ telemetry
def resolve_node(conn, ntype: str, number: int, mac: str, gid: str | None, now: datetime):
    """Find (or create) the node row a reading belongs to.  `gid` is None for the legacy topics, which carry no gateway id."""
    if gid:
        g = gateway_row(conn, _gid(gid), now)
    else:
        row = conn.execute("SELECT g.* FROM gateways g JOIN nodes n ON n.gateway_ref=g.id WHERE n.type=? AND n.number=? AND n.state!='removed'"
                           " ORDER BY (n.mac=?) DESC, COALESCE(g.last_heartbeat_at, g.first_seen) DESC LIMIT 1", (ntype, number, mac)).fetchone()
        g = row or sole_gateway(conn) or gateway_row(conn, "gw-unknown", now)
    r_at = fmt_ts(now)
    n = conn.execute("SELECT * FROM nodes WHERE gateway_ref=? AND type=? AND number=?", (g["id"], ntype, number)).fetchone()
    if n is None:
        cur = conn.execute("INSERT INTO nodes (gateway_ref,type,number,mac,name,state,created_at) VALUES (?,?,?,?,?,'paired',?)",
                           (g["id"], ntype, number, mac, f"{'Rain' if ntype == 'rain' else 'Node'} {number}", r_at))
        n = conn.execute("SELECT * FROM nodes WHERE id=?", (cur.lastrowid,)).fetchone()
    elif n["mac"] != mac:                      # the gateway only forwards verified frames: a new MAC behind a known number = Replace
        conn.execute("UPDATE nodes SET mac=? WHERE id=?", (mac, n["id"]))
    elif n["state"] == "removed":
        conn.execute("UPDATE nodes SET state='paired', removed_at=NULL WHERE id=?", (n["id"],))
    note_hw(conn, n["id"], mac, r_at)
    return g, conn.execute("SELECT * FROM nodes WHERE id=?", (n["id"],)).fetchone()


def ingest_telemetry(conn, d: dict, now: datetime, gid: str | None = None, topic_type: str | None = None, topic_number: int | None = None) -> dict:
    """Schema >= 3 telemetry from `<gw>/node/<N>`, `<gw>/rain/<N>` or the legacy topics (the same JSON on both: stored once)."""
    ingest.check_schema(d)
    ntype = d.get("type")
    if ntype not in TYPES:
        raise IngestError(f"unknown node type {ntype!r}")
    if topic_type and topic_type != ntype:
        raise IngestError(f"payload type {ntype} does not match topic {topic_type}")
    number = ingest._int(d, "node", 1, 250)
    if topic_number is not None and number != topic_number:
        raise IngestError(f"payload node {number} does not match topic node {topic_number}")
    mac = norm_mac(d.get("mac"))
    g, n = resolve_node(conn, ntype, number, mac, gid, now)
    if n["state"] == "pending":
        conn.execute("UPDATE nodes SET state='paired' WHERE id=?", (n["id"],))      # telemetry proves the node joined
    v2 = {"node_ref": n["id"], "hw_mac": mac}
    res = ingest.ingest_flow(conn, d, now, expected_node=number, v2=v2) if ntype == "flow" else ingest.ingest_rain(conn, d, now, v2=v2)
    if res.get("result") == "stored":
        batt = d.get("batteryMv")
        batt = batt if isinstance(batt, int) and not isinstance(batt, bool) else None
        # an explicit null means "no battery sensed" and must clear an old value; an absent key keeps what we had
        conn.execute("UPDATE nodes SET last_seen=?, rssi=COALESCE(?,rssi), battery_mv=CASE WHEN ? THEN ? ELSE battery_mv END, online=1 WHERE id=?",
                     (fmt_ts(now), d.get("rssi"), int("batteryMv" in d), batt, n["id"]))
        res["nodeRef"] = n["id"]
    return res


# ------------------------------------------------------------------ heartbeat / status
def handle_heartbeat(conn, d: dict, now: datetime) -> dict:
    res = ingest.ingest_gateway_heartbeat(conn, d, now)             # v1 tables (health history, device comm health)
    gid = _gid(d.get("gateway"))
    g = gateway_row(conn, gid, now)
    rx, q, wifi = d.get("rx") or {}, d.get("queue") or {}, d.get("wifi") or {}
    epoch = d.get("epoch")
    conn.execute("UPDATE gateways SET last_heartbeat_at=?, uptime_s=?, last_epoch=?, time_synced=?, channel=?, rssi=?, free_heap=?, queue_depth=?, queue_overflow=?,"
                 " rx_frames=?, rx_rejected=?, rx_bad_tag=?, rx_bad_value=?, rx_unknown_mac=?, rx_queue_dropped=? WHERE id=?",
                 (fmt_ts(now), d.get("uptime"), epoch, int(isinstance(epoch, (int, float)) and epoch > ingest.GW_EPOCH_MIN), wifi.get("channel"), wifi.get("rssi"),
                  d.get("freeHeap"), q.get("depth"), q.get("overflow"), rx.get("frames"), rx.get("rejected"), rx.get("badTag"), rx.get("badValue"),
                  rx.get("unknownMac"), rx.get("queueDropped"), g["id"]))
    for n in (d.get("nodes") or [])[:64]:                          # per-node link health -> mirror rows
        if isinstance(n, dict) and n.get("type") in TYPES and isinstance(n.get("node"), int):
            conn.execute("UPDATE nodes SET rssi=COALESCE(?,rssi), last_seen_epoch=COALESCE(NULLIF(?,0),last_seen_epoch) WHERE gateway_ref=? AND type=? AND number=?",
                         (n.get("rssi"), n.get("lastSeenEpoch"), g["id"], n["type"], n["node"]))
    res["gatewayRef"] = g["id"]
    return res


def handle_status(conn, raw: str, now: datetime) -> dict:
    res = ingest.ingest_gateway_status(conn, raw, now)               # v1 tables
    g = sole_gateway(conn)                                           # `<prefix>/gateway/status` has no gateway id: only attributable when there is one gateway
    if g is not None:
        conn.execute("UPDATE gateways SET status_raw=?, status_at=? WHERE id=?", (res["status"], fmt_ts(now), g["id"]))
    return res


def gateway_state(cfg, g, now: datetime) -> dict:
    """online | offline | unknown from the retained status AND the heartbeat age (a retained 'online' with a stale heartbeat is not online)."""
    hb = parse_ts(g["last_heartbeat_at"]) if g["last_heartbeat_at"] else None
    age = max(0.0, (now - hb).total_seconds()) if hb else None      # a clock a little ahead of ours must not show a negative age
    status = g["status_raw"]
    if status == "offline":
        state = "offline"
    elif age is not None and age <= cfg.gateway_offline_s:
        state = "online"
    elif age is None and status != "online":
        state = "unknown"
    else:
        state = "offline"
    synced = bool(g["time_synced"]) if state == "online" else None
    return {"state": state, "heartbeatAgeS": None if age is None else round(age, 1), "statusRaw": status, "timeSynced": synced,
            "timeStatus": "unknown" if synced is None else "synced" if synced else "not_synced"}


# ------------------------------------------------------------------ command results / pairing events
def handle_cmd_result(conn, gid: str, d: dict, now: datetime) -> dict:
    g = gateway_row(conn, _gid(gid), now)
    what = str(d.get("result", ""))[:40]
    rc = d.get("rc") if isinstance(d.get("rc"), int) else None
    ok = what == "ok"                                              # the gateway answers "ok" or the failure reason (duplicate_mac, tls_required, ...)
    row = conn.execute("SELECT id FROM gateway_commands WHERE gateway_ref=? AND status='sent' ORDER BY id ASC LIMIT 1", (g["id"],)).fetchone()
    if row:
        conn.execute("UPDATE gateway_commands SET status=?, result=?, rc=?, resolved_at=?, number=CASE WHEN ? > 0 THEN ? ELSE number END WHERE id=?",
                     ("ok" if ok else "error", what, rc, fmt_ts(now), d.get("number") or 0, d.get("number") or 0, row["id"]))
    return {"result": "stored", "command": what, "ok": ok}


def expire_commands(conn, cfg, now: datetime):
    cutoff = fmt_ts(now - timedelta(seconds=cfg.command_timeout_s))
    conn.execute("UPDATE gateway_commands SET status='timeout', resolved_at=? WHERE status='sent' AND created_at < ?", (fmt_ts(now), cutoff))


def handle_pair_event(conn, cfg, text: str, now: datetime) -> dict:
    """AQE1 <kind D|O|F> <number> <mac12> <devType> <fw> <channel> <rssi> <boot8hex> <slot> - - <epoch> <tag>   (tag = SipHash with the command key)."""
    f = text.strip().split(" ")
    if len(f) != 14 or f[0] != "AQE1":
        raise IngestError("bad pairing event")
    key = parse_cmd_key(cfg.cmd_key_hex)
    if key is None:
        return {"result": "not_configured"}
    body = text.strip().rsplit(" ", 1)[0]
    if cmd_tag(key, body) != f[13]:
        return {"result": "rejected"}                                  # forged or from a gateway with another key
    kind = {"D": "nearby", "O": "joined", "F": "failed"}.get(f[1])
    if kind is None:
        raise IngestError("bad event kind")
    g = sole_gateway(conn)                                           # ambiguous with several gateways: stored without a gateway
    conn.execute("INSERT INTO pairing_events_v2 (gateway_ref,received_at,kind,number,mac,dev_type,fw,channel,rssi,boot_id,epoch,tag_ok) VALUES (?,?,?,?,?,?,?,?,?,?,?,1)",
                 (g["id"] if g else None, fmt_ts(now), kind, int(f[2]), norm_mac(f[3]), int(f[4]), int(f[5]), int(f[6]), int(f[7]), f[8], int(f[12])))
    conn.execute("DELETE FROM pairing_events_v2 WHERE received_at < ?", (fmt_ts(now - timedelta(days=30)),))
    return {"result": "stored", "kind": kind}


# ------------------------------------------------------------------ topic router
_NODE_T = re.compile(r"^(?P<gw>[^/]+)/(?P<kind>node|rain)/(?P<n>\d{1,3})$")
_LEGACY_T = re.compile(r"^node(?P<n>\d{1,3})$")


def route(conn, cfg, sub: str, text: str, now: datetime):
    """Handle a v2 topic.  Returns a result dict, or None when the topic is not a v2 one (caller keeps its own routing)."""
    m = _NODE_T.match(sub)
    if m:
        return ingest_telemetry(conn, json.loads(text), now, gid=m["gw"], topic_type="flow" if m["kind"] == "node" else "rain", topic_number=int(m["n"]))
    parts = sub.split("/")
    if len(parts) == 3 and parts[1] == "gateway" and parts[2] == "info":
        return handle_info(conn, parts[0], json.loads(text), now)
    if len(parts) == 3 and parts[1] == "nodes" and parts[2] == "registry":
        return handle_registry(conn, parts[0], json.loads(text), now)
    if len(parts) == 4 and parts[1] == "gateway" and parts[2] == "cmd" and parts[3] == "result":
        return handle_cmd_result(conn, parts[0], json.loads(text), now)
    if sub == "gateway/heartbeat":
        return handle_heartbeat(conn, json.loads(text), now)
    if sub == "gateway/status":
        return handle_status(conn, text, now)
    m = _LEGACY_T.match(sub)
    if m or sub == "rainwater":
        d = json.loads(text)
        if isinstance(d, dict) and isinstance(d.get("schema"), int) and d["schema"] >= 3 and d.get("mac"):
            return ingest_telemetry(conn, d, now, gid=None, topic_type="flow" if m else "rain", topic_number=int(m["n"]) if m else None)
        return None                                                     # schema 2 (v1 nodes): old path
    if sub == "pairing/event" and text.strip().count(" ") == 13 and parse_cmd_key(cfg.cmd_key_hex) is not None:
        return handle_pair_event(conn, cfg, text, now)                    # protocol v2 events (14 fields); the old AQE1 layout falls through
    return None
