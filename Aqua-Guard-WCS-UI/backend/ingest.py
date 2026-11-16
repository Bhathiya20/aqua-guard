"""Telemetry ingestion: validation, de-duplication and cumulative-counter volume accounting.

All functions take a sqlite connection that is ALREADY inside a transaction
(see database.db_session) so every message is applied atomically.

Volume accounting rule
----------------------
Each node reports a cumulative pulse counter that starts at 0 at boot (boot_id
identifies the epoch).  For a sample S in epoch E:

    delta(S) = (cum(S) - cum(prev sample in E)) mod 2^32          (prev = highest seq below S)

where the very first sample of an epoch is measured against 0 when seq == 1,
or against (cum - interval_pulses) otherwise (we only know its own interval).
Lost packets therefore never lose volume (the next received counter covers
them); retransmitted / duplicate packets are rejected by the UNIQUE
(node, boot, seq) key; out-of-order packets re-split the following sample's
delta, so the SUM over the epoch always equals last_cum - baseline.
Pulses between the last received sample of one epoch and a node reboot cannot
be known and are NOT invented.
"""
import math
from datetime import datetime, timedelta, timezone

U32 = 1 << 32
MAX_ACCEPTED_SKEW_S = 7 * 24 * 3600     # gateway epoch further than this from "now" is ignored
FRESH_BOOT_MAX_SEQ = 300                # first sample seen within this many intervals AND
FRESH_BOOT_MAX_UPTIME_S = 600           # this uptime => counter is known to have started at 0


class IngestError(ValueError):
    """Raised for malformed / out-of-range messages (caller logs and drops)."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def parse_ts(s: str) -> datetime:
    return datetime.strptime(s[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ validation
def _int(d, key, lo, hi, required=True, default=None):
    v = d.get(key, default)
    if v is None:
        if required:
            raise IngestError(f"missing field '{key}'")
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (isinstance(v, float) and not math.isfinite(v)):
        raise IngestError(f"field '{key}' must be a number")
    v = int(v)
    if v < lo or v > hi:
        raise IngestError(f"field '{key}' out of range ({v})")
    return v


def _num(d, key, lo, hi, required=True, default=None):
    v = d.get(key, default)
    if v is None:
        if required:
            raise IngestError(f"missing field '{key}'")
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        raise IngestError(f"field '{key}' must be a number")
    v = float(v)
    if v < lo or v > hi:
        raise IngestError(f"field '{key}' out of range ({v})")
    return v


def _bool(d, key, required=True):
    v = d.get(key)
    if v is None:
        if required:
            raise IngestError(f"missing field '{key}'")
        return None
    if not isinstance(v, bool):
        raise IngestError(f"field '{key}' must be boolean")
    return v


def check_schema(d):
    if not isinstance(d, dict):
        raise IngestError("payload is not a JSON object")
    # schema 3 (protocol v2 gateways: numbered nodes, mac, battery) is accepted next to schema 2 (v1 history/tests).
    # TEMPORARY bridge for Phase 2; the full v2 ingest (battery, registry mirror) is Phase 4.
    if d.get("schema") not in (2, 3):
        raise IngestError(f"unsupported schema version {d.get('schema')!r} (expected 2 or 3)")


def measured_at(d, now: datetime) -> datetime:
    """Prefer the gateway's NTP epoch (when the gateway received the packet) over backend arrival time."""
    ep = d.get("rxEpoch")
    if isinstance(ep, (int, float)) and not isinstance(ep, bool) and ep > 1_700_000_000:
        try:
            t = datetime.fromtimestamp(int(ep), tz=timezone.utc)
            if abs((t - now).total_seconds()) <= MAX_ACCEPTED_SKEW_S:
                return t
        except (OverflowError, OSError, ValueError):
            pass
    return now


