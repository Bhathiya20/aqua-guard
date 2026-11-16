"""Periodic evaluation (leak lifecycle, alerts) + status/consumption queries.

Everything reads from the database so state survives backend restarts.
"""
import json
from datetime import datetime, timedelta, timezone

from config import Settings
from ingest import fmt_ts, parse_ts, utc_now

# ------------------------------------------------------------------ connectivity
def human_s(sec) -> str:
    """6583 -> '1 h 49 min'; below a minute '45 s'.  Used in every alert text."""
    s = max(0, int(sec))
    if s < 60:
        return f"{s} s"
    d, h, m = s // 86400, (s % 86400) // 3600, (s % 3600) // 60
    if d:
        return f"{d} d {h} h" if h else f"{d} d"
    if h:
        return f"{h} h {m} min" if m else f"{h} h"
    return f"{m} min"


def _age(ts_str, now):
    if not ts_str:
        return None
    try:
        return max(0, int((now - parse_ts(ts_str)).total_seconds()))
    except Exception:
        return None


def gateway_status(conn, cfg: Settings, now=None):
    """Gateway liveness from heartbeats (15 s) + MQTT last-will, NOT from a one-shot status message."""
    now = now or utc_now()
    hb = conn.execute("SELECT * FROM gateway_heartbeats ORDER BY id DESC LIMIT 1").fetchone()
    st = conn.execute("SELECT * FROM gateway_status ORDER BY id DESC LIMIT 1").fetchone()
    hb_age = _age(hb["received_at"] if hb else None, now)
    st_age = _age(st["received_at"] if st else None, now)
    lwt_offline = bool(st and st["gateway_status"].lower() == "offline" and (hb is None or st["received_at"] >= hb["received_at"]))
    online = hb_age is not None and hb_age <= cfg.gateway_offline_s and not lwt_offline
    return {
        "online": online,
        "status": "ONLINE" if online else "OFFLINE",
        "lastHeartbeatAt": hb["received_at"] if hb else None,
        "heartbeatAgeSeconds": hb_age,
        "lastStatusMessageAt": st["received_at"] if st else None,
        "statusAgeSeconds": st_age,
        "lastWillOffline": lwt_offline,
        "gatewayId": hb["gateway_id"] if hb else None,
        "wifiUp": bool(hb["wifi_up"]) if hb else None,
        "wifiRssi": hb["wifi_rssi"] if hb else None,
        "channel": hb["channel"] if hb else None,
        "mqttUp": bool(hb["mqtt_up"]) if hb else None,
        "freeHeap": hb["free_heap"] if hb else None,
        "uptimeSeconds": hb["uptime_s"] if hb else None,
        "queueDepth": hb["queue_depth"] if hb else None,
        "queueOverflow": hb["queue_overflow"] if hb else None,
        "channelChanges": hb["channel_changes"] if hb else None,
        "wifiReconnects": hb["wifi_reconnects"] if hb else None,
        "mqttReconnects": hb["mqtt_reconnects"] if hb else None,
    }


