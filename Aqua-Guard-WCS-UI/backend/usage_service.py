"""Per-node usage profiles on the database: settings, the periodic duration check, its alerts and the REST API.

The pure rules live in analysis/usage.py.  This module only loads each flow node's NEW samples since the last tick, advances its stored run,
classifies it and keeps ONE alert per event (key USAGE:<node ref>):  PROLONGED_USAGE (warning) -> CONTINUOUS_ABNORMAL_USAGE / NIGHT_FLOW (critical),
resolved automatically when the flow has stopped (no-flow confirmed).  It runs on each node's own flow, independent of the zone balance.
"""
import json
from datetime import datetime, timezone

from flask import g, jsonify, request

import auth
from analysis import usage as U
from database import db_session, get_db
from ingest import fmt_ts, parse_ts, utc_now

KIND = {"PROLONGED_USAGE": "usage_prolonged", "CONTINUOUS_ABNORMAL_USAGE": "usage_continuous", "NIGHT_FLOW": "night_flow"}
RANK = {"PROLONGED_USAGE": 1, "CONTINUOUS_ABNORMAL_USAGE": 2, "NIGHT_FLOW": 3}
KIND_RANK = {v: RANK[k] for k, v in KIND.items()}
REPLAY_S = 120            # first run for a node: look back this far only (a flow that started long before is simply not known)


# ---------------------------------------------------------------- settings
def _load(conn, scope):
    r = conn.execute("SELECT json, updated_by, updated_at FROM usage_settings WHERE scope=?", (scope,)).fetchone()
    return (json.loads(r["json"]), r["updated_by"], r["updated_at"]) if r else (None, None, None)


