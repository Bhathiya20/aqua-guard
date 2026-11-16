"""Resolved-alert history: list with filters, CSV export, one event with its flow / D chart, and the manual resolve (with a reason).

Every alert row that is no longer active is one history entry.  Leak events carry suspected -> confirmed -> resolved times, the peak D and the
estimated water lost (integral of D over the event, accumulated by the analysis tick); usage events carry the peak flow and the volume that ran
while the alert was open.  `resolved_by` is NULL when the system closed the alert itself ("auto"), otherwise the e-mail of the person.
"""
import csv
import io
import json
from datetime import datetime, timedelta, timezone

from flask import Response, g, jsonify, request

import auth
from database import db_session, get_db
from ingest import fmt_ts, parse_ts, utc_now

REASONS = {"fixed_leak": "Fixed leak", "false_alarm": "False alarm", "test": "Test", "tap_left_open": "Tap left open", "sensor_fault": "Sensor fault", "other": "Other"}
TYPES = {"leak": "Leak", "measurement_inconsistency": "Measurement inconsistency", "usage_prolonged": "Prolonged usage", "usage_continuous": "Continuous abnormal usage",
         "night_flow": "Night flow", "node_offline": "Node offline", "low_battery": "Low battery", "missing_packets": "Missing packets", "gateway_offline": "Gateway offline",
         "tank_full": "Tank full", "unsafe_turbidity": "Unsafe turbidity", "valve_fault": "Valve fault", "comm_degraded": "Communication degraded"}


def _type(r) -> str:
    return "leak" if r["kind"] == "LEAK" else r["kind"]


def _zone_names(conn, cache, pid, version) -> dict:
    """{zone id: real zone name} of the pipeline version that was active when the alert was raised."""
    key = (pid, version)
    if key not in cache:
        names = {}
        try:
            from analysis.zones import partition
            from pipeline.model import parse
            row = conn.execute("SELECT doc FROM pipeline_versions WHERE pipeline_id=? AND version=?", (pid, version)).fetchone()
            if row:
                names = {z.id: z.name for z in partition(parse(json.loads(row["doc"]))).zones}
        except Exception:                                      # noqa: BLE001  an old / broken version only costs the pretty name
            names = {}
        cache[key] = names
    return cache[key]


def _entry(conn, cache, r) -> dict:
    node = None
    if r["node_ref"]:
        n = conn.execute("SELECT id, number, name, type FROM nodes WHERE id=?", (r["node_ref"],)).fetchone()
        if n:
            node = {"ref": n["id"], "number": n["number"], "type": n["type"], "name": n["name"] or "", "label": f"{'Rain' if n['type'] == 'rain' else 'Node'} {n['number']}" + (f" ({n['name']})" if n["name"] else "")}
    zone = None
    if r["zone_id"]:
        zone = {"id": r["zone_id"], "name": _zone_names(conn, cache, r["pipeline_id"], r["pipeline_version"]).get(r["zone_id"], r["zone_id"])}
    started = r["created_at"]
    ended = r["cleared_at"]
    dur = None
    try:
        dur = int((parse_ts(ended) - parse_ts(started)).total_seconds()) if ended else None
    except Exception:                                          # noqa: BLE001
        dur = None
    typ = _type(r)
    peak = r["peak_unaccounted_lpm"] if typ == "leak" else r["peak_flow_lpm"]
    return {"id": r["id"], "type": typ, "typeLabel": TYPES.get(typ, typ.replace("_", " ").capitalize()), "severity": r["severity"], "message": r["message"], "node": node, "zone": zone,
            "pipelineId": r["pipeline_id"], "startedAt": started, "suspectedAt": r["suspected_at"] or (started if typ in ("leak", "measurement_inconsistency") else None),
            "confirmedAt": r["confirmed_at"], "resolvedAt": ended, "durationS": dur, "peakLpm": None if peak is None else round(peak, 3),
            "peakKind": "D" if typ == "leak" else "flow", "lostL": None if r["lost_l"] is None else round(r["lost_l"], 2),
            "resolvedBy": "auto" if not r["resolved_by"] else r["resolved_by"], "reason": r["resolve_reason"], "reasonLabel": REASONS.get(r["resolve_reason"] or "", None), "note": r["resolve_note"] or "",
            "demo": bool(r["demo"])}


def _filters(args):
    where, vals = ["active=0"], []
    if args.get("from"):
        where.append("COALESCE(cleared_at, created_at) >= ?"); vals.append(args["from"][:10] + " 00:00:00")
    if args.get("to"):
        where.append("created_at <= ?"); vals.append(args["to"][:10] + " 23:59:59")
    if args.get("node"):
        where.append("node_ref=?"); vals.append(int(args["node"]))
    if args.get("zone"):
        where.append("zone_id=?"); vals.append(args["zone"])
    if args.get("type"):
        t = args["type"]
        where.append("kind=?" if t != "leak" else "kind='LEAK'")
        if t != "leak":
            vals.append(t)
    if args.get("reason"):
        if args["reason"] == "auto":
            where.append("resolved_by IS NULL")
        else:
            where.append("resolve_reason=?"); vals.append(args["reason"])
    return " AND ".join(where), vals


def history_rows(conn, args, limit=500):
    w, vals = _filters(args)
    rows = conn.execute(f"SELECT * FROM alerts WHERE {w} ORDER BY COALESCE(cleared_at, created_at) DESC, id DESC LIMIT ?", (*vals, limit)).fetchall()
    cache: dict = {}
    return [_entry(conn, cache, r) for r in rows]