# ------------------------------------------------------------------ devices
def _touch_device(conn, key, kind, node_id, label, ts, boot=None, seq=None, channel=None, rssi=None, uptime=None, status=None):
    row = conn.execute("SELECT id, last_boot_id, sessions FROM devices WHERE device_key=?", (key,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO devices (device_key,kind,node_id,label,first_seen,last_seen,last_boot_id,last_seq,channel,rssi,uptime_s,status,sessions)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, kind, node_id, label, ts, ts, boot, seq, channel, rssi, uptime, status, 1 if boot else 0))
        return
    sessions = row["sessions"] or 0
    if boot and row["last_boot_id"] != boot:
        sessions += 1
    # keep the highest seq of the CURRENT boot (recovered/out-of-order packets must not move it backwards)
    conn.execute(
        "UPDATE devices SET last_seen=?, last_boot_id=COALESCE(?,last_boot_id),"
        " last_seq=CASE WHEN ? IS NULL THEN last_seq WHEN last_boot_id IS NOT ? THEN ? WHEN last_seq IS NULL OR ? > last_seq THEN ? ELSE last_seq END,"
        " channel=COALESCE(?,channel), rssi=COALESCE(?,rssi), uptime_s=COALESCE(?,uptime_s), status=COALESCE(?,status), sessions=? WHERE id=?",
        (ts, boot, seq, boot, seq, seq, seq, channel, rssi, uptime, status, sessions, row["id"]))


# ------------------------------------------------------------------ measurement time (protocol v2)
GW_EPOCH_MIN = 1_700_000_000


def _gw_epoch(d, now: datetime):
    ep = d.get("rxEpoch")
    if isinstance(ep, (int, float)) and not isinstance(ep, bool) and ep > GW_EPOCH_MIN and abs(ep - now.timestamp()) <= MAX_ACCEPTED_SKEW_S:
        return int(ep)
    return None


def session_measured_at(conn, node_ref: int, hw_mac: str, boot: int, d, now: datetime, uptime: int, cum: int, seq: int):
    """Measurement time of a sample = (boot anchor) + (node uptime at the sample).

    A recovered/backlog sample ARRIVES late, so its arrival time (rxEpoch / backend clock) is wrong as a measurement time.  The node's
    uptime is not.  Every sample of a boot gives an estimate  anchor = arrival_time - uptime  that can only be TOO LATE (delivery
    delay is never negative), so the anchor of a session is the MINIMUM over all its samples.  Gateway NTP time (rxEpoch) is preferred;
    when the gateway time is not synced the backend receive time is used and the session is flagged lower confidence ('recv').
    Returns (measured_dt, src, session_id, moved): moved=True when the anchor changed and older rows were re-timed."""
    gw = _gw_epoch(d, now)
    cand_src = "gw_epoch" if gw else "recv"
    cand = float((gw if gw else now.timestamp()) - uptime)
    r_at = fmt_ts(now)
    row = conn.execute("SELECT id, anchor_epoch, anchor_src FROM node_sessions WHERE node_ref=? AND boot_id=?", (node_ref, boot)).fetchone()
    moved = False
    if row is None:
        cur = conn.execute("INSERT INTO node_sessions (node_ref,hw_mac,boot_id,anchor_epoch,anchor_src,first_cum,last_cum,last_seq,samples,first_seen,last_seen)"
                           " VALUES (?,?,?,?,?,?,?,?,0,?,?)", (node_ref, hw_mac, boot, cand, cand_src, cum, cum, seq, r_at, r_at))
        sid, anchor, src = cur.lastrowid, cand, cand_src
    else:
        sid, anchor, src = row["id"], row["anchor_epoch"], row["anchor_src"]
        if src == "recv" and cand_src == "gw_epoch":
            anchor, src, moved = cand, "gw_epoch", True            # gateway time became available: it wins
        elif src == cand_src and cand < anchor - 0.5:
            anchor, moved = cand, True                              # a lower-latency sample refines the anchor
        if moved:
            conn.execute("UPDATE node_sessions SET anchor_epoch=?, anchor_src=? WHERE id=?", (anchor, src, sid))
            conn.execute("UPDATE flow_samples SET measured_at=strftime('%Y-%m-%d %H:%M:%S', ?+uptime_s, 'unixepoch'), measured_src=?"
                         " WHERE node_ref=? AND session_id=?", (anchor, src, node_ref, sid))
    return datetime.fromtimestamp(anchor + uptime, tz=timezone.utc), src, sid, moved


def touch_session(conn, sid: int, cum: int, seq: int, now: datetime):
    conn.execute("UPDATE node_sessions SET samples=samples+1, last_seen=?, last_cum=CASE WHEN ? >= COALESCE(last_cum,0) THEN ? ELSE last_cum END,"
                 " last_seq=MAX(COALESCE(last_seq,0), ?) WHERE id=?", (fmt_ts(now), cum, cum, seq, sid))


