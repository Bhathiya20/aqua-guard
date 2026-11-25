"""REST API: calibration wizard (sessions, runs, results, apply, per-node history, revert).

A session pauses leak detection for its nodes (see analysis_service.compute) and generates an INACTIVE series pipeline for the diagram.
Volumes come from cumulative pulse counters only (boot_id + cum_pulses), so lost packets never matter.  Reads: any signed-in user;
mutations: administrator + CSRF.  Applying goes through the existing signed calibrate command (works without TLS).
"""
import sqlite3
from datetime import timedelta
from pathlib import Path

from flask import g, jsonify, request

import auth
import calibration as cal
import gateway_v2
import nodes_api
from database import db_session, get_db
from ingest import fmt_ts, parse_ts, utc_now
from pipeline import presets
from pipeline_api import store_pipeline
from pipeline.model import DocError
from protocol_v2 import CommandError, DEV_FLOW

SESSION_IDLE_S = 4 * 3600          # a forgotten session stops pausing leak detection after this long without activity
DEFAULT_PPL_X10 = 4500


def _now():
    return utc_now()


def _epoch(s):
    return parse_ts(s).timestamp()


# ------------------------------------------------------------------------------------------------ helpers shared with the analysis
def paused_refs(conn, now) -> dict:
    """node_ref -> session id for nodes in a RUNNING calibration session that is still active (read-only)."""
    out = {}
    lim = int(now.timestamp()) - SESSION_IDLE_S
    for r in conn.execute("SELECT s.id, n.node_ref FROM calibration_sessions s JOIN calibration_session_nodes n ON n.session_id=s.id "
                          "WHERE s.status='running' AND s.last_activity>=?", (lim,)).fetchall():
        out[r["node_ref"]] = r["id"]
    return out


def expire_sessions(conn, now):
    conn.execute("UPDATE calibration_sessions SET status='cancelled', ended_at=? WHERE status='running' AND last_activity<?",
                 (fmt_ts(now), int(now.timestamp()) - SESSION_IDLE_S))


def record_history(conn, node_ref, user, old_x10, new_x10, source, command_id=None, runs=None, spread=None, session_id=None, method=None, now=None):
    conn.execute("INSERT INTO calibration_history (node_ref,at,user,old_ppl_x10,new_ppl_x10,runs,spread_pct,session_id,source,command_id,method) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (node_ref, fmt_ts(now or _now()), user, old_x10, new_x10, runs, spread, session_id, source, command_id, method))


def _samples(conn, node_ref, since_epoch):
    rows = conn.execute("SELECT boot_id, seq, cum_pulses, measured_at, flow_lpm FROM flow_samples WHERE node_ref=? AND anomaly=0 AND measured_at>=? ORDER BY measured_at, id",
                        (node_ref, fmt_ts(_dt(since_epoch)))).fetchall()
    return [{"boot": r["boot_id"], "seq": r["seq"], "cum": r["cum_pulses"], "measured": _epoch(r["measured_at"]), "flow": r["flow_lpm"]} for r in rows]


def _dt(epoch):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc)


def _label(n):
    return f"{'Rain' if n['type'] == 'rain' else 'Node'} {n['number']}"


# ------------------------------------------------------------------------------------------------ session state machine
def _session(conn, sid):
    return conn.execute("SELECT * FROM calibration_sessions WHERE id=?", (sid,)).fetchone()


def _session_nodes(conn, sid):
    return conn.execute("SELECT n.*, sn.pos AS pos, sn.old_ppl_x10 AS old_ppl_x10 FROM calibration_session_nodes sn JOIN nodes n ON n.id=sn.node_ref "
                        "WHERE sn.session_id=? ORDER BY sn.pos", (sid,)).fetchall()