def series_for(conn, r) -> dict:
    """The chart of one event: zone balance (Q in, Q out, D) for leaks, the node's own flow for usage alerts; a few minutes of context on each side."""
    t0 = parse_ts(r["created_at"]) - timedelta(minutes=5)
    t1 = (parse_ts(r["cleared_at"]) if r["cleared_at"] else utc_now()) + timedelta(minutes=5)
    if _type(r) in ("leak", "measurement_inconsistency") and r["zone_id"]:
        rows = conn.execute("SELECT bucket_ts, q_in_l, q_out_l, unaccounted_l, complete FROM zone_buckets WHERE pipeline_id=? AND zone_id=? AND bucket_ts>=? AND bucket_ts<=? ORDER BY bucket_ts",
                            (r["pipeline_id"], r["zone_id"], int(t0.timestamp()), int(t1.timestamp()))).fetchall()
        pts = [{"t": x["bucket_ts"], "qIn": round(x["q_in_l"], 3), "qOut": round(x["q_out_l"], 3), "d": round(x["unaccounted_l"], 3)} for x in rows if x["complete"] and x["q_in_l"] is not None]
        return {"kind": "balance", "unit": "L/min (per 1 min bucket)", "points": pts}
    if r["node_ref"]:
        rows = conn.execute("SELECT measured_at, flow_lpm FROM flow_samples WHERE node_ref=? AND anomaly=0 AND measured_at>=? AND measured_at<=? ORDER BY measured_at LIMIT 20000",
                            (r["node_ref"], fmt_ts(t0), fmt_ts(t1))).fetchall()
        step = max(1, len(rows) // 400)
        pts = [{"t": int(parse_ts(x["measured_at"]).timestamp()), "flow": round(x["flow_lpm"] or 0.0, 3)} for x in rows[::step]]
        return {"kind": "flow", "unit": "L/min", "points": pts}
    return {"kind": "none", "unit": "", "points": []}


def register(app, cfg):
    @app.get("/api/alerts/history")
    def alerts_history():
        limit = min(max(request.args.get("limit", 300, type=int), 1), 2000)
        conn = get_db()
        try:
            return jsonify(history=history_rows(conn, request.args, limit), reasons=REASONS, types=TYPES)
        finally:
            conn.close()

    @app.get("/api/alerts/history.csv")
    def alerts_history_csv():
        conn = get_db()
        try:
            rows = history_rows(conn, request.args, 5000)
        finally:
            conn.close()
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["id", "type", "severity", "node", "zone", "started", "suspected", "confirmed", "resolved", "duration_s", "peak_lpm", "peak_is", "water_lost_l", "resolved_by", "reason", "note", "demo", "message"])
        for e in rows:
            w.writerow([e["id"], e["typeLabel"], e["severity"], (e["node"] or {}).get("label", ""), (e["zone"] or {}).get("name", ""), e["startedAt"], e["suspectedAt"] or "", e["confirmedAt"] or "",
                        e["resolvedAt"] or "", "" if e["durationS"] is None else e["durationS"], "" if e["peakLpm"] is None else e["peakLpm"], e["peakKind"], "" if e["lostL"] is None else e["lostL"],
                        e["resolvedBy"], e["reasonLabel"] or "", e["note"], "yes" if e["demo"] else "", e["message"]])
        return Response("﻿" + out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=aqua-guard-history-{utc_now():%Y%m%d}.csv"})

    @app.get("/api/alerts/<int:alert_id>/detail")
    def alert_detail(alert_id):
        conn = get_db()
        try:
            r = conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
            if r is None:
                return jsonify(error="Alert not found"), 404
            return jsonify(event=_entry(conn, {}, r), series=series_for(conn, r))
        finally:
            conn.close()

    @app.post("/api/alerts/<int:alert_id>/resolve")
    @auth.require_role("admin")
    def resolve_alert(alert_id):
        """Close an alert by hand.  A reason is required (it is what the history shows).  A condition that still holds raises a NEW alert on the next evaluation."""
        body = request.get_json(silent=True) or {}
        reason = str(body.get("reason") or "")
        note = str(body.get("note") or "").strip()[:300]
        if reason not in REASONS:
            return jsonify(error="Choose a reason: " + ", ".join(REASONS.values()) + "."), 400
        with db_session() as conn:
            r = conn.execute("SELECT id, kind, active, alert_key FROM alerts WHERE id=?", (alert_id,)).fetchone()
            if r is None:
                return jsonify(error="Alert not found"), 404
            if r["active"]:
                now_s = fmt_ts(utc_now())
                conn.execute("UPDATE alerts SET active=0, cleared_at=?, state='resolved', updated_at=?, resolved_by=?, resolve_reason=?, resolve_note=?,"
                             " acknowledged_by=COALESCE(acknowledged_by,?), acknowledged_at=COALESCE(acknowledged_at,?) WHERE id=?",
                             (now_s, now_s, g.user["email"], reason, note, g.user["email"], now_s, alert_id))
                conn.execute("UPDATE zone_state SET alert_id=NULL WHERE alert_id=?", (alert_id,))
                auth.audit(conn, "alert_resolve", json.dumps({"id": alert_id, "reason": reason}), user=g.user)
        return jsonify(ok=True)