# ------------------------------------------------------------------ flow
def ingest_flow(conn, d, now: datetime | None = None, expected_node: int | None = None, v2: dict | None = None) -> dict:
    """Apply one flow telemetry message.  Returns {'result': 'stored'|'duplicate', 'delta_pulses': int, ...}."""
    now = now or utc_now()
    check_schema(d)
    slot = _int(d, "node", 1, 250 if v2 else 12)
    if expected_node is not None and slot != expected_node:
        raise IngestError(f"payload node {slot} does not match topic node {expected_node}")
    calib_ppl = None
    if v2:                                           # protocol v2: `node` is the registry NUMBER; identity = nodes row + hardware MAC
        node = slot
    elif slot <= 2:
        node = slot                                   # built-in nodes: flow_samples.node_id == radio slot
        reg = conn.execute("SELECT calibration_ppl FROM device_registry WHERE uid=?", (node,)).fetchone()
        calib_ppl = reg["calibration_ppl"] if reg else None
    elif slot == 3:
        raise IngestError("slot 3 is the rainwater node")
    else:
        # dynamically paired device: telemetry is accepted ONLY from a registry row that is paired in this very slot
        uid = _int(d, "uid", 100, 0x7FFFFFFF)
        reg = conn.execute("SELECT uid, slot, pairing_status, calibration_ppl FROM device_registry WHERE uid=?", (uid,)).fetchone()
        if reg is None or reg["pairing_status"] != "paired" or reg["slot"] != slot:
            raise IngestError(f"telemetry from device {uid} in slot {slot} refused: not paired")
        node, calib_ppl = uid, reg["calibration_ppl"]
    boot = _int(d, "boot", 1, U32 - 1)
    seq = _int(d, "seq", 1, U32 - 1)
    cum = _int(d, "cumPulses", 0, U32 - 1)
    interval_pulses = _int(d, "intervalPulses", 0, 1_000_000)
    ppl = _num(d, "pulsesPerLiter", 10.0, 6000.0)
    flow_lpm = _num(d, "flowRate", 0.0, 120.0)
    if calib_ppl:                                     # owner-set calibration overrides what the device reports
        flow_lpm = min(flow_lpm * ppl / calib_ppl, 120.0)
        ppl = calib_ppl
    ppl_x10 = int(round(ppl * 10))
    interval_ms = _int(d, "intervalMs", 1, 3_600_000)
    uptime = _int(d, "uptime", 0, U32 - 1, required=False, default=0)
    status = _int(d, "status", 0, 0xFFFF, required=False, default=0)
    channel = _int(d, "channel", 0, 14, required=False)
    rssi = _int(d, "rssi", -130, 20, required=False)
    recovered = 1 if d.get("recovered") is True else 0
    backlog = 1 if d.get("backlog") is True else 0
    r_at = fmt_ts(now)
    if v2:
        dup = conn.execute("SELECT 1 FROM flow_samples WHERE node_id=? AND boot_id=? AND seq=?", (node, boot, seq)).fetchone()
        if dup:                                      # same reading via the legacy topic or a retransmission: never stored twice
            return {"result": "duplicate"}
        m_dt, msrc, sid, _ = session_measured_at(conn, v2["node_ref"], v2["hw_mac"], boot, d, now, uptime, cum, seq)
        m_at = fmt_ts(m_dt)
    else:
        m_at, msrc, sid = fmt_ts(measured_at(d, now)), "legacy", None

    try:
        cur = conn.execute(
            "INSERT INTO flow_samples (node_id,boot_id,seq,measured_at,received_at,recovered,backlog,cum_pulses,interval_pulses,"
            " delta_pulses,gap_before,pulses_per_liter_x10,flow_lpm,interval_ms,uptime_s,status,channel,rssi,node_ref,hw_mac,session_id,measured_src)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (node, boot, seq, m_at, r_at, recovered, backlog, cum, interval_pulses, 0, 0, ppl_x10, flow_lpm,
             interval_ms, uptime, status, channel, rssi, v2["node_ref"] if v2 else None, v2["hw_mac"] if v2 else None, sid, msrc))
    except Exception as e:  # sqlite3.IntegrityError on the UNIQUE key => retransmission / duplicate MQTT delivery
        if "UNIQUE" in str(e):
            return {"result": "duplicate"}
        raise
    row_id = cur.lastrowid
    if v2:
        touch_session(conn, sid, cum, seq, now)

    prev = conn.execute("SELECT seq, cum_pulses FROM flow_samples WHERE node_id=? AND boot_id=? AND seq<? ORDER BY seq DESC LIMIT 1",
                        (node, boot, seq)).fetchone()
    nxt = conn.execute("SELECT id, seq, cum_pulses FROM flow_samples WHERE node_id=? AND boot_id=? AND seq>? ORDER BY seq ASC LIMIT 1",
                       (node, boot, seq)).fetchone()
    anomaly = 0
    if prev is not None:
        delta = (cum - prev["cum_pulses"]) % U32
        gap = seq - prev["seq"] - 1
    elif seq == 1 or (seq <= FRESH_BOOT_MAX_SEQ and uptime <= FRESH_BOOT_MAX_UPTIME_S and cum <= (uptime + 5) * 400):
        delta = cum % U32                 # node booted moments ago: its counter started at 0, so seq 1.. being lost does not lose volume
        gap = max(seq - 1, 0)
    else:
        delta = interval_pulses           # joined a long-running session: we cannot know how much was used before we looked
        gap = 0
    if delta >= U32 // 2:                 # counter apparently went backwards inside one epoch: corrupt, do not count
        delta, anomaly = 0, 1
    conn.execute("UPDATE flow_samples SET delta_pulses=?, gap_before=?, anomaly=? WHERE id=?", (delta, max(gap, 0), anomaly, row_id))

    if nxt is not None:                   # an out-of-order sample re-splits the following sample's delta
        nd = (nxt["cum_pulses"] - cum) % U32
        ngap = nxt["seq"] - seq - 1
        if nd >= U32 // 2:
            nd = 0
        conn.execute("UPDATE flow_samples SET delta_pulses=?, gap_before=? WHERE id=?", (nd, max(ngap, 0), nxt["id"]))

    if v2:
        pass                              # v2 nodes live in `nodes`; the retired v1 device rows keep their last v1 data (no "online" ghosts)
    elif slot <= 2:
        _touch_device(conn, f"node{node}", "flow", node, f"Flow node {node}", r_at, boot, seq, channel, rssi, uptime, status)
    else:
        _touch_device(conn, f"dev{node}", "flow", node, f"Device {node}", r_at, boot, seq, channel, rssi, uptime, status)
        conn.execute("UPDATE device_registry SET last_seen=? WHERE uid=?", (r_at, node))
    return {"result": "stored", "delta_pulses": delta, "node": node, "boot": boot, "seq": seq, "anomaly": bool(anomaly)}


