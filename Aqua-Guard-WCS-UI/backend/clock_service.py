"""Demo clock + unified time model on the database, and their REST API.

* Demo clock (admin, demo only): Auto (real time) / Force DAY / Force NIGHT, switches itself off after 1 h.  Stored in `usage_settings`
  (scope `demo_clock`, no schema change).  It never rewrites a stored timestamp, never touches the gateway or the readings: the rules only
  read "now" through `load_clock`.  Every switch (and the automatic switch-off) is written to the audit log.
* Time model: the quiet hours are the ones of the usage defaults (one source of truth); a zone may override them and the continuous-flow
  limits (scope `zone:<pipeline id>:<zone id>`).  `zone_time_rules` returns the effective values and where each comes from.
"""
import json

from flask import g, jsonify, request

import auth
from analysis import clock as C
from database import db_session, get_db
from ingest import fmt_ts, utc_now

SCOPE = "demo_clock"


def _load(conn, scope):
    r = conn.execute("SELECT json, updated_by, updated_at FROM usage_settings WHERE scope=?", (scope,)).fetchone()
    return (json.loads(r["json"]), r["updated_by"], r["updated_at"]) if r else (None, None, None)


def _save(conn, scope, value, user):
    conn.execute("INSERT INTO usage_settings (scope,json,updated_by,updated_at) VALUES (?,?,?,?) ON CONFLICT(scope) DO UPDATE SET json=excluded.json, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                 (scope, json.dumps(value, separators=(",", ":")), user, fmt_ts(utc_now())))


# ---------------------------------------------------------------- demo clock
def load_clock(conn, now_s: float) -> C.Clock:
    """The clock the rules must use at `now_s` (the real clock unless a demo clock is on and not expired)."""
    st, _, _ = _load(conn, SCOPE)
    if not st:
        return C.REAL
    return C.active(st.get("mode"), st.get("from"), st.get("until"), now_s)


def clock_json(conn, now_s: float) -> dict:
    c = load_clock(conn, now_s)
    st, by, _ = _load(conn, SCOPE)
    return {"mode": c.mode, "forced": c.forced, "since": c.since, "until": c.until, "by": (by if c.forced else None), "maxS": C.MAX_S}


def set_clock(conn, mode: str, user, now_s: float) -> dict:
    if mode not in C.MODES:
        raise ValueError("mode must be auto, day or night")
    if mode == C.AUTO:
        conn.execute("DELETE FROM usage_settings WHERE scope=?", (SCOPE,))
    else:
        _save(conn, SCOPE, {"mode": mode, "from": int(now_s), "until": int(now_s) + C.MAX_S}, user["email"] if user is not None else None)
    auth.audit(conn, "demo_clock", json.dumps({"mode": mode, "until": None if mode == C.AUTO else int(now_s) + C.MAX_S}), user=user)
    return clock_json(conn, now_s)


def expire_clock(conn, now_s: float) -> bool:
    """Switch an expired demo clock off (audit entry by the system).  True when it did."""
    st, _, _ = _load(conn, SCOPE)
    if st and not C.active(st.get("mode"), st.get("from"), st.get("until"), now_s).forced:
        conn.execute("DELETE FROM usage_settings WHERE scope=?", (SCOPE,))
        auth.audit(conn, "demo_clock", json.dumps({"mode": "auto", "why": "switched off automatically after 1 h"}), email="system")
        return True
    return False


# ---------------------------------------------------------------- time model
def global_quiet(conn) -> tuple[str, str]:
    import usage_service
    d = usage_service.get_defaults(conn)
    return d["quiet_start"], d["quiet_end"]


def _zone_scope(pid: int, zid: str) -> str:
    return f"zone:{pid}:{zid}"


def zone_overrides(conn, pid: int) -> dict:
    """zone id -> {quiet_start|quiet_end|continuous_suspect_h|continuous_confirm_h} for the zones of one pipeline that override something."""
    out = {}
    for r in conn.execute("SELECT scope, json FROM usage_settings WHERE scope LIKE ?", (f"zone:{pid}:%",)).fetchall():
        try:
            v = json.loads(r["json"])
        except ValueError:
            continue
        zid = r["scope"].split(":", 2)[2]
        v = {k: x for k, x in v.items() if k in C.ZONE_TIME_FIELDS}
        if v:
            out[zid] = v
    return out


def apply_to_params(p, conn, pid: int, clock: C.Clock) -> dict:
    """Put the unified time model into ZoneParams (quiet hours = night window, continuous limits, demo clock).  -> per-zone resolution for the UI."""
    qs, qe = global_quiet(conn)
    p.night_start_min, p.night_end_min = C.hm(qs), C.hm(qe)
    p.clock_mode, p.clock_since = (clock.mode, clock.since) if clock.forced else ("auto", None)
    over = zone_overrides(conn, pid)
    for zid, o in over.items():
        z = p.overrides.setdefault(zid, {})
        if "quiet_start" in o:
            z["night_start_min"] = C.hm(o["quiet_start"])
        if "quiet_end" in o:
            z["night_end_min"] = C.hm(o["quiet_end"])
        for k in ("continuous_suspect_h", "continuous_confirm_h"):
            if k in o:
                z[k] = float(o[k])
    return {"quiet": (qs, qe), "overrides": over}


def zone_time_rules(res: dict, zid: str) -> dict:
    """The effective time rules of one zone for the API / UI: values, the source of each value and whether anything is overridden."""
    r = C.resolve_zone_time(res["quiet"][0], res["quiet"][1], res["overrides"].get(zid))
    v, s = r["values"], r["source"]
    return {"quietStart": v["quiet_start"], "quietEnd": v["quiet_end"], "continuousSuspectH": v["continuous_suspect_h"], "continuousConfirmH": v["continuous_confirm_h"],
            "source": {"quietStart": s["quiet_start"], "quietEnd": s["quiet_end"], "continuousSuspectH": s["continuous_suspect_h"], "continuousConfirmH": s["continuous_confirm_h"]},
            "overridden": any(x == "zone" for x in s.values()),
            "defaults": {"quietStart": res["quiet"][0], "quietEnd": res["quiet"][1], **{"continuousSuspectH": C.ZONE_TIME_FACTORY["continuous_suspect_h"], "continuousConfirmH": C.ZONE_TIME_FACTORY["continuous_confirm_h"]}}}


# ---------------------------------------------------------------- REST API
def register(app, cfg):
    @app.get("/api/demo-clock")
    def demo_clock_get():
        conn = get_db()
        try:
            return jsonify(clock_json(conn, utc_now().timestamp()))
        finally:
            conn.close()

    @app.put("/api/demo-clock")
    @auth.require_role("admin")
    def demo_clock_put():
        body = request.get_json(silent=True) or {}
        mode = body.get("mode")
        if mode not in C.MODES:
            return jsonify(error="mode must be auto, day or night"), 400
        with db_session() as conn:
            return jsonify(set_clock(conn, mode, g.user, utc_now().timestamp()))

    def _pipeline_zone(conn, pid, zid):
        import analysis_service
        from pipeline.model import DocError
        row = conn.execute("SELECT p.id, p.name, p.active_version, p.demo_until, v.doc FROM pipelines p JOIN pipeline_versions v "
                           "ON v.pipeline_id=p.id AND v.version=COALESCE(p.active_version, (SELECT MAX(version) FROM pipeline_versions WHERE pipeline_id=p.id)) "
                           "WHERE p.id=? AND p.deleted=0", (pid,)).fetchone()
        if row is None:
            return None, "Pipeline not found"
        try:
            doc = json.loads(row["doc"])
            from analysis.zones import partition
            from pipeline.model import parse
            if partition(parse(doc)).zone(zid) is None:
                return None, "Zone not found"
        except DocError as e:
            return None, str(e)
        return row, None

    def _json(conn, pid, zid):
        res = {"quiet": global_quiet(conn), "overrides": zone_overrides(conn, pid)}
        return zone_time_rules(res, zid)

    @app.get("/api/pipelines/<int:pid>/zones/<zid>/time-settings")
    def zone_time_get(pid, zid):
        conn = get_db()
        try:
            row, err = _pipeline_zone(conn, pid, zid)
            return (jsonify(_json(conn, pid, zid)) if row else (jsonify(error=err), 404))
        finally:
            conn.close()

    @app.put("/api/pipelines/<int:pid>/zones/<zid>/time-settings")
    @auth.require_role("admin")
    def zone_time_put(pid, zid):
        body = request.get_json(silent=True) or {}
        sub = {k: body[k] for k in C.ZONE_TIME_FIELDS if k in body}
        if not sub:
            return jsonify(error="Nothing to change: send quiet_start, quiet_end, continuous_suspect_h and/or continuous_confirm_h."), 400
        with db_session() as conn:
            row, err = _pipeline_zone(conn, pid, zid)
            if row is None:
                return jsonify(error=err), 404
            cur = zone_overrides(conn, pid).get(zid, {})
            merged = {**cur, **sub}
            res = {"quiet": global_quiet(conn), "overrides": {zid: merged}}
            eff = C.resolve_zone_time(res["quiet"][0], res["quiet"][1], merged)["values"]
            errs = C.validate_zone_time(sub)
            if eff["continuous_suspect_h"] >= eff["continuous_confirm_h"]:
                errs.append("The suspected limit must be shorter than the confirmed limit.")
            if errs:
                return jsonify(error=" ".join(errs)), 400
            _save(conn, _zone_scope(pid, zid), merged, g.user["email"])
            auth.audit(conn, "zone_time_set", f"pipeline={pid} zone={zid} " + " ".join(f"{k}={v}" for k, v in sub.items()), user=g.user)
            return jsonify(_json(conn, pid, zid))

    @app.delete("/api/pipelines/<int:pid>/zones/<zid>/time-settings")
    @auth.require_role("admin")
    def zone_time_reset(pid, zid):
        with db_session() as conn:
            row, err = _pipeline_zone(conn, pid, zid)
            if row is None:
                return jsonify(error=err), 404
            conn.execute("DELETE FROM usage_settings WHERE scope=?", (_zone_scope(pid, zid),))
            auth.audit(conn, "zone_time_reset", f"pipeline={pid} zone={zid}", user=g.user)
            return jsonify(_json(conn, pid, zid))