def settle(conn, sid, now):
    """Advance the runs of a session: detect reboots, finish 'stopping' runs when every node is idle, time out silent nodes."""
    now_e = now.timestamp()
    for run in conn.execute("SELECT * FROM calibration_runs WHERE session_id=? AND status IN ('active','stopping') ORDER BY idx", (sid,)).fetchall():
        started = _epoch(run["started_at"])
        stop_e = _epoch(run["stop_requested_at"]) if run["stop_requested_at"] else None
        rows = conn.execute("SELECT rn.*, n.number, n.type FROM calibration_run_nodes rn JOIN nodes n ON n.id=rn.node_ref WHERE rn.run_id=?", (run["id"],)).fetchall()
        reboot = None
        pending = False
        for rn in rows:
            if not rn["valid"] or rn["cum_end"] is not None:
                continue
            smp = _samples(conn, rn["node_ref"], started - 1)
            if cal.rebooted(smp, rn["boot_id"], started):
                reboot = _label(rn)
                break
            if run["status"] != "stopping":
                continue
            pair = cal.idle_after(smp, rn["boot_id"], stop_e)
            if pair is not None:
                if pair["cum"] < rn["cum_start"]:
                    conn.execute("UPDATE calibration_run_nodes SET valid=0, reason=? WHERE run_id=? AND node_ref=?", ("the pulse counter went backwards", run["id"], rn["node_ref"]))
                else:
                    conn.execute("UPDATE calibration_run_nodes SET cum_end=?, pulses=? WHERE run_id=? AND node_ref=?", (pair["cum"], pair["cum"] - rn["cum_start"], run["id"], rn["node_ref"]))
            elif now_e - stop_e > cal.STOP_TIMEOUT_S:
                conn.execute("UPDATE calibration_run_nodes SET valid=0, reason=? WHERE run_id=? AND node_ref=?",
                             (f"{_label(rn)} sent no new readings with a steady counter within {cal.STOP_TIMEOUT_S} s - this run is invalid for this node.", run["id"], rn["node_ref"]))
            else:
                pending = True
        if reboot:
            conn.execute("UPDATE calibration_runs SET status='invalid', invalid_reason=?, stopped_at=? WHERE id=?",
                         (f"{reboot} restarted during the run (its pulse counter started again from 0). Delete this run and repeat it.", fmt_ts(now), run["id"]))
            conn.execute("UPDATE calibration_run_nodes SET valid=0, reason='node restarted' WHERE run_id=?", (run["id"],))
        elif run["status"] == "stopping" and not pending:
            left = conn.execute("SELECT COUNT(*) AS c FROM calibration_run_nodes WHERE run_id=? AND valid=1", (run["id"],)).fetchone()["c"]
            if left:
                conn.execute("UPDATE calibration_runs SET status='done', stopped_at=? WHERE id=?", (fmt_ts(now), run["id"]))
            else:
                why = conn.execute("SELECT reason FROM calibration_run_nodes WHERE run_id=? AND reason IS NOT NULL LIMIT 1", (run["id"],)).fetchone()
                conn.execute("UPDATE calibration_runs SET status='invalid', invalid_reason=?, stopped_at=? WHERE id=?", (why["reason"] if why else "no valid data", fmt_ts(now), run["id"]))