# ------------------------------------------------------------------ rainwater
def ingest_rain(conn, d, now: datetime | None = None, v2: dict | None = None) -> dict:
    now = now or utc_now()
    check_schema(d)
    _int(d, "node", 1 if v2 else 3, 250 if v2 else 3)
    boot = _int(d, "boot", 1, U32 - 1)
    seq = _int(d, "seq", 1, U32 - 1)
    level_valid = _bool(d, "levelValid")
    level = _num(d, "waterLevel", 0.0, 100.0, required=False) if level_valid else None
    if level_valid and level is None:
        raise IngestError("levelValid=true but waterLevel missing")
    dist = _num(d, "distance", 0.0, 600.0, required=False)
    turb = _int(d, "turbidity", 0, 4095)
    rain = _bool(d, "rain"); clear = _bool(d, "waterClear"); full = _bool(d, "tankFull")
    valve = _bool(d, "valveOpen")
    turb_valid = _bool(d, "turbidityValid", required=False)
    vstate = _int(d, "valveState", 0, 15, required=False, default=0)
    vreason = _int(d, "valveReason", 0, 31, required=False, default=0)
    buzzer = bool(d.get("buzzer")); ff = bool(d.get("firstFlush")); dry = bool(d.get("dryRun"))
    faults = _int(d, "faults", 0, 0xFFFF, required=False, default=0)
    revents = _int(d, "rainEvents", 0, 65535, required=False, default=0)
    uptime = _int(d, "uptime", 0, U32 - 1, required=False, default=0)
    channel = _int(d, "channel", 0, 14, required=False)
    rssi = _int(d, "rssi", -130, 20, required=False)
    m_dt = measured_at(d, now)
    m_at, r_at = fmt_ts(m_dt), fmt_ts(now)
    try:
        conn.execute(
            "INSERT INTO rain_samples (boot_id,seq,measured_at,received_at,recovered,backlog,rain,level_pct,level_valid,distance_cm,"
            " turbidity_adc,turbidity_valid,water_clear,tank_full,valve_open,valve_state,valve_reason,buzzer,first_flush,dry_run,"
            " faults,rain_events,uptime_s,channel,rssi,out_of_range) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (boot, seq, m_at, r_at, 1 if d.get("recovered") is True else 0, 1 if d.get("backlog") is True else 0,
             int(rain), level, int(level_valid), dist, turb, int(bool(turb_valid)) if turb_valid is not None else 1,
             int(clear), int(full), int(valve), vstate, vreason, int(buzzer), int(ff), int(dry), faults, revents,
             uptime, channel, rssi, int(bool(d.get("outOfRange")) and not level_valid)))
    except Exception as e:
        if "UNIQUE" in str(e):
            return {"result": "duplicate"}
        raise
    if not v2:
        _touch_device(conn, "rainwater", "rain", 3, "Rainwater node", r_at, boot, seq, channel, rssi, uptime, faults)
    _update_rain_event(conn, m_dt, bool(rain), level if level_valid else None, bool(valve), not clear)
    return {"result": "stored", "boot": boot, "seq": seq}


