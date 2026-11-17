"""Aqua Guard REST API (Flask).

All /api routes except /api/health and /api/login require an authenticated session cookie;
state-changing requests also need the X-CSRF-Token header; admin routes check the role on the server.
"""
import time
from datetime import timedelta

from flask import Flask, g, jsonify, request
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash

import auth
import database
import mqtt_service
import analysis_service
import calibration_api
import nodes_api
import pipeline_api
import history_api
import usage_service
import clock_service
import registry_api
import services
from config import Settings, ensure_secret_key, load_settings
from database import db_session, init_db
from ingest import fmt_ts, parse_ts, utc_now

DUMMY_HASH = generate_password_hash("not-a-real-password")
PRIVATE_LAN_ORIGIN = r"http://(10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)(:\d+)?"


def create_app(settings: Settings | None = None, start_background: bool = True) -> Flask:
    cfg = settings or load_settings()
    if not cfg.secret_key:
        ensure_secret_key(cfg)
    database.set_db_path(cfg.db_path)
    generated = init_db(cfg.default_admin_email, cfg.default_admin_password)
    if generated:
        print("=" * 72)
        print(f"[SETUP] First administrator created: {cfg.default_admin_email}")
        print(f"[SETUP] One-time password: {generated}   (you must change it at first login)")
        print("=" * 72)

    app = Flask(__name__)
    app.config["AQ"] = cfg
    app.config["SECRET_KEY"] = cfg.secret_key or "unused-sessions-are-server-side"
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

    origins = list(cfg.cors_origins)
    import os
    if os.environ.get("AQUAGUARD_CORS_ALLOW_PRIVATE_LAN", "").lower() in ("1", "true", "yes"):
        origins.append(PRIVATE_LAN_ORIGIN)
    CORS(app, resources={r"/api/*": {"origins": origins}}, supports_credentials=True,
         allow_headers=["Content-Type", "X-CSRF-Token"], methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])

    app.before_request(auth.before_request)

    @app.after_request
    def security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(404)
    def _404(e):
        return jsonify(error="Not found"), 404

    @app.errorhandler(413)
    def _413(e):
        return jsonify(error="Request too large"), 413

    @app.errorhandler(Exception)
    def _500(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return jsonify(error=e.description), e.code
        app.logger.exception("Unhandled error")
        return jsonify(error="Internal server error"), 500

    # ------------------------------------------------------------ helpers
    def age_of(ts):
        if not ts:
            return None
        return max(0, int((utc_now() - parse_ts(ts)).total_seconds()))

    def fmt_flow(r):
        if r is None:
            return None
        age = age_of(r["received_at"])
        ppl = r["pulses_per_liter_x10"] / 10.0
        return {
            "id": r["id"], "node": r["node_id"], "bootId": r["boot_id"], "seq": r["seq"],
            "flowRate": r["flow_lpm"], "totalLiters": round(r["cum_pulses"] / ppl, 3),
            "intervalPulses": r["interval_pulses"], "cumPulses": r["cum_pulses"], "deltaPulses": r["delta_pulses"],
            "pulsesPerLiter": ppl, "uptime": r["uptime_s"], "status": r["status"],
            "recovered": bool(r["recovered"]), "backlog": bool(r["backlog"]), "gapBefore": r["gap_before"],
            "channel": r["channel"], "rssi": r["rssi"],
            "measuredAt": r["measured_at"], "receivedAt": r["received_at"], "ageSeconds": age,
            "active": age is not None and age <= cfg.node_offline_s,
        }

    def latest_flow(conn, node):
        d = conn.execute("SELECT last_boot_id FROM devices WHERE device_key=?", (f"node{node}",)).fetchone()
        r = None
        if d and d["last_boot_id"]:
            r = conn.execute("SELECT * FROM flow_samples WHERE node_id=? AND boot_id=? ORDER BY seq DESC LIMIT 1", (node, d["last_boot_id"])).fetchone()
        return r or conn.execute("SELECT * FROM flow_samples WHERE node_id=? ORDER BY id DESC LIMIT 1", (node,)).fetchone()

    def fmt_rain(r):
        if r is None:
            return None
        age = age_of(r["received_at"])
        return {
            "id": r["id"], "bootId": r["boot_id"], "seq": r["seq"], "rain": bool(r["rain"]),
            "waterLevel": r["level_pct"] if r["level_valid"] else None, "levelValid": bool(r["level_valid"]), "outOfRange": bool(r["out_of_range"]) and not r["level_valid"],
            "distance": r["distance_cm"] if r["level_valid"] else None,
            "turbidity": r["turbidity_adc"], "turbidityValid": bool(r["turbidity_valid"]),
            "waterClear": bool(r["water_clear"]), "tankFull": bool(r["tank_full"]), "valveOpen": bool(r["valve_open"]),
            "valveState": r["valve_state"], "valveReason": r["valve_reason"], "firstFlush": bool(r["first_flush"]),
            "buzzer": bool(r["buzzer"]), "dryRun": bool(r["dry_run"]), "faults": r["faults"], "rainEvents": r["rain_events"],
            "uptime": r["uptime_s"], "recovered": bool(r["recovered"]), "channel": r["channel"], "rssi": r["rssi"],
            "measuredAt": r["measured_at"], "receivedAt": r["received_at"], "ageSeconds": age,
            "active": age is not None and age <= cfg.node_offline_s,
        }

    def latest_rain(conn):
        # The boot of the most recently RECEIVED sample, then its highest seq (a recovered old packet must not move us back).
        # Not taken from devices.last_boot_id: v2 ingest keeps no 'rainwater' device row, so that value goes stale (it showed 1-day-old data).
        last = conn.execute("SELECT boot_id FROM rain_samples ORDER BY id DESC LIMIT 1").fetchone()
        if last is None:
            return None
        return conn.execute("SELECT * FROM rain_samples WHERE boot_id=? ORDER BY seq DESC LIMIT 1", (last["boot_id"],)).fetchone()

    LEGACY_LEAK_STATUS = {"NORMAL": "normal", "RESOLVED": "normal", "SUSPECTED": "warning", "CONFIRMED": "leak", "SENSOR_FAULT": "unavailable", "NOT_CONFIGURED": "unavailable"}

    def fmt_leak_event(r):
        if r is None:
            return None
        state = r["state"] or ("CONFIRMED" if r["status"] == "active" else "RESOLVED")
        return {
            "id": f"LEAK-{r['id']:04d}", "dbId": r["id"], "node1Flow": r["node1_flow"], "node2Flow": r["node2_flow"],
            "flowDifference": r["flow_difference"], "differencePercentage": r["difference_percentage"],
            "severity": r["severity"], "status": r["status"], "state": state,
            "detectedAt": r["detected_at"], "suspectedAt": r["suspected_at"], "confirmedAt": r["confirmed_at"],
            "resolvedAt": r["resolved_at"], "peakDifference": r["peak_difference"], "peakPercentage": r["peak_percentage"],
        }

    def fmt_leak_alert(r):
        """A leak EVENT of the pipeline analysis (one alerts row per event) in the shape the dashboard already renders."""
        st = r["state"] or "suspected"
        un = r["unaccounted_lpm"] if r["unaccounted_lpm"] is not None else 0.0
        peak = r["peak_unaccounted_lpm"] if r["peak_unaccounted_lpm"] is not None else un
        qin = r["q_in_lpm"] if r["q_in_lpm"] is not None else 0.0
        pct = round(un / qin * 100.0, 1) if qin > 0.1 else 0.0
        return {"id": f"LEAK-A{r['id']:04d}", "dbId": r["id"], "zone": r["zone_id"], "pipelineId": r["pipeline_id"], "pipelineVersion": r["pipeline_version"], "message": r["message"],
                "node1Flow": qin, "node2Flow": r["q_out_lpm"] if r["q_out_lpm"] is not None else 0.0, "flowDifference": un, "differencePercentage": pct,
                "severity": r["severity"] if r["severity"] in ("warning", "critical") else "warning",
                "status": {"suspected": "suspected", "confirmed": "active", "resolved": "resolved"}.get(st, "active"), "state": st.upper(),
                "detectedAt": r["created_at"], "suspectedAt": r["created_at"], "confirmedAt": r["confirmed_at"], "resolvedAt": r["cleared_at"],
                "peakDifference": peak, "peakPercentage": round(peak / qin * 100.0, 1) if qin > 0.1 else None}

    def fmt_report(r):
        if r is None:
            return None
        return {"id": r["report_code"], "dbId": r["id"], "building": r["building"], "location": r["location"],
                "description": r["description"], "status": r["status"],
                "date": r["created_at"][:10] if r["created_at"] else "", "reportedBy": r["reported_by"]}

    def user_json(u):
        return {"id": u["id"], "email": u["email"], "firstName": u["first_name"], "lastName": u["last_name"],
                "phone": u["phone"] or "", "address": u["address"] or "", "role": u["role"]}

    def read():
        return database.get_db()

    # ------------------------------------------------------------ general / auth
    @app.get("/api/health")
    def health():
        return jsonify(ok=True, database="SQLite", mqtt=mqtt_service.state["connected"])

    @app.post("/api/login")
    def login():
        data = request.get_json(silent=True) or {}
        email = str(data.get("email", "")).strip()[:254]
        password = str(data.get("password", ""))[:256]
        if not email or not password:
            return jsonify(error="Email and password are required"), 400
        now = time.time()
        with db_session() as conn:
            u = conn.execute("SELECT * FROM users WHERE lower(email)=lower(?)", (email,)).fetchone()
            if u is not None and (u["locked_until"] or 0) > now:
                auth.audit(conn, "login_locked", "", email=email)
                return jsonify(error="Account temporarily locked. Try again later.", retryAfterSeconds=int(u["locked_until"] - now)), 429
            ok = check_password_hash(u["password_hash"] if u is not None else DUMMY_HASH, password)
            if u is None or not ok or not u["active"]:
                if u is not None:
                    fails = (u["failed_logins"] or 0) + 1
                    lock = now + cfg.login_lock_minutes * 60 if fails >= cfg.login_max_failures else None
                    conn.execute("UPDATE users SET failed_logins=?, locked_until=? WHERE id=?", (0 if lock else fails, lock, u["id"]))
                auth.audit(conn, "login_failed", "", email=email)
                return jsonify(error="Invalid email or password"), 401
            conn.execute("UPDATE users SET failed_logins=0, locked_until=NULL WHERE id=?", (u["id"],))
            token, csrf, exp = auth.create_session(conn, u["id"])
            auth.audit(conn, "login", "", user=u)
        resp = jsonify(user=user_json(u), csrfToken=csrf, mustChangePassword=bool(u["must_change_password"]))
        auth.set_cookie(resp, token, exp)
        return resp

    @app.post("/api/logout")
    def logout():
        with db_session() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (g.token_hash,))
            auth.audit(conn, "logout", "", user=g.user)
        resp = jsonify(ok=True)
        auth.clear_cookie(resp)
        return resp

    @app.get("/api/me")
    def me():
        u = g.user
        return jsonify(user={k: u[k] for k in ("id", "email", "role", "firstName", "lastName", "phone", "address")},
                       csrfToken=g.csrf, mustChangePassword=u["mustChangePassword"],
                       sessionIdleMinutes=cfg.session_idle_minutes)

    @app.post("/api/change-password")
    def change_password():
        data = request.get_json(silent=True) or {}
        cur, new = str(data.get("currentPassword", "")), str(data.get("newPassword", ""))
        problem = auth.password_problem(new, cfg.min_password_length)
        if problem:
            return jsonify(error=problem), 400
        with db_session() as conn:
            u = conn.execute("SELECT * FROM users WHERE id=?", (g.user["id"],)).fetchone()
            if not check_password_hash(u["password_hash"], cur):
                auth.audit(conn, "password_change_failed", "", user=u)
                return jsonify(error="Current password is incorrect"), 400
            if cur == new:
                return jsonify(error="New password must differ from the current one"), 400
            conn.execute("UPDATE users SET password_hash=?, must_change_password=0, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                         (generate_password_hash(new), u["id"]))
            conn.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (u["id"], g.token_hash))   # revoke other sessions
            auth.audit(conn, "password_changed", "", user=u)
        return jsonify(ok=True)

    # ------------------------------------------------------------ system / devices
    @app.get("/api/system/status")
    def system_status():
        conn = read()
        try:
            gw, devs = services.device_status(conn, cfg)
        finally:
            conn.close()
        n = {d["device"]: d for d in devs}
        return jsonify(
            gatewayStatus=gw["status"], gatewayLastReceived=gw["lastHeartbeatAt"], gatewayAgeSeconds=gw["heartbeatAgeSeconds"],
            gatewayActive=gw["online"], gateway=gw, devices=devs,
            node1LastReceived=n["node1"]["lastSeen"], node2LastReceived=n["node2"]["lastSeen"],
            rainwaterLastReceived=n["rainwater"]["lastSeen"], mqtt=mqtt_service.state)

    @app.get("/api/devices")
    def devices():
        conn = read()
        try:
            gw, devs = services.device_status(conn, cfg)
        finally:
            conn.close()
        return jsonify(gateway=gw, devices=devs)

    @app.get("/api/comm/history")
    def comm_history():
        node = request.args.get("node", type=int)
        limit = min(max(request.args.get("limit", 120, type=int), 1), 2000)
        conn = read()
        try:
            if node in (1, 2, 3):
                rows = conn.execute("SELECT * FROM node_comm_health WHERE node_id=? ORDER BY id DESC LIMIT ?", (node, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM node_comm_health ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        finally:
            conn.close()
        return jsonify([{k: r[k] for k in r.keys()} for r in reversed(rows)])

    @app.get("/api/overview")
    def overview():
        conn = read()
        try:
            now = utc_now()
            gw, devs = services.device_status(conn, cfg, now)
            n1, n2, rain = latest_flow(conn, 1), latest_flow(conn, 2), latest_rain(conn)
            today = fmt_ts(now.replace(hour=0, minute=0, second=0, microsecond=0))
            week = fmt_ts(now - timedelta(days=7))
            month = fmt_ts(now - timedelta(days=30))
            a = cfg.authoritative_node
            alerts = services.list_alerts(conn, active_only=True)
            return jsonify(
                consumption={"authoritativeNode": a, "allTime": services.consumption_total(conn, a),
                             "today": services.consumption_total(conn, a, today),
                             "last7Days": services.consumption_total(conn, a, week),
                             "last30Days": services.consumption_total(conn, a, month)},
                node1=fmt_flow(n1), node2=fmt_flow(n2), rainwater=fmt_rain(rain),
                leak=services.leak_state(conn), gateway=gw, devices=devs, alerts=alerts)
        finally:
            conn.close()

    # ------------------------------------------------------------ module 1: water
    @app.get("/api/water/live")
    def water_live():
        conn = read()
        try:
            gw = services.gateway_status(conn, cfg)
            return jsonify(node1=fmt_flow(latest_flow(conn, 1)), node2=fmt_flow(latest_flow(conn, 2)),
                           gateway={"status": gw["status"], "receivedAt": gw["lastHeartbeatAt"],
                                    "ageSeconds": gw["heartbeatAgeSeconds"], "active": gw["online"]})
        finally:
            conn.close()

    @app.get("/api/water/history")
    @app.get("/api/flow/history")
    def water_history():
        limit = min(max(request.args.get("limit", 100, type=int), 1), 2000)
        node = request.args.get("node", type=int)
        conn = read()
        try:
            if node in (1, 2):
                rows = conn.execute("SELECT * FROM flow_samples WHERE node_id=? ORDER BY measured_at DESC, id DESC LIMIT ?", (node, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM flow_samples ORDER BY measured_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
        finally:
            conn.close()
        return jsonify([fmt_flow(r) for r in reversed(rows)])

    @app.get("/api/water/summary")
    def water_summary():
        conn = read()
        try:
            now = utc_now()
            a = cfg.authoritative_node
            today = fmt_ts(now.replace(hour=0, minute=0, second=0, microsecond=0))
            t1, t2 = services.consumption_total(conn, 1), services.consumption_total(conn, 2)
            f1, f2 = latest_flow(conn, 1), latest_flow(conn, 2)
            count = conn.execute("SELECT COUNT(*) c FROM flow_samples").fetchone()["c"]
            return jsonify(
                authoritativeNode=a,
                authoritativeVolume=(t1 if a == 1 else t2)["liters"],
                node1TotalLiters=t1["liters"], node2TotalLiters=t2["liters"],
                node1CurrentFlow=f1["flow_lpm"] if f1 else None, node2CurrentFlow=f2["flow_lpm"] if f2 else None,
                todayLiters=services.consumption_total(conn, a, today)["liters"],
                last7DaysLiters=services.consumption_total(conn, a, fmt_ts(now - timedelta(days=7)))["liters"],
                last30DaysLiters=services.consumption_total(conn, a, fmt_ts(now - timedelta(days=30)))["liters"],
                missedIntervals=t1["missedIntervals"], totalMeasurements=count)
        finally:
            conn.close()

    @app.get("/api/water/consumption")
    def water_consumption():
        period = request.args.get("period", "day")
        if period not in ("day", "week", "month"):
            return jsonify(error="period must be day, week or month"), 400
        count = min(max(request.args.get("count", 14, type=int), 1), 120)
        node = request.args.get("node", cfg.authoritative_node, type=int)
        if node not in (1, 2):
            return jsonify(error="node must be 1 or 2"), 400
        conn = read()
        try:
            return jsonify(period=period, node=node, series=services.consumption_series(conn, node, period, count))
        finally:
            conn.close()

    # ------------------------------------------------------------ module 2: leaks
    @app.get("/api/leaks/live")
    def leaks_live():
        conn = read()
        try:
            st = services.leak_state(conn)
            ev = conn.execute("SELECT * FROM alerts WHERE id=? AND kind='LEAK'", (st["eventId"],)).fetchone() if st["eventId"] else None
            # the figures shown are the balance of the zone that decides the state: Q_in, sum of Q_out, unaccounted (L/min)
            zrow = None
            if st["state"] not in (services.NOT_CONFIGURED,):
                zrow = conn.execute("SELECT z.* FROM zone_state z JOIN pipelines p ON p.id=z.pipeline_id AND p.deleted=0 AND p.active_version IS NOT NULL "
                                    "WHERE z.zone_class!='not_monitored' AND z.q_in_lpm IS NOT NULL ORDER BY (z.state='confirmed') DESC, (z.state='suspected') DESC, z.updated_at DESC LIMIT 1").fetchone()
            q_in = zrow["q_in_lpm"] if zrow else None
            q_out = zrow["q_out_lpm"] if zrow else None
            diff = zrow["unaccounted_lpm"] if zrow else None
            pct = (round(diff / q_in * 100.0, 1) if (diff is not None and q_in and q_in > 0.1) else (0.0 if diff is not None else None))
            p = analysis_service.params_from_env()
            return jsonify(
                node1Flow=q_in, node2Flow=q_out, flowDifference=diff, differencePercentage=pct,
                state=st["state"], stateSince=st["since"], reason=st["reason"],
                status=LEGACY_LEAK_STATUS.get(st["state"], "normal"),
                severity={"SUSPECTED": "warning", "CONFIRMED": (ev["severity"] if ev else "critical")}.get(st["state"], "none" if st["state"] == services.NOT_CONFIGURED else "normal"),
                node1Active=zrow is not None, node2Active=zrow is not None and q_out is not None, lastUpdated=zrow["updated_at"] if zrow else None,
                activeEvent=fmt_leak_alert(ev) if ev else None,
                zone=st.get("zone"), pipelineId=st.get("pipelineId"),
                thresholds={"absoluteLpm": p.abs_min_lpm, "relativePct": round(p.tolerance * 100, 1),
                            "suspectAfterS": int(p.suspect_min * 60), "confirmAfterS": int(p.confirm_min * 60)})
        finally:
            conn.close()

    @app.get("/api/leaks/events")
    def leak_events():
        limit = min(max(request.args.get("limit", 50, type=int), 1), 500)
        conn = read()
        try:
            new = [fmt_leak_alert(r) for r in conn.execute("SELECT * FROM alerts WHERE kind='LEAK' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
            old = [fmt_leak_event(r) for r in conn.execute("SELECT * FROM leak_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]   # v1 two-node history stays readable
        finally:
            conn.close()
        return jsonify(sorted(new + old, key=lambda e: e["detectedAt"] or "", reverse=True)[:limit])

    @app.get("/api/leaks/summary")
    def leak_summary():
        now = utc_now()
        conn = read()
        try:
            c = lambda q, *a: conn.execute(q, a).fetchone()[0]
            day, week = now.strftime("%Y-%m-%d"), fmt_ts(now - timedelta(days=7))
            return jsonify(
                activeLeaks=c("SELECT COUNT(*) FROM alerts WHERE kind='LEAK' AND active=1 AND state='confirmed'") + c("SELECT COUNT(*) FROM leak_events WHERE status='active'"),
                suspectedLeaks=c("SELECT COUNT(*) FROM alerts WHERE kind='LEAK' AND active=1 AND state='suspected'") + c("SELECT COUNT(*) FROM leak_events WHERE status='suspected'"),
                eventsToday=c("SELECT COUNT(*) FROM alerts WHERE kind='LEAK' AND created_at>=?", day) + c("SELECT COUNT(*) FROM leak_events WHERE detected_at>=? AND status<>'dismissed'", day),
                eventsThisWeek=c("SELECT COUNT(*) FROM alerts WHERE kind='LEAK' AND created_at>=?", week) + c("SELECT COUNT(*) FROM leak_events WHERE detected_at>=? AND status<>'dismissed'", week),
                resolvedEvents=c("SELECT COUNT(*) FROM alerts WHERE kind='LEAK' AND state='resolved'") + c("SELECT COUNT(*) FROM leak_events WHERE status='resolved'"),
                state=services.leak_state(conn)["state"])
        finally:
            conn.close()

    # ------------------------------------------------------------ module 3: rainwater
    @app.get("/api/rainwater/live")
    def rainwater_live():
        conn = read()
        try:
            return jsonify(rainwater=fmt_rain(latest_rain(conn)))
        finally:
            conn.close()

    @app.get("/api/rainwater/history")
    def rainwater_history():
        limit = min(max(request.args.get("limit", 100, type=int), 1), 2000)
        conn = read()
        try:
            rows = conn.execute("SELECT * FROM rain_samples ORDER BY measured_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
        finally:
            conn.close()
        return jsonify([fmt_rain(r) for r in reversed(rows)])

    @app.get("/api/rainwater/summary")
    def rainwater_summary():
        conn = read()
        try:
            r = latest_rain(conn)
            count = conn.execute("SELECT COUNT(*) c FROM rain_samples").fetchone()["c"]
            ev = conn.execute("SELECT COUNT(*) c FROM rain_events").fetchone()["c"]
        finally:
            conn.close()
        valid = bool(r and r["level_valid"])
        return jsonify(
            isRaining=bool(r["rain"]) if r else False,
            tankFillPercentage=r["level_pct"] if valid else None,
            ultrasonicDistance=r["distance_cm"] if valid else None, ultrasonicValid=valid,
            turbidityAdc=r["turbidity_adc"] if r else None, waterClear=bool(r["water_clear"]) if r else None,
            turbidityValid=bool(r["turbidity_valid"]) if r else None,
            valveOpen=bool(r["valve_open"]) if r else False, valveState=r["valve_state"] if r else None,
            outOfRange=bool(r["out_of_range"]) and not valid if r else False,
            valveReason=r["valve_reason"] if r else None, tankFull=bool(r["tank_full"]) if r else None,
            dryRun=bool(r["dry_run"]) if r else None, faults=r["faults"] if r else None,
            totalMeasurements=count, rainEventCount=ev)

    @app.get("/api/rainwater/events")
    def rainwater_events():
        limit = min(max(request.args.get("limit", 50, type=int), 1), 500)
        conn = read()
        try:
            rows = conn.execute("SELECT * FROM rain_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        finally:
            conn.close()
        return jsonify([{"id": r["id"], "startedAt": r["started_at"], "lastRainAt": r["last_rain_at"], "endedAt": r["ended_at"],
                         "startLevelPct": r["start_level_pct"], "endLevelPct": r["end_level_pct"], "peakLevelPct": r["peak_level_pct"],
                         "levelGainPct": (round(r["end_level_pct"] - r["start_level_pct"], 1)
                                          if r["end_level_pct"] is not None and r["start_level_pct"] is not None else None),
                         "valveOpenSeconds": round(r["valve_open_seconds"] or 0), "unsafeSeconds": round(r["unsafe_seconds"] or 0)} for r in rows])

    # ------------------------------------------------------------ legacy combined
    @app.get("/api/live")
    def live_combined():
        conn = read()
        try:
            gw = services.gateway_status(conn, cfg)
            return jsonify(node1=fmt_flow(latest_flow(conn, 1)), node2=fmt_flow(latest_flow(conn, 2)),
                           rainwater=fmt_rain(latest_rain(conn)),
                           gateway={"status": gw["status"], "receivedAt": gw["lastHeartbeatAt"],
                                    "ageSeconds": gw["heartbeatAgeSeconds"], "active": gw["online"]})
        finally:
            conn.close()

    # ------------------------------------------------------------ alerts
    @app.get("/api/alerts")
    def alerts():
        active = request.args.get("active", "0") in ("1", "true")
        limit = min(max(request.args.get("limit", 100, type=int), 1), 500)
        conn = read()
        try:
            return jsonify(services.list_alerts(conn, active, limit))
        finally:
            conn.close()

    @app.post("/api/alerts/<int:alert_id>/ack")
    @auth.require_role("admin")
    def ack_alert(alert_id):
        with db_session() as conn:
            r = conn.execute("SELECT id FROM alerts WHERE id=?", (alert_id,)).fetchone()
            if r is None:
                return jsonify(error="Alert not found"), 404
            conn.execute("UPDATE alerts SET acknowledged_by=?, acknowledged_at=? WHERE id=?", (g.user["email"], fmt_ts(utc_now()), alert_id))
            auth.audit(conn, "alert_ack", str(alert_id), user=g.user)
        return jsonify(ok=True)

    # ------------------------------------------------------------ profile
    def _target_email():
        return str((request.args.get("email") if request.method == "GET" else (request.get_json(silent=True) or {}).get("currentEmail")) or g.user["email"]).strip()

    @app.get("/api/profile")
    def get_profile():
        email = _target_email()
        if email.lower() != g.user["email"].lower() and g.user["role"] != "admin":
            return jsonify(error="Insufficient permissions"), 403
        conn = read()
        try:
            u = conn.execute("SELECT * FROM users WHERE lower(email)=lower(?)", (email,)).fetchone()
        finally:
            conn.close()
        if u is None:
            return jsonify(error="User not found"), 404
        return jsonify(user_json(u))

    @app.put("/api/profile")
    def update_profile():
        data = request.get_json(silent=True) or {}
        current = _target_email()
        if current.lower() != g.user["email"].lower() and g.user["role"] != "admin":
            return jsonify(error="Insufficient permissions"), 403
        with db_session() as conn:
            u = conn.execute("SELECT * FROM users WHERE lower(email)=lower(?)", (current,)).fetchone()
            if u is None:
                return jsonify(error="User not found"), 404
            new_email = str(data.get("email", u["email"])).strip()[:254]
            if "@" not in new_email or " " in new_email:
                return jsonify(error="Invalid email address"), 400
            clash = conn.execute("SELECT id FROM users WHERE lower(email)=lower(?) AND id<>?", (new_email, u["id"])).fetchone()
            if clash:
                return jsonify(error="That email address is already in use"), 409
            fields = {k: str(data.get(k, u[col]) or "")[:120] for k, col in (("firstName", "first_name"), ("lastName", "last_name"), ("phone", "phone"), ("address", "address"))}
            conn.execute("UPDATE users SET first_name=?, last_name=?, email=?, phone=?, address=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                         (fields["firstName"], fields["lastName"], new_email, fields["phone"], fields["address"], u["id"]))
            auth.audit(conn, "profile_update", f"user {u['id']}", user=g.user)
        return jsonify(ok=True)

    # ------------------------------------------------------------ reports
    @app.get("/api/reports")
    def get_reports():
        conn = read()
        try:
            rows = conn.execute("SELECT * FROM leakage_reports ORDER BY id DESC").fetchall()
        finally:
            conn.close()
        return jsonify([fmt_report(r) for r in rows])

    @app.post("/api/reports")
    def create_report():
        data = request.get_json(silent=True) or {}
        building, location, description = (str(data.get(k, "")).strip() for k in ("building", "location", "description"))
        if not building or not location or not description:
            return jsonify(error="Building, location, and description are required"), 400
        if len(building) > 100 or len(location) > 200 or len(description) > 2000:
            return jsonify(error="Field too long"), 400
        reporter = f"{g.user['firstName'] or ''} {g.user['lastName'] or ''}".strip() or g.user["email"]
        with db_session() as conn:
            cur = conn.execute("INSERT INTO leakage_reports (building,location,description,reported_by) VALUES (?,?,?,?)",
                               (building, location, description, reporter))
            rid = cur.lastrowid
            conn.execute("UPDATE leakage_reports SET report_code=? WHERE id=?", (f"RPT-{rid:03d}", rid))
            row = conn.execute("SELECT * FROM leakage_reports WHERE id=?", (rid,)).fetchone()
            auth.audit(conn, "report_created", f"RPT-{rid:03d}", user=g.user)
        return jsonify(fmt_report(row)), 201

    @app.put("/api/reports/<int:report_id>")
    @auth.require_role("admin")
    def update_report(report_id):
        status = str((request.get_json(silent=True) or {}).get("status", "")).strip().lower()
        if status not in ("pending", "investigating", "resolved", "rejected"):
            return jsonify(error="Invalid status. Must be pending, investigating, resolved, or rejected"), 400
        with db_session() as conn:
            if conn.execute("SELECT 1 FROM leakage_reports WHERE id=?", (report_id,)).fetchone() is None:
                return jsonify(error="Report not found"), 404
            conn.execute("UPDATE leakage_reports SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, report_id))
            row = conn.execute("SELECT * FROM leakage_reports WHERE id=?", (report_id,)).fetchone()
            auth.audit(conn, "report_status", f"{report_id} -> {status}", user=g.user)
        return jsonify(fmt_report(row))

    # ------------------------------------------------------------ admin
    @app.get("/api/admin/users")
    @auth.require_role("admin")
    def admin_users():
        conn = read()
        try:
            rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        finally:
            conn.close()
        return jsonify([{**user_json(r), "active": bool(r["active"]), "lockedUntil": r["locked_until"],
                         "createdAt": r["created_at"]} for r in rows])

    @app.post("/api/admin/users")
    @auth.require_role("admin")
    def admin_create_user():
        d = request.get_json(silent=True) or {}
        email = str(d.get("email", "")).strip()[:254]
        pw = str(d.get("password", ""))
        role = str(d.get("role", "user"))
        if "@" not in email or role not in ("user", "admin"):
            return jsonify(error="Valid email and role (user|admin) required"), 400
        problem = auth.password_problem(pw, cfg.min_password_length)
        if problem:
            return jsonify(error=problem), 400
        with db_session() as conn:
            if conn.execute("SELECT 1 FROM users WHERE lower(email)=lower(?)", (email,)).fetchone():
                return jsonify(error="That email address is already in use"), 409
            cur = conn.execute("INSERT INTO users (first_name,last_name,email,password_hash,phone,address,role,must_change_password) VALUES (?,?,?,?,?,?,?,1)",
                               (str(d.get("firstName", "User"))[:80], str(d.get("lastName", "Member"))[:80], email,
                                generate_password_hash(pw), str(d.get("phone", ""))[:40], str(d.get("address", ""))[:200], role))
            auth.audit(conn, "user_created", f"{email} ({role})", user=g.user)
        return jsonify(ok=True, id=cur.lastrowid), 201

    @app.put("/api/admin/users/<int:uid>")
    @auth.require_role("admin")
    def admin_update_user(uid):
        d = request.get_json(silent=True) or {}
        with db_session() as conn:
            u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if u is None:
                return jsonify(error="User not found"), 404
            role = d.get("role", u["role"])
            active = d.get("active", bool(u["active"]))
            if role not in ("user", "admin") or not isinstance(active, bool):
                return jsonify(error="Invalid role or active flag"), 400
            if uid == g.user["id"] and (role != "admin" or not active):
                return jsonify(error="You cannot demote or disable your own account"), 400
            conn.execute("UPDATE users SET role=?, active=?, failed_logins=0, locked_until=NULL WHERE id=?", (role, int(active), uid))
            if not active:
                conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            auth.audit(conn, "user_updated", f"{uid}: role={role} active={active}", user=g.user)
        return jsonify(ok=True)

    @app.get("/api/admin/audit")
    @auth.require_role("admin")
    def admin_audit():
        limit = min(max(request.args.get("limit", 100, type=int), 1), 1000)
        conn = read()
        try:
            rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        finally:
            conn.close()
        return jsonify([{k: r[k] for k in r.keys()} for r in rows])

    registry_api.register(app, cfg)
    nodes_api.register(app, cfg)
    pipeline_api.register(app, cfg)
    calibration_api.register(app, cfg)
    usage_service.register(app, cfg)
    clock_service.register(app, cfg)
    history_api.register(app, cfg)
    if not nodes_api.parse_cmd_key(cfg.cmd_key_hex):
        print("[CMD] AQUAGUARD_CMD_KEY is missing or all zero: node commands (add/rename/calibrate/replace/remove) are DISABLED.")

    # ------------------------------------------------------------ background workers
    if start_background:
        mqtt_service.start_mqtt(cfg)
        ev = mqtt_service.Evaluator(cfg)
        ev.start()
        app.config["EVALUATOR"] = ev
    return app


if __name__ == "__main__":
    _cfg = load_settings()
    _app = create_app(_cfg)
    print(f"[FLASK] Aqua Guard API on http://{_cfg.host}:{_cfg.port}  (MQTT {'on' if _cfg.mqtt_enabled else 'off'}, "
          f"{'TLS' if _cfg.mqtt_tls else 'PLAINTEXT dev broker' if 'emqx.io' in _cfg.mqtt_host else 'plain'})")
    _app.run(host=_cfg.host, port=_cfg.port, debug=False, threaded=True)