def device_status(conn, cfg: Settings, now=None):
    """Per-node status that distinguishes SENSOR offline from GATEWAY offline."""
    now = now or utc_now()
    gw = gateway_status(conn, cfg, now)
    out = []
    for key, kind, nid, label in (("node1", "flow", 1, "Flow node 1"), ("node2", "flow", 2, "Flow node 2"),
                                  ("rainwater", "rain", 3, "Rainwater node")):
        d = conn.execute("SELECT * FROM devices WHERE device_key=?", (key,)).fetchone()
        comm = conn.execute("SELECT * FROM node_comm_health WHERE node_id=? ORDER BY id DESC LIMIT 1", (nid,)).fetchone()
        last_data = d["last_seen"] if d else None
        if kind == "rain":
            # protocol v2 keeps the legacy `devices` row frozen (by design): the newest rain sample is the real "last data"
            r = conn.execute("SELECT MAX(received_at) AS t FROM rain_samples").fetchone()
            last_data = max([x for x in (last_data, r["t"] if r else None) if x], default=None)
        gw_seen = d["gw_last_seen"] if d else None
        # freshest evidence that the node is alive: backend got data, or the gateway says it heard the node
        freshest = max([x for x in (last_data, gw_seen) if x], default=None)
        age = _age(freshest, now)
        if age is not None and age <= cfg.node_offline_s:
            state = "online"
        elif not gw["online"]:
            state = "unknown"          # cannot tell: the gateway itself is offline
        else:
            state = "offline"          # gateway alive, node silent => SENSOR offline
        out.append({
            "device": key, "node": nid, "kind": kind, "label": label, "state": state,
            "lastSeen": freshest, "lastDataAt": last_data, "ageSeconds": age,
            "bootId": d["last_boot_id"] if d else None, "lastSeq": d["last_seq"] if d else None,
            "sessions": d["sessions"] if d else 0, "uptimeSeconds": d["uptime_s"] if d else None,
            "channel": d["channel"] if d else None, "rssi": d["rssi"] if d else None,
            "firmwareStatus": d["status"] if d else None,
            "nodeRetransmits": d["node_retransmits"] if d else None, "nodeDropped": d["node_dropped"] if d else None,
            "nodeRediscoveries": d["node_rediscoveries"] if d else None,
            "comm": ({
                "received": comm["received"], "duplicates": comm["duplicates"], "recovered": comm["recovered"],
                "lost": comm["lost"], "missing": comm["missing"], "outOfOrder": comm["out_of_order"],
                "nacksSent": comm["nacks_sent"], "invalid": comm["invalid"], "sessions": comm["sessions"],
            } if comm else None),
        })
    return gw, out


# ------------------------------------------------------------------ consumption
def _liters_expr():
    return "SUM(delta_pulses * 10.0 / pulses_per_liter_x10)"


def consumption_total(conn, node: int, since: str | None = None):
    q = f"SELECT COALESCE({_liters_expr()},0) AS l, COUNT(*) AS n, COALESCE(SUM(gap_before),0) AS gaps FROM flow_samples WHERE node_id=? AND anomaly=0"
    args = [node]
    if since:
        q += " AND measured_at >= ?"
        args.append(since)
    r = conn.execute(q, args).fetchone()
    return {"liters": round(r["l"], 3), "samples": r["n"], "missedIntervals": r["gaps"]}


def consumption_series(conn, node: int, period: str, count: int, now=None):
    """Aggregate by day / week / month (UTC).  Volumes derive from cumulative-counter deltas, so
    packets lost in transit still count.  A delta that spans missed packets is attributed to the
    time its sample ARRIVED (documented limitation at bucket boundaries)."""
    now = now or utc_now()
    fmt = {"day": "%Y-%m-%d", "week": "%Y-W%W", "month": "%Y-%m"}[period]
    if period == "day":
        since = now - timedelta(days=count)
    elif period == "week":
        since = now - timedelta(weeks=count)
    else:
        since = now - timedelta(days=31 * count)
    rows = conn.execute(
        f"SELECT strftime('{fmt}', measured_at) AS bucket, {_liters_expr()} AS liters, COUNT(*) AS samples,"
        " COALESCE(SUM(gap_before),0) AS gaps FROM flow_samples WHERE node_id=? AND anomaly=0 AND measured_at>=?"
        " GROUP BY bucket ORDER BY bucket", (node, fmt_ts(since))).fetchall()
    return [{"bucket": r["bucket"], "liters": round(r["liters"] or 0, 3), "samples": r["samples"], "missedIntervals": r["gaps"]} for r in rows]


# ------------------------------------------------------------------ leak state (from the pipeline analysis engine)
NOT_CONFIGURED = "NOT_CONFIGURED"