RAIN_EVENT_GAP = timedelta(minutes=30)       # rain stopped this long => event ends


def _update_rain_event(conn, t: datetime, rain: bool, level, valve_open: bool, unsafe: bool):
    last = conn.execute("SELECT * FROM rain_events ORDER BY id DESC LIMIT 1").fetchone()
    t_s = fmt_ts(t)
    open_ev = last if (last is not None and last["ended_at"] is None) else None
    if open_ev is not None and t - parse_ts(open_ev["last_rain_at"]) > RAIN_EVENT_GAP:
        conn.execute("UPDATE rain_events SET ended_at=?, end_level_pct=COALESCE(?, end_level_pct) WHERE id=?",
                     (open_ev["last_rain_at"], level, open_ev["id"]))
        open_ev = None
    if rain and open_ev is None:
        conn.execute("INSERT INTO rain_events (started_at,last_rain_at,start_level_pct,end_level_pct,peak_level_pct,samples) VALUES (?,?,?,?,?,1)",
                     (t_s, t_s, level, level, level))
        return
    if open_ev is None:
        return
    last_t = parse_ts(open_ev["last_rain_at"])
    dt = max(0.0, min((t - last_t).total_seconds(), 30.0)) if t >= last_t else 0.0
    peak = open_ev["peak_level_pct"]
    if level is not None and (peak is None or level > peak):
        peak = level
    conn.execute(
        "UPDATE rain_events SET last_rain_at=CASE WHEN ? THEN ? ELSE last_rain_at END, end_level_pct=COALESCE(?, end_level_pct),"
        " peak_level_pct=?, valve_open_seconds=valve_open_seconds+?, unsafe_seconds=unsafe_seconds+?, samples=samples+1 WHERE id=?",
        (1 if (rain and t >= last_t) else 0, t_s, level, peak, dt if valve_open else 0.0, dt if unsafe else 0.0, open_ev["id"]))