def session_json(conn, sid, now, cfg):
    s = _session(conn, sid)
    if s is None:
        return None
    settle(conn, sid, now)
    nodes = _session_nodes(conn, sid)
    now_e = now.timestamp()
    out_nodes = []
    active = conn.execute("SELECT * FROM calibration_runs WHERE session_id=? AND status IN ('active','stopping') LIMIT 1", (sid,)).fetchone()
    for n in nodes:
        smp = _samples(conn, n["id"], now_e - 120)
        ok, why = cal.idle_now(smp, now_e)
        newest = max(smp, key=lambda x: (x["measured"], x["seq"])) if smp else None
        age = None if newest is None else max(0.0, now_e - newest["measured"])
        in_run = None
        if active:
            rn = conn.execute("SELECT * FROM calibration_run_nodes WHERE run_id=? AND node_ref=?", (active["id"], n["id"])).fetchone()
            if rn and newest is not None and newest["boot"] == rn["boot_id"]:
                in_run = newest["cum"] - rn["cum_start"]
        out_nodes.append({"id": n["id"], "number": n["number"], "label": _label(n), "name": n["name"], "order": n["pos"],
                          "currentPpl": (n["ppl_x10"] or DEFAULT_PPL_X10) / 10.0, "isDefault": not n["ppl_x10"], "outOfService": bool(n["out_of_service"]),
                          "idle": {"ok": ok, "reason": why},
                          "live": {"flowLpm": round(newest["flow"], 2) if newest and age is not None and age <= cfg.node_offline_s else None,
                                   "cumPulses": newest["cum"] if newest else None, "ageS": None if age is None else round(age, 1)},
                          "pulsesInRun": in_run})
    labels = {n["id"]: n["label"] for n in out_nodes}
    runs_in = []
    runs_out = []
    for r in conn.execute("SELECT * FROM calibration_runs WHERE session_id=? ORDER BY idx", (sid,)).fetchall():
        rn = conn.execute("SELECT * FROM calibration_run_nodes WHERE run_id=?", (r["id"],)).fetchall()
        dur = None
        if r["stop_requested_at"]:
            dur = max(0.0, _epoch(r["stop_requested_at"]) - _epoch(r["started_at"]))
        cells = {x["node_ref"]: {"pulses": x["pulses"], "valid": bool(x["valid"]), "reason": x["reason"]} for x in rn}
        runs_in.append({"id": r["id"], "idx": r["idx"], "status": r["status"], "volumeL": r["volume_l"], "durationS": dur, "reason": r["invalid_reason"], "nodes": cells})
        runs_out.append({"id": r["id"], "idx": r["idx"], "status": r["status"], "volumeL": r["volume_l"], "durationS": dur, "reason": r["invalid_reason"],
                         "startedAt": r["started_at"], "stopRequestedAt": r["stop_requested_at"], "stoppedAt": r["stopped_at"],
                         "waitingFor": [labels[x["node_ref"]] for x in rn if x["valid"] and x["cum_end"] is None and r["status"] == "stopping"],
                         "cells": {str(k): v for k, v in cells.items()}})
    res = cal.summarize([{"id": n["id"], "label": n["label"]} for n in out_nodes], runs_in)
    for r in res["runs"]:
        r["nodes"] = {str(k): v for k, v in r["nodes"].items()}
    proposals = {}
    for st in res["nodes"]:
        n = next(x for x in out_nodes if x["id"] == st["id"])
        d = {}
        for m in ("weighted", "median"):
            v = cal.proposed_value(st, m)
            verdict, msg = cal.check_apply(v, n["currentPpl"])
            d[m] = {"value": None if v is None else cal.ppl_x10(v) / 10.0, "verdict": verdict, "message": msg}
        proposals[str(st["id"])] = d
    res["nodes"] = [dict(st, current=next(x for x in out_nodes if x["id"] == st["id"])["currentPpl"]) for st in res["nodes"]]
    canstart = s["status"] == "running" and active is None and all(n["idle"]["ok"] for n in out_nodes)
    return {"id": s["id"], "status": s["status"], "createdAt": s["created_at"], "endedAt": s["ended_at"], "pipelineId": s["pipeline_id"], "nodes": out_nodes,
            "runs": runs_out, "results": res, "proposals": proposals, "activeRunId": active["id"] if active else None, "canStart": canstart,
            "recommended": {"minRuns": cal.RECOMMENDED_RUNS[0], "maxRuns": cal.RECOMMENDED_RUNS[1], "minLiters": cal.MIN_RUN_L},
            "limits": {"min": cal.PPL_MIN, "max": cal.PPL_MAX, "confirmPct": cal.CONFIRM_DIFF * 100}}