def leak_state(conn):
    """Overall leak state for the dashboard, derived from the zones of the ACTIVE pipelines (analysis_service writes zone_state / alerts).
    NORMAL | SUSPECTED | CONFIRMED, or NOT_CONFIGURED when no pipeline has a monitored zone yet (never a false 'all clear')."""
    pipes = conn.execute("SELECT id, name FROM pipelines WHERE deleted=0 AND active_version IS NOT NULL").fetchall()
    rows = conn.execute("SELECT z.*, a.message AS amsg, a.id AS aid FROM zone_state z JOIN pipelines p ON p.id=z.pipeline_id AND p.deleted=0 AND p.active_version IS NOT NULL "
                        "LEFT JOIN alerts a ON a.id=z.alert_id AND a.active=1 WHERE z.zone_class!='not_monitored'").fetchall()
    if not pipes:
        return {"state": NOT_CONFIGURED, "since": None, "reason": "No pipeline is configured: design one to enable leak detection.", "eventId": None, "updatedAt": None}
    if not rows:
        return {"state": NOT_CONFIGURED, "since": None, "reason": "No zone has an inflow node yet, so nothing can be monitored. Place a node on a pipe.", "eventId": None, "updatedAt": None}
    rank = {"confirmed": 2, "suspected": 1}
    worst = max(rows, key=lambda r: (rank.get(r["state"], 0), r["unaccounted_lpm"] or 0.0))
    lvl = rank.get(worst["state"], 0)
    if lvl == 0:
        newest = max((r["updated_at"] or "") for r in rows)
        return {"state": "NORMAL", "since": None, "reason": "", "eventId": None, "updatedAt": newest or None}
    return {"state": "CONFIRMED" if lvl == 2 else "SUSPECTED", "since": worst["since"], "reason": worst["amsg"] or "", "eventId": worst["aid"], "updatedAt": worst["updated_at"],
            "zone": worst["zone_id"], "qIn": worst["q_in_lpm"], "qOut": worst["q_out_lpm"], "unaccounted": worst["unaccounted_lpm"], "pipelineId": worst["pipeline_id"]}


FAULT_BITS = ((0x0001, "tank level sensor (ultrasonic) error"), (0x0002, "turbidity sensor error"), (0x0004, "rain sensor error"), (0x0008, "valve relay error"), (0x0010, "sensor timeout"))


def _valve_fault_text(r, faults: int) -> str:
    """Say WHY the valve is held closed, not just that a fault exists."""
    dry = " (dry-run)" if (r is not None and r["dry_run"]) else ""
    if r is not None and r["out_of_range"] and not r["level_valid"]:
        why = "tank level sensor out of range (no tank in front of the ultrasonic sensor)"
    else:
        names = [t for bit, t in FAULT_BITS if faults & bit]
        why = ", ".join(names) if names else "the controller is in its fail-safe state"
    return f"Valve held closed{dry}: {why} [fault code 0x{faults:02X}]"


# ------------------------------------------------------------------ alerts
def _set_alert(conn, key, kind, severity, message, active, now_s, node_ref=None):
    row = conn.execute("SELECT id FROM alerts WHERE alert_key=? AND active=1", (key,)).fetchone()
    if active and row is None:
        conn.execute("INSERT INTO alerts (alert_key,kind,severity,message,active,created_at,node_ref) VALUES (?,?,?,?,1,?,?)", (key, kind, severity, message, now_s, node_ref))
    elif active and row is not None:
        conn.execute("UPDATE alerts SET message=?, severity=? WHERE id=?", (message, severity, row["id"]))
    elif not active and row is not None:
        conn.execute("UPDATE alerts SET active=0, cleared_at=? WHERE id=?", (now_s, row["id"]))


# Alert keys of the retired two-node detector (and, in the v2 world, of the v1 slot alerts).  Nothing raises them any more, so a row that is still
# active is stale and would stay active forever; every evaluation closes them.
RETIRED_KEYS = ("FLOW_SENSOR_FAULT", "LEAK_CONFIRMED", "LEAK_SUSPECTED")
RETIRED_V1_KEYS = ("GATEWAY_OFFLINE", "COMM_DEGRADED")          # exact keys; SENSOR_OFFLINE:<device> is matched by prefix


def _retire_legacy_alerts(conn, v2: bool, now_s: str):
    keys = RETIRED_KEYS + (RETIRED_V1_KEYS if v2 else ())
    conn.execute(f"UPDATE alerts SET active=0, cleared_at=? WHERE active=1 AND (alert_key IN ({','.join('?' * len(keys))})"
                 + (" OR alert_key LIKE 'SENSOR_OFFLINE:%'" if v2 else "") + ")", (now_s, *keys))