def _save(conn, scope, value, user):
    conn.execute("INSERT INTO usage_settings (scope,json,updated_by,updated_at) VALUES (?,?,?,?) ON CONFLICT(scope) DO UPDATE SET json=excluded.json, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                 (scope, json.dumps(value, separators=(",", ":")), user, fmt_ts(utc_now())))


def get_defaults(conn) -> dict:
    stored, _, _ = _load(conn, "default")
    out = dict(U.FACTORY)
    out.update({k: v for k, v in (stored or {}).items() if k in U.FIELDS})
    out["preset"] = "default"
    return out


def node_profile(conn, ref: int, now_s: float, clock=None):
    """-> (effective profile, override, demo) for one node.  demo = {'until': epoch, 'quiet': bool} while the per-node demo is on.
    The demo clock (day / night) is NOT part of the profile: it is handed to `classify` and only changes how "now" is read."""
    defaults = get_defaults(conn)
    override, _, _ = _load(conn, f"node:{ref}")
    p = U.resolve(defaults, override)
    demo, _, _ = _load(conn, f"demo:{ref}")
    if demo and demo.get("until", 0) > now_s:
        p = U.demo_profile(p, int(demo.get("from", now_s)) if demo.get("quiet") else None, int(demo["until"]) if demo.get("quiet") else None)
        if clock is not None and clock.night:
            p["allow_24h"] = False                       # demo_profile switches the night rule off when no night test was asked for; the clock says NIGHT
        return p, override, demo
    return p, override, None


def _label(n) -> str:
    return f"Node {n['number']}" + (f" ({n['name']})" if n["name"] else "")


# ---------------------------------------------------------------- periodic check
def _site_offset(now: datetime) -> int:
    import analysis_service
    return analysis_service.site_offset_min(now)


def _load_run(conn, ref):
    r = conn.execute("SELECT * FROM usage_state WHERE node_ref=?", (ref,)).fetchone()
    if r is None:
        return U.Run(), None
    return U.Run(bool(r["flowing"]), r["flow_since"], r["last_flow_at"], r["processed_until"] or 0.0, r["peak_lpm"] or 0.0), r


def _acc(conn, alert_id, lpm, now: datetime):
    """lost_l += flow x dt (dt capped at 10 s so a restart gap is not invented water)."""
    a = conn.execute("SELECT acc_at FROM alerts WHERE id=?", (alert_id,)).fetchone()
    ts = fmt_ts(now)
    dt = 0.0
    if a and a["acc_at"]:
        try:
            dt = min(10.0, max(0.0, (now - parse_ts(a["acc_at"])).total_seconds()))
        except Exception:
            dt = 0.0
    conn.execute("UPDATE alerts SET lost_l=COALESCE(lost_l,0)+?, acc_at=?, peak_flow_lpm=MAX(COALESCE(peak_flow_lpm,0),?) WHERE id=?", (max(lpm or 0.0, 0.0) * dt / 60.0, ts, lpm or 0.0, alert_id))


def _text(n, cls, p, in_demo=False) -> str:
    from services import human_s
    t = human_s(cls["tFlowS"] or 0)
    demo = "[demo] " if in_demo else ""
    if cls["status"] == "NIGHT_FLOW":
        return f"{demo}{_label(n)}: water is flowing during quiet hours ({p['quiet_start']}-{p['quiet_end']}) - night flow, flowing for {t}"
    if cls["status"] == "CONTINUOUS_ABNORMAL_USAGE":
        return f"{demo}{_label(n)}: continuous abnormal usage - water has been running for {t} (limit {human_s(p['t_max_s'])})"
    return f"{demo}{_label(n)}: prolonged usage - water has been running for {t} (limit {human_s(p['t_lim_s'])})"


def run_usage(conn, now: datetime | None = None):
    import clock_service
    now = now or utc_now()
    now_s = now.timestamp()
    off = _site_offset(now)
    ts = fmt_ts(now)
    clk = clock_service.load_clock(conn, now_s)                     # demo clock: day / night as simulated (alerts raised meanwhile are tagged demo)
    for n in conn.execute("SELECT id, number, name, out_of_service FROM nodes WHERE type='flow' AND state='paired'").fetchall():
        ref = n["id"]
        p, _, demo = node_profile(conn, ref, now_s, clk)
        run, row = _load_run(conn, ref)
        since = run.processed_until if run.processed_until else now_s - REPLAY_S
        rows = conn.execute("SELECT measured_at, flow_lpm FROM flow_samples WHERE node_ref=? AND anomaly=0 AND measured_at>? ORDER BY measured_at LIMIT 3000",
                            (ref, fmt_ts(datetime.fromtimestamp(since, timezone.utc)))).fetchall()
        samples = [(parse_ts(r["measured_at"]).timestamp(), float(r["flow_lpm"] or 0.0)) for r in rows]
        samples = [s for s in samples if s[0] > since]
        run = U.advance(run, samples, p)
        last_lpm = samples[-1][1] if samples else (row["last_lpm"] if row else None)
        cls = U.classify(run, now_s, p, off, clk)
        status = "OUT_OF_SERVICE" if n["out_of_service"] else cls["status"]
        alert = conn.execute("SELECT * FROM alerts WHERE alert_key=? AND active=1", (f"USAGE:{ref}",)).fetchone()
        alert_id = alert["id"] if alert else None
        in_demo = demo is not None or clk.forced
        if status in RANK:
            msg = _text(n, cls, p, in_demo)
            if alert is None:
                crit = cls["severity"] == "critical"
                cur = conn.execute("INSERT INTO alerts (alert_key,kind,severity,message,active,created_at,node_ref,state,updated_at,demo,suspected_at,confirmed_at,acc_at,lost_l,peak_flow_lpm) "
                                   "VALUES (?,?,?,?,1,?,?,?,?,?,?,?,?,0,?)",
                                   (f"USAGE:{ref}", KIND[status], cls["severity"], msg, ts, ref, "confirmed" if crit else "suspected", ts, int(in_demo), ts, ts if crit else None, ts, last_lpm or 0.0))
                alert_id = cur.lastrowid
            else:
                esc = RANK[status] > KIND_RANK.get(alert["kind"], 0)
                if esc:
                    conn.execute("UPDATE alerts SET kind=?, severity=?, message=?, state=?, updated_at=?, confirmed_at=COALESCE(confirmed_at, ?) WHERE id=?",
                                 (KIND[status], cls["severity"], msg, "confirmed" if cls["severity"] == "critical" else "suspected", ts, ts if cls["severity"] == "critical" else None, alert_id))
                elif msg != alert["message"] and (cls["tFlowS"] or 0) % 30 < 2:         # keep the text fresh without a write every 2 s
                    conn.execute("UPDATE alerts SET message=? WHERE id=?", (msg, alert_id))
        elif alert is not None and (status in ("NO_FLOW", "OUT_OF_SERVICE") or (alert["demo"] and not in_demo)
                                    or (status == "UNKNOWN" and now_s - run.processed_until > 600)):
            why = {"NO_FLOW": "flow stopped", "OUT_OF_SERVICE": "node out of service", "UNKNOWN": "no data from the node"}.get(status, "")
            if alert["demo"] and not in_demo and status not in ("NO_FLOW", "OUT_OF_SERVICE"):
                why = "demo ended"
            conn.execute("UPDATE alerts SET active=0, cleared_at=?, state='resolved', updated_at=?, message=message || ' (' || ? || ')' WHERE id=?", (ts, ts, why, alert["id"]))
            alert_id = None
        if alert_id and run.flowing:
            _acc(conn, alert_id, last_lpm, now)
        conn.execute("INSERT INTO usage_state (node_ref,flowing,flow_since,last_flow_at,processed_until,peak_lpm,last_lpm,status,alert_id,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
                     "ON CONFLICT(node_ref) DO UPDATE SET flowing=excluded.flowing, flow_since=excluded.flow_since, last_flow_at=excluded.last_flow_at, processed_until=excluded.processed_until,"
                     " peak_lpm=excluded.peak_lpm, last_lpm=excluded.last_lpm, status=excluded.status, alert_id=excluded.alert_id, updated_at=excluded.updated_at",
                     (ref, int(run.flowing), run.flow_since, run.last_flow_at, run.processed_until, run.peak_lpm, last_lpm, status, alert_id, ts))


# ---------------------------------------------------------------- live view
def profile_summary(p: dict) -> str:
    from services import human_s
    s = f"{U.PRESET_LABEL.get(p['preset'], p['preset'])}: {human_s(p['t_lim_s'])} / {human_s(p['t_max_s'])}"
    if p["allow_24h"]:
        s += " · 24 h use allowed"
    if p["schedule"]:
        s += " · allowed schedule"
    return s


def live_all(conn, now: datetime | None = None) -> list:
    import clock_service
    now = now or utc_now()
    now_s = now.timestamp()
    off = _site_offset(now)
    clk = clock_service.load_clock(conn, now_s)
    import usage_reset
    base = usage_reset.baseline(conn, now_s)
    out = []
    for n in conn.execute("SELECT id, number, name, out_of_service FROM nodes WHERE type='flow' AND state='paired' ORDER BY number, id").fetchall():
        p, override, demo = node_profile(conn, n["id"], now_s, clk)
        run, row = _load_run(conn, n["id"])
        if base is not None and run.flowing and run.flow_since is not None:
            run.flow_since = max(run.flow_since, base)                 # demo "Reset usage": the running time counts from the baseline
        cls = U.classify(run, now_s, p, off, clk)
        if n["out_of_service"]:
            cls = dict(cls, status="OUT_OF_SERVICE", tFlowS=None, next=None)
        out.append({"nodeRef": n["id"], "number": n["number"], "name": n["name"] or "", "label": _label(n), "preset": p["preset"] if demo is None else "demo",
                    "presetLabel": U.PRESET_LABEL.get(p["preset"], p["preset"]), "custom": override is not None, "summary": profile_summary(p),
                    "tLimS": p["t_lim_s"], "tMaxS": p["t_max_s"], "status": cls["status"], "tFlowS": cls["tFlowS"], "next": cls["next"], "allowed": cls["allowed"], "quiet": cls["quiet"],
                    "flowLpm": (row["last_lpm"] if row and now_s - (row["processed_until"] or 0) <= U.FRESH_S else None), "alertId": row["alert_id"] if row else None,
                    "demo": None if demo is None else {"until": demo["until"], "quiet": bool(demo.get("quiet"))}})
    return out


# ---------------------------------------------------------------- REST
def _clean(body: dict) -> dict:
    """Pick the profile fields out of a request body (camelCase or snake_case) and coerce the obvious types."""
    camel = {"tLimS": "t_lim_s", "tMaxS": "t_max_s", "flowOnLpm": "flow_on_lpm", "flowOffLpm": "flow_off_lpm", "noFlowS": "no_flow_s", "quietStart": "quiet_start",
             "quietEnd": "quiet_end", "quietConfirmS": "quiet_confirm_s", "allow24h": "allow_24h"}
    out = {}
    for k, v in (body or {}).items():
        k2 = camel.get(k, k)
        if k2 in U.FIELDS:
            out[k2] = v
    return out


def _diff(before: dict, after: dict) -> dict:
    """Only the fields that changed ({name: [old, new]}): the audit detail is cut at 500 characters, so it must stay short."""
    a, b = _public(before), _public(after)
    return {k: [a[k], b[k]] for k in b if a.get(k) != b[k]}


def _public(p: dict) -> dict:
    return {"preset": p["preset"], "tLimS": p["t_lim_s"], "tMaxS": p["t_max_s"], "flowOnLpm": p["flow_on_lpm"], "flowOffLpm": p["flow_off_lpm"], "noFlowS": p["no_flow_s"],
            "quietStart": p["quiet_start"], "quietEnd": p["quiet_end"], "quietConfirmS": p["quiet_confirm_s"], "allow24h": p["allow_24h"], "schedule": p["schedule"]}


PRESET_INFO = [{"key": k, "label": U.PRESET_LABEL[k], **({"tLimS": v["t_lim_s"], "tMaxS": v["t_max_s"]} if v else {})} for k, v in U.PRESETS.items()]


def register(app, cfg):
    @app.get("/api/usage/defaults")
    def usage_defaults():
        conn = get_db()
        try:
            stored, by, at = _load(conn, "default")
            return jsonify(profile=_public(get_defaults(conn)), factory=_public(U.FACTORY), presets=PRESET_INFO, custom=stored is not None, updatedBy=by, updatedAt=at)
        finally:
            conn.close()

    @app.put("/api/usage/defaults")
    @auth.require_role("admin")
    def usage_defaults_put():
        body = _clean(request.get_json(silent=True) or {})
        with db_session() as conn:
            before = get_defaults(conn)
            new = dict(before, **body, preset="default")
            err = U.validate(new)
            if err:
                return jsonify(error=" ".join(err)), 400
            _save(conn, "default", {k: new[k] for k in U.FIELDS if k != "preset"}, g.user["email"])
            auth.audit(conn, "usage_defaults", json.dumps({"changed": _diff(before, new)}), user=g.user)
            return jsonify(profile=_public(new))

    @app.delete("/api/usage/defaults")
    @auth.require_role("admin")
    def usage_defaults_reset():
        with db_session() as conn:
            before = get_defaults(conn)
            conn.execute("DELETE FROM usage_settings WHERE scope='default'")
            auth.audit(conn, "usage_defaults", json.dumps({"changed": "factory values restored"}), user=g.user)
        return jsonify(ok=True)

    @app.get("/api/usage/live")
    def usage_live():
        conn = get_db()
        try:
            return jsonify(nodes=live_all(conn))
        finally:
            conn.close()

    @app.get("/api/usage/audit")
    def usage_audit():
        limit = min(max(request.args.get("limit", 50, type=int), 1), 300)
        conn = get_db()
        try:
            rows = conn.execute("SELECT ts, email, action, detail FROM audit_log WHERE action LIKE 'usage_%' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return jsonify(audit=[{"ts": r["ts"], "user": r["email"], "action": r["action"], "detail": r["detail"]} for r in rows])
        finally:
            conn.close()

    def _node(conn, ref):
        return conn.execute("SELECT id, number, name, type FROM nodes WHERE id=?", (ref,)).fetchone()

    @app.get("/api/nodes/<int:ref>/usage-profile")
    def node_usage_profile(ref):
        conn = get_db()
        try:
            n = _node(conn, ref)
            if n is None or n["type"] != "flow":
                return jsonify(error="Flow node not found"), 404
            now = utc_now()
            p, override, demo = node_profile(conn, ref, now.timestamp(), __import__('clock_service').load_clock(conn, now.timestamp()))
            eff = U.resolve(get_defaults(conn), override)
            _, by, at = _load(conn, f"node:{ref}")
            live = next((x for x in live_all(conn, now) if x["nodeRef"] == ref), None)
            return jsonify(nodeRef=ref, label=_label(n), profile=_public(eff), custom=override is not None, updatedBy=by, updatedAt=at, presets=PRESET_INFO,
                           defaults=_public(get_defaults(conn)), demo=None if demo is None else {"until": demo["until"], "quiet": bool(demo.get("quiet"))}, live=live)
        finally:
            conn.close()

    @app.put("/api/nodes/<int:ref>/usage-profile")
    @auth.require_role("admin")
    def node_usage_put(ref):
        body = request.get_json(silent=True) or {}
        sub = _clean(body)
        with db_session() as conn:
            n = _node(conn, ref)
            if n is None or n["type"] != "flow":
                return jsonify(error="Flow node not found"), 404
            defaults = get_defaults(conn)
            pre = sub.get("preset", "custom")
            full = U.resolve(defaults, {"preset": pre, "fields": {}})
            full.update({k: v for k, v in sub.items() if k != "preset"})
            full["preset"] = pre
            err = U.validate(full)
            if err:
                return jsonify(error=" ".join(err)), 400
            before = U.resolve(defaults, _load(conn, f"node:{ref}")[0])
            _save(conn, f"node:{ref}", U.minimal_override(defaults, full), g.user["email"])
            auth.audit(conn, "usage_node", json.dumps({"node": _label(n), "changed": _diff(before, full)}), user=g.user)
            return jsonify(profile=_public(full))

    @app.delete("/api/nodes/<int:ref>/usage-profile")
    @auth.require_role("admin")
    def node_usage_reset(ref):
        with db_session() as conn:
            n = _node(conn, ref)
            if n is None:
                return jsonify(error="Flow node not found"), 404
            conn.execute("DELETE FROM usage_settings WHERE scope=?", (f"node:{ref}",))
            auth.audit(conn, "usage_node", json.dumps({"node": _label(n), "changed": "reset to the default profile"}), user=g.user)
        return jsonify(ok=True)

    @app.put("/api/nodes/<int:ref>/usage-profile/demo")
    @auth.require_role("admin")
    def node_usage_demo(ref):
        body = request.get_json(silent=True) or {}
        on, quiet = bool(body.get("on")), bool(body.get("quiet"))
        with db_session() as conn:
            n = _node(conn, ref)
            if n is None or n["type"] != "flow":
                return jsonify(error="Flow node not found"), 404
            now = utc_now().timestamp()
            if on:
                _save(conn, f"demo:{ref}", {"from": int(now), "until": int(now) + U.DEMO_MAX_S, "quiet": quiet}, g.user["email"])
            else:
                conn.execute("DELETE FROM usage_settings WHERE scope=?", (f"demo:{ref}",))
            auth.audit(conn, "usage_demo", json.dumps({"node": _label(n), "on": on, "quiet": quiet}), user=g.user)
            return jsonify(ok=True, on=on, until=int(now) + U.DEMO_MAX_S if on else None)