# ------------------------------------------------------------------ gateway
def ingest_gateway_heartbeat(conn, d, now: datetime | None = None) -> dict:
    now = now or utc_now()
    check_schema(d)
    gid = str(d.get("gateway", ""))[:40]
    if not gid:
        raise IngestError("missing gateway id")
    wifi = d.get("wifi") or {}
    mqttd = d.get("mqtt") or {}
    queue = d.get("queue") or {}
    rx = d.get("rx") or {}
    r_at = fmt_ts(now)
    ch = _int(wifi, "channel", 0, 14, required=False)
    conn.execute(
        "INSERT INTO gateway_heartbeats (gateway_id,received_at,uptime_s,epoch,wifi_up,wifi_rssi,channel,mqtt_up,free_heap,queue_depth,"
        " queue_overflow,published,wifi_reconnects,mqtt_reconnects,channel_changes,rx_frames,rx_rejected,rx_bad_tag,acks,nacks)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (gid, r_at, _int(d, "uptime", 0, U32, required=False), _int(d, "epoch", 0, 1 << 40, required=False),
         int(bool(wifi.get("up"))), _int(wifi, "rssi", -130, 20, required=False), ch, int(bool(mqttd.get("up"))),
         _int(d, "freeHeap", 0, 1 << 30, required=False), _int(queue, "depth", 0, 1 << 20, required=False),
         _int(queue, "overflow", 0, U32, required=False), _int(mqttd, "published", 0, U32, required=False),
         _int(wifi, "reconnects", 0, U32, required=False), _int(mqttd, "reconnects", 0, U32, required=False),
         _int(wifi, "channelChanges", 0, U32, required=False), _int(rx, "frames", 0, U32, required=False),
         _int(rx, "rejected", 0, U32, required=False), _int(rx, "badTag", 0, U32, required=False),
         _int(rx, "acks", 0, U32, required=False), _int(rx, "nacks", 0, U32, required=False)))
    _touch_device(conn, f"gateway:{gid}", "gateway", None, f"Gateway {gid}", r_at, None, None, ch,
                  _int(wifi, "rssi", -130, 20, required=False), _int(d, "uptime", 0, U32, required=False))
    nodes = d.get("nodes") or []
    if not isinstance(nodes, list):
        raise IngestError("nodes must be a list")
    for n in nodes[:16]:
        if not isinstance(n, dict):
            continue
        slot = _int(n, "node", 1, 12)
        nid = slot
        if slot >= 4:                                   # dynamic slot: map to the paired device uid, ignore anything else
            reg = conn.execute("SELECT uid FROM device_registry WHERE slot=? AND pairing_status='paired'", (slot,)).fetchone()
            if reg is None or _int(n, "uid", 0, 0x7FFFFFFF, required=False, default=0) != reg["uid"]:
                continue
            nid = reg["uid"]
        age = _int(n, "ageS", -1, 10 ** 9, required=False, default=-1)
        conn.execute(
            "INSERT INTO node_comm_health (node_id,received_at,boot_id,received,duplicates,recovered,lost,missing,out_of_order,nacks_sent,"
            " sessions,invalid,node_retransmits,node_dropped,node_rediscoveries,age_s,rssi,node_uptime_s) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (nid, r_at, _int(n, "boot", 0, U32, required=False), _int(n, "received", 0, U32, required=False),
             _int(n, "duplicates", 0, U32, required=False), _int(n, "recovered", 0, U32, required=False),
             _int(n, "lost", 0, U32, required=False), _int(n, "missing", 0, U32, required=False),
             _int(n, "outOfOrder", 0, U32, required=False), _int(n, "nacksSent", 0, U32, required=False),
             _int(n, "sessions", 0, U32, required=False), _int(n, "invalid", 0, U32, required=False),
             _int(n, "nodeRetransmits", 0, 65535, required=False), _int(n, "nodeDropped", 0, 65535, required=False),
             _int(n, "nodeRediscoveries", 0, 65535, required=False), age,
             _int(n, "rssi", -130, 20, required=False), _int(n, "nodeUptime", 0, U32, required=False)))
        key = "rainwater" if nid == 3 else (f"node{nid}" if nid <= 2 else f"dev{nid}")
        kind = "rain" if nid == 3 else "flow"
        # gw_last_seen: when the GATEWAY last heard this node (independent of MQTT queue delay)
        if age >= 0:
            seen = fmt_ts(now - timedelta(seconds=age))
            row = conn.execute("SELECT id FROM devices WHERE device_key=?", (key,)).fetchone()
            if row is None:
                _touch_device(conn, key, kind, nid, f"Node {nid}", r_at)
            conn.execute("UPDATE devices SET gw_last_seen=?, node_retransmits=?, node_dropped=?, node_rediscoveries=? WHERE device_key=?",
                         (seen, _int(n, "nodeRetransmits", 0, 65535, required=False), _int(n, "nodeDropped", 0, 65535, required=False),
                          _int(n, "nodeRediscoveries", 0, 65535, required=False), key))
    # keep history tables bounded (heartbeat rows are small, but never unbounded)
    cutoff = fmt_ts(now - timedelta(days=30))
    conn.execute("DELETE FROM gateway_heartbeats WHERE received_at < ?", (cutoff,))
    conn.execute("DELETE FROM node_comm_health WHERE received_at < ?", (cutoff,))
    return {"result": "stored", "gateway": gid}


def ingest_gateway_status(conn, raw: str, now: datetime | None = None) -> dict:
    now = now or utc_now()
    status = raw.strip().strip('"').strip("'").lower()
    if status not in ("online", "offline"):
        raise IngestError(f"unknown gateway status {raw!r}")
    r_at = fmt_ts(now)
    conn.execute("INSERT INTO gateway_status (gateway_status, received_at) VALUES (?, ?)", (status.upper(), r_at))
    conn.execute("INSERT INTO system_status (gateway_status, received_at) VALUES (?, ?)", (status.upper(), r_at))
    return {"result": "stored", "status": status}