def evaluate_alerts(conn, cfg: Settings, now: datetime | None = None):
    now = now or utc_now()
    now_s = fmt_ts(now)
    v2 = conn.execute("SELECT COUNT(*) c FROM gateways").fetchone()["c"] > 0
    _retire_legacy_alerts(conn, v2, now_s)
    if v2:
        _v2_alerts(conn, cfg, now, now_s)                # numbered nodes of the gateway registry; the v1 slot alerts would only be noise
        gw = {"online": True, "heartbeatAgeSeconds": 0, "queueOverflow": 0}
    else:
        gw, devices = device_status(conn, cfg, now)
        _set_alert(conn, "GATEWAY_OFFLINE", "gateway_offline", "critical",
                   "Gateway is offline - no data is reaching the backend" if gw["heartbeatAgeSeconds"] is None else
                   f"Gateway has not sent a heartbeat for {human_s(gw['heartbeatAgeSeconds'])}", not gw["online"] and gw["lastHeartbeatAt"] is not None or (gw["lastWillOffline"]), now_s)
        for d in devices:
            _set_alert(conn, f"SENSOR_OFFLINE:{d['device']}", "sensor_offline", "warning",
                       f"{d['label']} is offline (gateway online, no data for {human_s(d['ageSeconds'])})" if d["ageSeconds"] is not None else f"{d['label']} has never reported",
                       d["state"] == "offline" and (d["lastSeen"] is not None), now_s)
    r = conn.execute("SELECT * FROM rain_samples ORDER BY measured_at DESC, id DESC LIMIT 1").fetchone()
    fresh = False
    if r is not None:
        a = _age(r["received_at"], now)
        fresh = a is not None and a <= cfg.node_offline_s
    _set_alert(conn, "TANK_FULL", "tank_full", "info", "Rainwater tank is full - collection valve closed",
               bool(r and fresh and r["tank_full"]), now_s)
    _set_alert(conn, "UNSAFE_TURBIDITY", "unsafe_turbidity", "warning", "Rainwater turbidity is unsafe - collection stopped",
               bool(r and fresh and r["turbidity_valid"] and not r["water_clear"] and r["rain"]), now_s)
    faults = (r["faults"] or 0) if r else 0
    vfault = bool(r and fresh and (faults != 0 or r["valve_state"] == 6))
    _set_alert(conn, "VALVE_FAULT", "valve_fault", "critical", _valve_fault_text(r, faults), vfault, now_s)

    # communication degradation: loss ratio over the last 10 min (counters are cumulative per boot)
    degraded, msg = False, ""
    for nid in (() if v2 else (1, 2, 3)):
        rows = conn.execute("SELECT * FROM node_comm_health WHERE node_id=? AND received_at>=? ORDER BY id",
                            (nid, fmt_ts(now - timedelta(minutes=10)))).fetchall()
        if len(rows) >= 2 and rows[0]["boot_id"] == rows[-1]["boot_id"]:
            rec = (rows[-1]["received"] or 0) - (rows[0]["received"] or 0)
            lost = (rows[-1]["lost"] or 0) - (rows[0]["lost"] or 0)
            rec_fix = (rows[-1]["recovered"] or 0) - (rows[0]["recovered"] or 0)
            tot = rec + lost
            if tot >= 20 and 100.0 * (lost + rec_fix) / tot >= cfg.comm_loss_alert_pct:
                degraded, msg = True, f"Node {nid}: {lost} lost and {rec_fix} late-recovered of {tot} packets in 10 min"
    if not v2 and gw["queueOverflow"]:
        prev = conn.execute("SELECT queue_overflow FROM gateway_heartbeats WHERE received_at<? ORDER BY id DESC LIMIT 1",
                            (fmt_ts(now - timedelta(minutes=10)),)).fetchone()
        if gw["queueOverflow"] > ((prev["queue_overflow"] or 0) if prev else 0):
            degraded, msg = True, "Gateway MQTT buffer overflowed - telemetry was dropped (volume still reconciled from counters)"
    if not v2:
        _set_alert(conn, "COMM_DEGRADED", "comm_degraded", "warning", msg or "Communication degraded", degraded, now_s)