# ------------------------------------------------------------------------------------------------ routes
def register(app, cfg):
    def fail(code, message, http=409, **extra):
        return jsonify(error=message, code=code, **extra), http

    def touch(conn, sid):
        if g.user and g.user["role"] == "admin":
            conn.execute("UPDATE calibration_sessions SET last_activity=? WHERE id=? AND status='running'", (int(_now().timestamp()), sid))

    @app.get("/api/calibration/active")
    def cal_active():
        now = _now()
        with db_session() as conn:
            expire_sessions(conn, now)
            ids = paused_refs(conn, now)
            if not ids:
                return jsonify(active=False)
            sid = next(iter(ids.values()))
            nodes = [{"id": n["id"], "label": _label(n)} for n in _session_nodes(conn, sid)]
            return jsonify(active=True, sessionId=sid, nodes=nodes)

    @app.get("/api/calibration/sessions")
    def cal_list():
        with db_session() as conn:
            rows = conn.execute("SELECT * FROM calibration_sessions ORDER BY id DESC LIMIT 20").fetchall()
            return jsonify(sessions=[{"id": r["id"], "status": r["status"], "createdAt": r["created_at"], "endedAt": r["ended_at"], "createdBy": r["created_by"],
                                      "nodes": [_label(n) for n in _session_nodes(conn, r["id"])]} for r in rows])

    @app.post("/api/calibration/sessions")
    @auth.require_role("admin")
    def cal_create():
        d = request.get_json(silent=True) or {}
        ids = d.get("nodeIds")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, int) for i in ids) or len(set(ids)) != len(ids) or len(ids) > 8:
            return fail("bad_request", "Choose 1-8 different flow nodes.", 400)
        now = _now()
        with db_session() as conn:
            expire_sessions(conn, now)
            if paused_refs(conn, now):
                return fail("session_running", "A calibration session is already running. Finish or cancel it first.")
            nodes = []
            for i in ids:
                n = conn.execute("SELECT * FROM nodes WHERE id=? AND state='paired' AND type='flow'", (i,)).fetchone()
                if n is None:
                    return fail("bad_node", "Only paired flow nodes can be calibrated.", 400)
                if n["out_of_service"]:
                    return fail("out_of_service", f"{_label(n)} is out of service.")
                nodes.append(n)
            doc = presets.calibration_rig([{"number": n["number"], "nodeRef": n["id"], "name": n["name"] or ""} for n in nodes], "Calibration rig")
            stamp = now.strftime("%Y-%m-%d %H:%M")
            try:
                pid, _v, _valid, _msgs = store_pipeline(conn, doc, f"Calibration rig {stamp}", g.user["email"], "calibration rig")
            except DocError as e:
                return fail("bad_rig", str(e), 400)
            sid = conn.execute("INSERT INTO calibration_sessions (created_by,created_at,last_activity,pipeline_id) VALUES (?,?,?,?)",
                               (g.user["email"], fmt_ts(now), int(now.timestamp()), pid)).lastrowid
            for pos, n in enumerate(nodes):
                conn.execute("INSERT INTO calibration_session_nodes (session_id,node_ref,pos,old_ppl_x10) VALUES (?,?,?,?)", (sid, n["id"], pos, n["ppl_x10"]))
            auth.audit(conn, "calibration_start", f"session={sid} nodes={','.join(str(i) for i in ids)}", user=g.user)
            import services
            services.evaluate_alerts(conn, cfg)
            import analysis_service
            analysis_service.run_analysis(conn, cfg, now)           # the zones pause at once (and a running leak event is closed)
            return jsonify(session_json(conn, sid, now, cfg)), 201

    @app.get("/api/calibration/sessions/<int:sid>")
    def cal_get(sid):
        now = _now()
        with db_session() as conn:
            expire_sessions(conn, now)
            if _session(conn, sid) is None:
                return fail("not_found", "Session not found.", 404)
            touch(conn, sid)
            return jsonify(session_json(conn, sid, now, cfg))

    def _running(conn, sid):
        s = _session(conn, sid)
        if s is None:
            return None, fail("not_found", "Session not found.", 404)
        if s["status"] != "running":
            return None, fail("session_ended", "This calibration session has ended.")
        return s, None

    @app.post("/api/calibration/sessions/<int:sid>/runs")
    @auth.require_role("admin")
    def cal_start(sid):
        now = _now()
        with db_session() as conn:
            _s, err = _running(conn, sid)
            if err:
                return err
            settle(conn, sid, now)
            if conn.execute("SELECT 1 FROM calibration_runs WHERE session_id=? AND status IN ('active','stopping')", (sid,)).fetchone():
                return fail("run_open", "A run is already in progress.")
            starts, busy = [], []
            for n in _session_nodes(conn, sid):
                smp = _samples(conn, n["id"], now.timestamp() - 120)
                ok, why = cal.idle_now(smp, now.timestamp())
                if not ok:
                    busy.append({"nodeId": n["id"], "label": _label(n), "reason": why})
                    continue
                newest = max(smp, key=lambda x: (x["measured"], x["seq"]))
                starts.append((n["id"], newest["boot"], newest["cum"]))
            if busy:
                return fail("not_idle", "Start is only possible when every node is idle (no water flowing and recent readings): " +
                            ", ".join(f"{b['label']} ({b['reason']})" for b in busy), nodes=busy)
            idx = conn.execute("SELECT COALESCE(MAX(idx),0)+1 AS i FROM calibration_runs WHERE session_id=?", (sid,)).fetchone()["i"]
            rid = conn.execute("INSERT INTO calibration_runs (session_id,idx,started_at,status) VALUES (?,?,?, 'active')", (sid, idx, fmt_ts(now))).lastrowid
            for ref, boot, cum in starts:
                conn.execute("INSERT INTO calibration_run_nodes (run_id,node_ref,boot_id,cum_start) VALUES (?,?,?,?)", (rid, ref, boot, cum))
            touch(conn, sid)
            return jsonify(session_json(conn, sid, now, cfg)), 201

    @app.post("/api/calibration/sessions/<int:sid>/runs/<int:rid>/stop")
    @auth.require_role("admin")
    def cal_stop(sid, rid):
        now = _now()
        with db_session() as conn:
            _s, err = _running(conn, sid)
            if err:
                return err
            r = conn.execute("SELECT * FROM calibration_runs WHERE id=? AND session_id=?", (rid, sid)).fetchone()
            if r is None:
                return fail("not_found", "Run not found.", 404)
            if r["status"] != "active":
                return fail("bad_state", "This run is not in progress.")
            conn.execute("UPDATE calibration_runs SET status='stopping', stop_requested_at=? WHERE id=?", (fmt_ts(now), rid))
            touch(conn, sid)
            return jsonify(session_json(conn, sid, now, cfg))

    @app.put("/api/calibration/sessions/<int:sid>/runs/<int:rid>")
    @auth.require_role("admin")
    def cal_volume(sid, rid):
        d = request.get_json(silent=True) or {}
        v = d.get("volumeL")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= 1000:
            return fail("bad_volume", "Enter the measured volume in litres (more than 0).", 400)
        now = _now()
        with db_session() as conn:
            s = _session(conn, sid)
            if s is None:
                return fail("not_found", "Session not found.", 404)
            r = conn.execute("SELECT * FROM calibration_runs WHERE id=? AND session_id=?", (rid, sid)).fetchone()
            if r is None:
                return fail("not_found", "Run not found.", 404)
            if r["status"] != "done":
                return fail("bad_state", "The volume can be entered once the run has finished (and is valid).")
            if s["status"] == "cancelled":
                return fail("session_ended", "This calibration session was cancelled.")
            conn.execute("UPDATE calibration_runs SET volume_l=? WHERE id=?", (float(v), rid))
            touch(conn, sid)
            return jsonify(session_json(conn, sid, now, cfg))

    @app.delete("/api/calibration/sessions/<int:sid>/runs/<int:rid>")
    @auth.require_role("admin")
    def cal_delete_run(sid, rid):
        now = _now()
        with db_session() as conn:
            s, err = _running(conn, sid)
            if err:
                return err
            if conn.execute("SELECT 1 FROM calibration_runs WHERE id=? AND session_id=?", (rid, sid)).fetchone() is None:
                return fail("not_found", "Run not found.", 404)
            conn.execute("DELETE FROM calibration_run_nodes WHERE run_id=?", (rid,))
            conn.execute("DELETE FROM calibration_runs WHERE id=?", (rid,))
            touch(conn, sid)
            return jsonify(session_json(conn, sid, now, cfg))

    @app.post("/api/calibration/sessions/<int:sid>/cancel")
    @auth.require_role("admin")
    def cal_cancel(sid):
        now = _now()
        with db_session() as conn:
            s = _session(conn, sid)
            if s is None:
                return fail("not_found", "Session not found.", 404)
            if s["status"] == "running":
                conn.execute("UPDATE calibration_sessions SET status='cancelled', ended_at=? WHERE id=?", (fmt_ts(now), sid))
                auth.audit(conn, "calibration_cancel", f"session={sid}", user=g.user)
            return jsonify(session_json(conn, sid, now, cfg))

    @app.post("/api/calibration/sessions/<int:sid>/apply")
    @auth.require_role("admin")
    def cal_apply(sid):
        d = request.get_json(silent=True) or {}
        method = d.get("method", "weighted")
        if method not in ("weighted", "median"):
            return fail("bad_request", "method must be 'weighted' or 'median'.", 400)
        now = _now()
        plan = []
        with db_session() as conn:                                  # phase 1: read and decide (send_command opens its own transaction, so none may be open then)
            s = _session(conn, sid)
            if s is None:
                return fail("not_found", "Session not found.", 404)
            if s["status"] == "cancelled":
                return fail("session_ended", "This calibration session was cancelled.")
            data = session_json(conn, sid, now, cfg)
            want = d.get("nodeIds")
            nodes = [n for n in data["nodes"] if want is None or n["id"] in want]
            if not nodes:
                return fail("bad_request", "No nodes selected.", 400)
            refused = [{"nodeId": n["id"], "label": n["label"], "message": data["proposals"][str(n["id"])][method]["message"] or "no valid run"} for n in nodes
                       if data["proposals"][str(n["id"])][method]["verdict"] == "refuse"]
            if refused:
                return fail("refused", "Not applied: " + "; ".join(f"{x['label']}: {x['message']}" for x in refused), 422, nodes=refused)
            confirm = [{"nodeId": n["id"], "label": n["label"], "message": data["proposals"][str(n["id"])][method]["message"]} for n in nodes
                       if data["proposals"][str(n["id"])][method]["verdict"] == "confirm"]
            if confirm and d.get("confirm") is not True:
                return fail("needs_confirm", "A value differs more than 15 % from the current one: confirm to apply. " + " ".join(x["message"] for x in confirm), 409, nodes=confirm)
            stats = {x["id"]: x for x in data["results"]["nodes"]}
            for n in nodes:
                row = conn.execute("SELECT * FROM nodes WHERE id=?", (n["id"],)).fetchone()
                gw = conn.execute("SELECT * FROM gateways WHERE id=?", (row["gateway_ref"],)).fetchone()
                plan.append({"n": n, "row": row, "gw": gw, "new": cal.ppl_x10(data["proposals"][str(n["id"])][method]["value"]), "stat": stats[n["id"]]})
            status0 = s["status"]
        results = []
        for p in plan:                                              # phase 2: send
            n, row = p["n"], p["row"]
            flags = gateway_v2.TX_BITS.get(row["tx_dbm_x10"] or 85, 0x03)           # a calibration change keeps the node's TX level
            try:
                cid = nodes_api.send_command(cfg, p["gw"], "C", DEV_FLOW, row["number"], ppl_x10=p["new"], flags=flags, user=g.user["email"])
            except nodes_api.Refused as e:
                results.append({"nodeId": n["id"], "label": n["label"], "commandId": None, "error": e.message, "code": e.code})
                continue
            except CommandError as e:
                results.append({"nodeId": n["id"], "label": n["label"], "commandId": None, "error": str(e), "code": "command_error"})
                continue
            results.append({"nodeId": n["id"], "label": n["label"], "commandId": cid, "valueX10": p["new"], "error": None})
        sent = [r for r in results if r["commandId"]]
        with db_session() as conn:                                  # phase 3: record
            by = {p["n"]["id"]: p for p in plan}
            for r in sent:
                p = by[r["nodeId"]]
                record_history(conn, r["nodeId"], g.user["email"], p["row"]["ppl_x10"] or DEFAULT_PPL_X10, p["new"], "wizard", r["commandId"], p["stat"]["runs"], p["stat"]["spreadPct"], sid, method, now)
            if sent:
                if status0 == "running":                             # leak detection resumes once the result has been sent
                    conn.execute("UPDATE calibration_sessions SET status='done', ended_at=?, method=? WHERE id=?", (fmt_ts(now), method, sid))
                auth.audit(conn, "calibration_apply", f"session={sid} method={method}", user=g.user)
        return jsonify(ok=True, results=results, status="done" if sent else status0)

    @app.get("/api/nodes/<int:ref>/calibrations")
    def cal_history(ref):
        with db_session() as conn:
            if conn.execute("SELECT 1 FROM nodes WHERE id=?", (ref,)).fetchone() is None:
                return fail("not_found", "Node not found.", 404)
            rows = conn.execute("SELECT h.*, c.status AS cmd_status FROM calibration_history h LEFT JOIN gateway_commands c ON c.id=h.command_id "
                                "WHERE h.node_ref=? ORDER BY h.id DESC LIMIT 50", (ref,)).fetchall()
            return jsonify(history=[{"id": r["id"], "at": r["at"], "user": r["user"], "oldPpl": (r["old_ppl_x10"] or DEFAULT_PPL_X10) / 10.0, "newPpl": r["new_ppl_x10"] / 10.0,
                                     "runs": r["runs"], "spreadPct": r["spread_pct"], "source": r["source"], "method": r["method"], "sessionId": r["session_id"],
                                     "commandStatus": r["cmd_status"]} for r in rows])

    @app.post("/api/nodes/<int:ref>/recalculate-history")
    @auth.require_role("admin")
    def cal_recalculate(ref):
        """Admin option: re-express the node's STORED history with one pulses-per-litre value.  The raw pulse counts are never changed; every sample's
        pulses_per_liter and its flow rate (flow = pulses / ppl) are rewritten, so volumes become delta_pulses / new ppl.  The database is backed up first."""
        body = request.get_json(silent=True) or {}
        now = _now()
        rd = get_db()
        try:
            n = rd.execute("SELECT id, number, ppl_x10 FROM nodes WHERE id=? AND type='flow' AND state!='removed'", (ref,)).fetchone()
            if n is None:
                return fail("not_found", "Flow node not found.", 404)
            last = rd.execute("SELECT new_ppl_x10 FROM calibration_history WHERE node_ref=? ORDER BY id DESC LIMIT 1", (ref,)).fetchone()
            try:
                target = int(round(float(body["ppl"]) * 10)) if body.get("ppl") not in (None, "") else (last["new_ppl_x10"] if last else n["ppl_x10"])
            except (TypeError, ValueError):
                return fail("bad_calibration", "Pulses per litre must be a number.", 400)
            if not target or not 100 <= target <= 60000:
                return fail("bad_calibration", "That value is outside the allowed range (10 to 6000 pulses per litre).", 400)
            bdir = Path(cfg.db_path).parent / "backups"
            bdir.mkdir(parents=True, exist_ok=True)
            bfile = bdir / f"aquaguard-before-recalc-node{n['number']}-{now.strftime('%Y%m%d-%H%M%S')}.db"
            dst = sqlite3.connect(str(bfile))
            try:
                rd.backup(dst)                                                     # consistent copy of the whole database, before anything is changed
            finally:
                dst.close()
        finally:
            rd.close()
        with db_session() as conn:
            todo = conn.execute("SELECT COUNT(*) c FROM flow_samples WHERE node_ref=? AND pulses_per_liter_x10<>?", (ref, target)).fetchone()["c"]
            conn.execute("UPDATE flow_samples SET flow_lpm=MIN(flow_lpm*pulses_per_liter_x10*1.0/?, 120.0), pulses_per_liter_x10=? WHERE node_ref=? AND pulses_per_liter_x10<>?",
                         (target, target, ref, target))
            conn.execute("UPDATE calibration_history SET recalculated_at=? WHERE node_ref=?", (fmt_ts(now), ref))
            auth.audit(conn, "calibration_recalculate", f"node={n['number']} ppl={target / 10.0} samples={todo} backup={bfile.name}", user=g.user)
        return jsonify(ok=True, samples=todo, ppl=target / 10.0, backup=bfile.name)

    @app.post("/api/nodes/<int:ref>/calibrations/<int:hid>/revert")
    @auth.require_role("admin")
    def cal_revert(ref, hid):
        now = _now()
        with db_session() as conn:
            h = conn.execute("SELECT * FROM calibration_history WHERE id=? AND node_ref=?", (hid, ref)).fetchone()
            n = conn.execute("SELECT * FROM nodes WHERE id=? AND state!='removed'", (ref,)).fetchone()
            if h is None or n is None:
                return fail("not_found", "Entry not found.", 404)
            target = h["old_ppl_x10"] or DEFAULT_PPL_X10
            if not 100 <= target <= 60000:
                return fail("bad_calibration", "That value is outside the allowed range.", 400)
            gw = conn.execute("SELECT * FROM gateways WHERE id=?", (n["gateway_ref"],)).fetchone()
        flags = gateway_v2.TX_BITS.get(n["tx_dbm_x10"] or 85, 0x03)
        try:
            cid = nodes_api.send_command(cfg, gw, "C", DEV_FLOW, n["number"], ppl_x10=target, flags=flags, user=g.user["email"])
        except nodes_api.Refused as e:
            return fail(e.code, e.message, e.http)
        with db_session() as conn:
            record_history(conn, ref, g.user["email"], n["ppl_x10"] or DEFAULT_PPL_X10, target, "revert", cid, now=now)
        return jsonify(commandId=cid, status="sent", number=n["number"]), 202
