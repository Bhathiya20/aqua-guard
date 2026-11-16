"""REST API for Device Management (registry, discovery, pairing, calibration).

Reads: any signed-in user.  Every mutation: administrator + CSRF (enforced by auth.before_request).
Nothing here fabricates data: discovery lists devices the gateway actually reported, pairing results
come from the gateway/device challenge-response, measurements come from flow_samples.
"""
import time
from datetime import timedelta

from flask import g, jsonify, request

import auth
import ingest
import pairing
import services
from database import db_session, get_db
from ingest import utc_now

RATE_MAX, RATE_WINDOW_S = 20, 60             # pairing requests per signed-in user per minute (the per-device lockout is the real brute-force guard)


def _rate_ok(rate, key) -> bool:
    now = time.time()
    hits = [t for t in rate.get(key, []) if now - t < RATE_WINDOW_S]
    if len(hits) >= RATE_MAX:
        rate[key] = hits
        return False
    hits.append(now)
    rate[key] = hits
    return True


def register(app, cfg):
    rate = {}                                # per-app, in memory
    def read():
        c = get_db()
        c.execute("PRAGMA query_only=ON")
        return c

    def row_json(conn, r, gw_online, now):
        dev_key = {1: "node1", 2: "node2", 3: "rainwater"}.get(r["uid"]) if r["legacy"] else f"dev{r['uid']}"
        drow = conn.execute("SELECT * FROM devices WHERE device_key=?", (dev_key,)).fetchone()
        state, freshest, age = pairing.device_online_state(cfg, drow, gw_online, now) if r["pairing_status"] == "paired" else ("n/a", None, None)
        last = None
        if r["device_type"] == "flow" and r["pairing_status"] == "paired":
            f = conn.execute("SELECT * FROM flow_samples WHERE node_id=? ORDER BY id DESC LIMIT 1", (r["uid"],)).fetchone()
            if f is not None:
                ppl = f["pulses_per_liter_x10"] / 10.0
                last = {"flowRate": f["flow_lpm"], "pulsesPerLiter": ppl, "cumPulses": f["cum_pulses"], "measuredAt": f["measured_at"],
                        "receivedAt": f["received_at"], "rssi": f["rssi"], "channel": f["channel"]}
            tot = services.consumption_total(conn, r["uid"])
            last = (last or {}) | {"totalLiters": tot["liters"], "samples": tot["samples"], "missedIntervals": tot["missedIntervals"]}
        return {
            "uid": r["uid"], "deviceId": r["device_id"], "type": r["device_type"], "displayName": r["display_name"],
            "mac": r["mac"], "pairingStatus": r["pairing_status"], "slot": r["slot"], "legacy": bool(r["legacy"]),
            "firmwareVersion": r["firmware_version"], "calibrationPulsesPerLiter": r["calibration_ppl"],
            "state": state, "lastSeen": freshest or r["last_seen"], "ageSeconds": age,
            "pairedAt": r["paired_at"], "pairedBy": r["paired_by"], "unpairedAt": r["unpaired_at"], "createdAt": r["created_at"],
            "pairingError": r["pairing_error"], "locked": bool(r["code_locked_until"] and r["code_locked_until"] > time.time()),
            "lastVerifiedAt": r["last_verified_at"],
            "sessions": drow["sessions"] if drow else 0, "bootId": drow["last_boot_id"] if drow else None,
            "latest": last,
        }

    @app.get("/api/registry/devices")
    def registry_list():
        conn = read()
        try:
            now = utc_now()
            gw = services.gateway_status(conn, cfg, now)
            rows = conn.execute("SELECT * FROM device_registry ORDER BY legacy DESC, uid").fetchall()
            return jsonify(devices=[row_json(conn, r, gw["online"], now) for r in rows], gatewayOnline=gw["online"],
                           pairingConfigured=pairing.pairing_configured(cfg), maxSlots=pairing.MAX_SLOTS - pairing.FIRST_DYNAMIC_SLOT + 1)
        finally:
            conn.close()

    @app.get("/api/registry/devices/<int:uid>")
    def registry_get(uid):
        conn = read()
        try:
            r = conn.execute("SELECT * FROM device_registry WHERE uid=?", (uid,)).fetchone()
            if r is None:
                return jsonify(error="Device not found"), 404
            now = utc_now()
            gw = services.gateway_status(conn, cfg, now)
            out = row_json(conn, r, gw["online"], now)
            hist = conn.execute("SELECT ts,kind,ok,detail,user_email FROM pairing_attempts WHERE uid=? ORDER BY id DESC LIMIT 15", (uid,)).fetchall()
            out["history"] = [{k: h[k] for k in h.keys()} for h in hist]
            return jsonify(out)
        finally:
            conn.close()

    @app.get("/api/registry/discovered")
    def registry_discovered():
        """Devices the gateway currently hears announcing themselves (not authorised)."""
        conn = read()
        try:
            now = utc_now()
            gw = services.gateway_status(conn, cfg, now)
            cutoff = ingest.fmt_ts(now - timedelta(seconds=90))
            rows = conn.execute("SELECT d.*, r.pairing_status AS reg_status, r.device_id AS reg_device_id "
                                "FROM discovered_devices d LEFT JOIN device_registry r ON r.uid=d.uid "
                                "WHERE d.last_seen >= ? ORDER BY d.last_seen DESC", (cutoff,)).fetchall()
            items = []
            for d in rows:
                status = d["reg_status"]
                items.append({
                    "uid": d["uid"], "mac": d["mac"], "type": d["device_type"], "firmwareVersion": d["firmware_version"],
                    "channel": d["channel"], "rssi": d["rssi"], "firstSeen": d["first_seen"], "lastSeen": d["last_seen"],
                    "announces": d["announces"],
                    "provisioned": status is not None, "pairingStatus": status, "deviceId": d["reg_device_id"],
                    "canPair": status in ("provisioned", "unpaired", "pairing"),
                })
            return jsonify(devices=items, gatewayOnline=gw["online"], pairingConfigured=pairing.pairing_configured(cfg))
        finally:
            conn.close()

    @app.post("/api/registry/devices/<int:uid>/pair")
    @auth.require_role("admin")
    def registry_pair(uid):
        d = request.get_json(silent=True) or {}
        code = str(d.get("code", ""))
        if not code.strip():
            return jsonify(error="Pairing code required"), 400
        if not _rate_ok(rate, (g.user["id"], "pair")):
            return jsonify(error="Too many pairing attempts. Wait a minute."), 429
        try:
            with db_session() as conn:
                res = pairing.start_pairing(conn, cfg, uid, code, d.get("name"), g.user["email"], auth.client_ip())
                if "error" in res:                       # wrong code / locked: commit the failure counter, then report
                    auth.audit(conn, "pairing_denied", f"uid {uid}: {res['error'].code}", user=g.user)
                    err = res["error"]
                    return jsonify(error=err.message, code=err.code), err.status
                auth.audit(conn, "pairing_started", f"uid {uid} slot {res['slot']}", user=g.user)
        except pairing.PairingError as e:
            # wrong code etc. must persist its failure counter even though we return an error
            return jsonify(error=e.message, code=e.code), e.status
        sent = pairing.send_cmd(res["command"])
        return jsonify(ok=True, status="pairing", slot=res["slot"], commandSent=sent,
                       message="Authorisation sent to the gateway. Waiting for the device to complete the secure handshake." if sent else
                               "Authorisation is ready but the gateway link (MQTT) is not connected - it will be retried when the device announces itself."), 202

    @app.post("/api/registry/devices/<int:uid>/unpair")
    @auth.require_role("admin")
    def registry_unpair(uid):
        try:
            with db_session() as conn:
                res = pairing.unpair(conn, cfg, uid, g.user["email"])
                auth.audit(conn, "device_unpaired", f"uid {uid}", user=g.user)
        except pairing.PairingError as e:
            return jsonify(error=e.message, code=e.code), e.status
        sent = pairing.send_cmd(res["command"])
        return jsonify(ok=True, commandSent=sent)

    @app.post("/api/registry/devices/<int:uid>/new-code")
    @auth.require_role("admin")
    def registry_new_code(uid):
        try:
            with db_session() as conn:
                code = pairing.reset_code(conn, uid, g.user["email"])
                auth.audit(conn, "pairing_code_reset", f"uid {uid}", user=g.user)
        except pairing.PairingError as e:
            return jsonify(error=e.message, code=e.code), e.status
        return jsonify(ok=True, pairingCode=code, note="Shown once. Write it on the device label.")

    @app.put("/api/registry/devices/<int:uid>")
    @auth.require_role("admin")
    def registry_update(uid):
        d = request.get_json(silent=True) or {}
        with db_session() as conn:
            r = conn.execute("SELECT * FROM device_registry WHERE uid=?", (uid,)).fetchone()
            if r is None:
                return jsonify(error="Device not found"), 404
            name = r["display_name"]
            if "displayName" in d:
                name = str(d.get("displayName", "")).strip()
                if not (1 <= len(name) <= 60):
                    return jsonify(error="Name must be 1-60 characters"), 400
            ppl = r["calibration_ppl"]
            if "calibrationPulsesPerLiter" in d:
                v = d["calibrationPulsesPerLiter"]
                if v is None:
                    ppl = None
                elif isinstance(v, bool) or not isinstance(v, (int, float)) or not (10.0 <= float(v) <= 6000.0):
                    return jsonify(error="Calibration must be between 10 and 6000 pulses per litre (or null for the device default)"), 400
                else:
                    if r["device_type"] != "flow":
                        return jsonify(error="Only flow sensors have a calibration factor"), 400
                    ppl = round(float(v), 2)
            conn.execute("UPDATE device_registry SET display_name=?, calibration_ppl=? WHERE uid=?", (name, ppl, uid))
            auth.audit(conn, "device_updated", f"uid {uid}: name={name!r} calibration={ppl}", user=g.user)
        return jsonify(ok=True, displayName=name, calibrationPulsesPerLiter=ppl)