def _v2_alerts(conn, cfg: Settings, now: datetime, now_s: str):
    """Alerts for the protocol-v2 world: gateway offline, numbered node offline / low battery / missing packets.  One row per active condition (key)."""
    import gateway_v2                                    # local import: gateway_v2 imports ingest, not services
    online_gw = set()
    for gw in conn.execute("SELECT * FROM gateways").fetchall():
        st = gateway_v2.gateway_state(cfg, gw, now)
        if st["state"] == "online":
            online_gw.add(gw["id"])
        off = st["state"] == "offline"
        _set_alert(conn, f"GATEWAY_OFFLINE:{gw['id']}", "gateway_offline", "critical",
                   f"Gateway {gw['gateway_id']} is offline" + ("" if st["heartbeatAgeS"] is None else f" (no heartbeat for {human_s(st['heartbeatAgeS'])})") + " - no data is reaching the backend",
                   off, now_s)
    for n in conn.execute("SELECT * FROM nodes").fetchall():
        label = f"{'Rain' if n['type'] == 'rain' else 'Node'} {n['number']}" + (f" ({n['name']})" if n["name"] else "")
        live = n["state"] == "paired"
        oos = bool(n["out_of_service"])                       # maintenance: no offline / low-battery / missing-packet alerts (they resolve)
        age = _age(n["last_seen"], now)
        node_off = live and not oos and n["gateway_ref"] in online_gw and n["last_seen"] is not None and (age is None or age > cfg.node_offline_s)
        _set_alert(conn, f"NODE_OFFLINE:{n['id']}", "node_offline", "warning",
                   f"{label} is offline (gateway online, no data for {human_s(age)})" if age is not None else f"{label} is offline", node_off, now_s, n["id"])
        low = live and not oos and n["battery_mv"] is not None and cfg.battery_min_plausible_mv <= n["battery_mv"] < cfg.battery_low_mv      # implausible readings are a wiring fault, not "low"
        _set_alert(conn, f"LOW_BATTERY:{n['id']}", "low_battery", "warning",
                   f"{label} battery is low ({(n['battery_mv'] or 0) / 1000:.2f} V, limit {cfg.battery_low_mv / 1000:.2f} V)", low, now_s, n["id"])
        if oos:
            _set_alert(conn, f"MISSING_PACKETS:{n['id']}", "missing_packets", "warning", f"{label}: out of service", False, now_s, n["id"])
        if n["type"] == "flow" and live and not oos:
            r = conn.execute("SELECT COALESCE(SUM(gap_before),0) AS m, COUNT(*) AS g FROM flow_samples WHERE node_ref=? AND received_at>=?",
                             (n["id"], fmt_ts(now - timedelta(minutes=10)))).fetchone()
            tot = r["m"] + r["g"]
            pct = 100.0 * r["m"] / tot if tot else 0.0
            _set_alert(conn, f"MISSING_PACKETS:{n['id']}", "missing_packets", "warning",
                       f"{label}: {r['m']} of {tot} packets missing in the last 10 min ({pct:.0f} %)", tot >= 20 and pct >= cfg.comm_loss_alert_pct, now_s, n["id"])


def _alert_kind(r):
    if r["kind"] == "LEAK":
        return "leak_confirmed" if (r["state"] == "confirmed" or r["confirmed_at"]) else "leak_suspected"
    return r["kind"]


def list_alerts(conn, active_only=False, limit=100):
    q = "SELECT * FROM alerts"
    if active_only:
        q += " WHERE active=1"
    q += " ORDER BY active DESC, id DESC LIMIT ?"
    return [{"id": r["id"], "key": r["alert_key"], "kind": _alert_kind(r), "severity": r["severity"], "message": r["message"],
             "active": bool(r["active"]), "createdAt": r["created_at"], "clearedAt": r["cleared_at"],
             "acknowledgedBy": r["acknowledged_by"], "acknowledgedAt": r["acknowledged_at"],
             "nodeRef": r["node_ref"], "zone": r["zone_id"], "pipelineId": r["pipeline_id"], "pipelineVersion": r["pipeline_version"], "state": r["state"], "demo": bool(r["demo"])}
            for r in conn.execute(q, (limit,))]
