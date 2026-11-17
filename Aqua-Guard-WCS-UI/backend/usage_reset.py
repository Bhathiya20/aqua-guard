"""Demo "Reset usage": the usage counters start fresh from NOW, non-destructively.

Only a baseline timestamp is stored (`usage_settings`, scope `usage_baseline`, no schema change).  Samples, history, alerts and leak events are never
touched: every usage figure ("Used today", "Used in 7 days", per-node Today / 7 days, running time) simply counts from max(its own start, baseline).
Analytics and History read the raw data and ignore the baseline.  It switches itself off after 24 h unless extended; undo removes it at once.
Reset, extend, undo and the automatic switch-off are written to the audit log.
"""
import json
from datetime import datetime, timezone

from flask import g, jsonify, request

import auth
from database import db_session, get_db
from ingest import fmt_ts, utc_now

SCOPE = "usage_baseline"
MAX_S = 24 * 3600
USAGE_KINDS = ("usage_prolonged", "usage_continuous", "night_flow")


def _load(conn):
    r = conn.execute("SELECT json, updated_by FROM usage_settings WHERE scope=?", (SCOPE,)).fetchone()
    return (json.loads(r["json"]), r["updated_by"]) if r else (None, None)


def _save(conn, value, user):
    conn.execute("INSERT INTO usage_settings (scope,json,updated_by,updated_at) VALUES (?,?,?,?) ON CONFLICT(scope) DO UPDATE SET json=excluded.json, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                 (SCOPE, json.dumps(value, separators=(",", ":")), user, fmt_ts(utc_now())))


def baseline(conn, now_s: float) -> float | None:
    """The active baseline (epoch seconds) or None (no reset, or it has expired)."""
    st, _ = _load(conn)
    if not st or now_s >= st.get("until", 0):
        return None
    return float(st["from"])


def clamp(conn, since: str, now_s: float) -> str:
    """A 'since' timestamp (fmt_ts string) moved forward to the baseline when that is later."""
    b = baseline(conn, now_s)
    if b is None:
        return since
    return max(since, fmt_ts(datetime.fromtimestamp(b, timezone.utc)))


def state_json(conn, now_s: float) -> dict:
    b = baseline(conn, now_s)
    st, by = _load(conn)
    if b is None:
        return {"active": False, "since": None, "until": None, "by": None, "cleared": False, "maxS": MAX_S}
    return {"active": True, "since": st["from"], "until": st["until"], "by": by, "cleared": bool(st.get("clear")), "maxS": MAX_S}


def reset(conn, user, now_s: float, clear: bool = False) -> dict:
    _save(conn, {"from": int(now_s), "until": int(now_s) + MAX_S, "clear": bool(clear)}, user["email"])
    cleared = {"timers": 0, "alerts": 0}
    if clear:
        ts = fmt_ts(datetime.fromtimestamp(now_s, timezone.utc))
        cleared["timers"] = conn.execute("UPDATE usage_state SET flow_since=? WHERE flowing=1", (now_s,)).rowcount
        ph = ",".join("?" * len(USAGE_KINDS))
        cleared["alerts"] = conn.execute(
            f"UPDATE alerts SET active=0, cleared_at=?, state='resolved', updated_at=?, resolved_by=?, resolve_reason='test', resolve_note='Usage reset (demo)',"
            f" acknowledged_by=COALESCE(acknowledged_by,?), acknowledged_at=COALESCE(acknowledged_at,?) WHERE active=1 AND kind IN ({ph})",
            (ts, ts, user["email"], user["email"], ts, *USAGE_KINDS)).rowcount
        conn.execute("UPDATE usage_state SET alert_id=NULL WHERE alert_id IS NOT NULL AND alert_id NOT IN (SELECT id FROM alerts WHERE active=1)")
    auth.audit(conn, "usage_reset", json.dumps({"from": int(now_s), "until": int(now_s) + MAX_S, "clearTimersAndAlerts": bool(clear), **cleared}), user=user)
    return state_json(conn, now_s)


def extend(conn, user, now_s: float) -> dict | None:
    st, by = _load(conn)
    if baseline(conn, now_s) is None:
        return None
    st["until"] = int(now_s) + MAX_S
    _save(conn, st, by)
    auth.audit(conn, "usage_reset_extend", json.dumps({"until": st["until"]}), user=user)
    return state_json(conn, now_s)


def undo(conn, user, now_s: float) -> dict:
    st, _ = _load(conn)
    if st:
        conn.execute("DELETE FROM usage_settings WHERE scope=?", (SCOPE,))
        auth.audit(conn, "usage_reset_undo", json.dumps({"was": st.get("from")}), user=user)
    return state_json(conn, now_s)


def expire(conn, now_s: float) -> bool:
    """Remove an expired baseline (audit entry by the system).  True when it did."""
    st, _ = _load(conn)
    if st and now_s >= st.get("until", 0):
        conn.execute("DELETE FROM usage_settings WHERE scope=?", (SCOPE,))
        auth.audit(conn, "usage_reset_undo", json.dumps({"was": st.get("from"), "why": "switched off automatically after 24 h"}), email="system")
        return True
    return False


def register(app, cfg):
    @app.get("/api/usage-reset")
    def usage_reset_get():
        conn = get_db()
        try:
            return jsonify(state_json(conn, utc_now().timestamp()))
        finally:
            conn.close()

    @app.post("/api/usage-reset")
    @auth.require_role("admin")
    def usage_reset_post():
        body = request.get_json(silent=True) or {}
        with db_session() as conn:
            return jsonify(reset(conn, g.user, utc_now().timestamp(), bool(body.get("clear"))))

    @app.post("/api/usage-reset/extend")
    @auth.require_role("admin")
    def usage_reset_extend():
        with db_session() as conn:
            out = extend(conn, g.user, utc_now().timestamp())
            return (jsonify(out) if out else (jsonify(error="No usage reset is active."), 409))

    @app.delete("/api/usage-reset")
    @auth.require_role("admin")
    def usage_reset_undo():
        with db_session() as conn:
            return jsonify(undo(conn, g.user, utc_now().timestamp()))
