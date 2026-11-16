"""REST API: pipelines (versioned network JSON), validation, activation, export/import, live zone analysis, usage and leak history.

Reads: any signed-in user.  Writes: administrator + CSRF (auth.before_request).  A saved version is never edited: every save is a new
version; the analysis always runs on the ACTIVE version, and alerts store the version that was active when they were raised.
Invalid designs can be saved as drafts (the editor must not lose work) but only a valid version can become active.
"""
import json
import time
from datetime import datetime, timedelta, timezone

from flask import Response, g, jsonify, request

import analysis_service
import auth
from analysis.zones import partition
from database import db_session, get_db
from ingest import fmt_ts, parse_ts, utc_now
from pipeline import presets
from pipeline.model import KINDS, NODE_KIND, OUTLET_TYPES, SCHEMA, DocError, node_key, parse
from pipeline.validate import is_valid, validate

EMPTY_DOC = {"schema": SCHEMA, "name": "New network", "parts": [], "links": []}
_BUCKETS = {"hour": "%Y-%m-%d %H:00:00", "day": "%Y-%m-%d", "week": "%Y-W%W", "month": "%Y-%m"}


def _preset_of(doc_json) -> str | None:
    try:
        return json.loads(doc_json).get("preset")
    except Exception:
        return None


def _not_for_detection(doc) -> bool:
    """A calibration rig is a test set-up, never a network that leak detection may run on."""
    return isinstance(doc, dict) and doc.get("preset") == "calibration_rig"


def _save_version(conn, pid, doc, note, valid, user):
    n = conn.execute("SELECT COALESCE(MAX(version),0)+1 AS n FROM pipeline_versions WHERE pipeline_id=?", (pid,)).fetchone()["n"]
    conn.execute("INSERT INTO pipeline_versions (pipeline_id,version,doc,note,valid,created_by,created_at) VALUES (?,?,?,?,?,?,?)",
                 (pid, n, json.dumps(doc, separators=(",", ":")), (note or "")[:200], int(valid), user, fmt_ts(utc_now())))
    return n


def store_pipeline(conn, doc: dict, name: str, user: str, note: str = "preset"):
    """Create an INACTIVE pipeline from a document (presets, calibration rigs).  -> (pipeline id, version, valid, messages)."""
    doc = dict(doc, name=name)
    net = parse(doc)
    paired = {(r["type"], r["number"]) for r in conn.execute("SELECT type, number FROM nodes WHERE state='paired'").fetchall()}
    msgs = validate(net, paired)
    pid = conn.execute("INSERT INTO pipelines (name,created_by,created_at) VALUES (?,?,?)", (name[:80], user, fmt_ts(utc_now()))).lastrowid
    v = _save_version(conn, pid, doc, note, is_valid(msgs), user)
    return pid, v, is_valid(msgs), msgs


def register(app, cfg):
    def read():
        c = get_db()
        c.execute("PRAGMA query_only=ON")
        return c

    def paired_keys(conn):
        return {(r["type"], r["number"]) for r in conn.execute("SELECT type, number FROM nodes WHERE state='paired'").fetchall()}

    def check(conn, doc):
        """-> (network or None, messages).  A structurally broken document yields a single error message."""
        try:
            net = parse(doc)
        except DocError as e:
            return None, [{"severity": "error", "code": "bad_document", "partId": None, "message": str(e)}]
        return net, validate(net, paired_keys(conn))

    def pipeline_row(conn, pid):
        return conn.execute("SELECT * FROM pipelines WHERE id=? AND deleted=0", (pid,)).fetchone()

    def version_row(conn, pid, version=None):
        if version is None:
            r = pipeline_row(conn, pid)
            version = r["active_version"] if r and r["active_version"] else None
        if version is None:
            return conn.execute("SELECT * FROM pipeline_versions WHERE pipeline_id=? ORDER BY version DESC LIMIT 1", (pid,)).fetchone()
        return conn.execute("SELECT * FROM pipeline_versions WHERE pipeline_id=? AND version=?", (pid, version)).fetchone()

    save_version = _save_version

    def summary(conn, doc, net, msgs):
        valid = is_valid(msgs)
        out = {"valid": valid, "messages": msgs}
        if net is not None:
            try:
                p = partition(net)
            except Exception:                    # a half-drawn draft may not be analysable: the editor just shows the messages
                return out
            out["partial"] = not valid           # for an invalid draft the zones are a best-effort preview, never used for analysis
            out["coverage"] = p.coverage
            out["zones"] = [{"id": z.id, "name": z.name, "class": z.cls, "reason": z.reason, "equation": z.equation, "inMeter": z.in_meter.label if z.in_meter else None,
                             "inNode": z.in_meter.key[1] if z.in_meter else None, "outNodes": [m.key[1] for m in z.out_meters],
                             "outMeters": [m.label for m in z.out_meters], "outlets": len(z.outlets), "pipes": [x[0] for x in z.pipes], "parts": z.parts,
                             "outletIds": z.outlets, "suggestions": z.suggestions} for z in p.zones]
            out["suggestions"] = p.suggestions
        return out

    def placed_keys(doc):
        return {(p.get("nodeType", "flow"), p.get("number")) for p in (doc.get("parts") or []) if isinstance(p, dict) and p.get("kind") == NODE_KIND}

    @app.get("/api/pipelines/palette")
    def pipeline_palette():
        return jsonify(schema=SCHEMA, kinds=[{"kind": k, "name": v[0], "ports": list(v[1])} for k, v in KINDS.items()], outletTypes=list(OUTLET_TYPES))

    @app.get("/api/pipelines/presets")
    def pipeline_presets():
        return jsonify(presets=presets.preset_list())

    @app.post("/api/pipelines/from-preset")
    @auth.require_role("admin")
    def pipeline_from_preset():
        """Create an INACTIVE pipeline from a preset: {preset, nodeIds (node registry ids, in slot order; fewer = the missing meters are left out), name?}."""
        d = request.get_json(silent=True) or {}
        pre = presets.PRESETS.get(d.get("preset"))
        if pre is None:
            return jsonify(error="Unknown preset."), 400
        ids = d.get("nodeIds") or []
        if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids) or len(set(ids)) != len(ids):
            return jsonify(error="nodeIds must be a list of different node ids."), 400
        if len(ids) > pre["maxNodes"]:
            return jsonify(error=f"{pre['name']} uses at most {pre['maxNodes']} node{'s' if pre['maxNodes'] != 1 else ''}."), 400
        name = str(d.get("name") or pre["name"]).strip()[:80] or pre["name"]
        with db_session() as conn:
            nodes = []
            for i in ids:
                n = conn.execute("SELECT * FROM nodes WHERE id=? AND state='paired' AND type='flow'", (i,)).fetchone()
                if n is None:
                    return jsonify(error="Only paired flow nodes can be placed."), 400
                nodes.append({"number": n["number"], "nodeRef": n["id"], "name": n["name"] or ""})
            try:
                pid, v, valid, msgs = store_pipeline(conn, pre["builder"](nodes), name, g.user["email"], f"preset {d['preset']}")
            except DocError as e:
                return jsonify(error=str(e)), 400
            auth.audit(conn, "pipeline_from_preset", f"id={pid} preset={d['preset']}", user=g.user)
        return jsonify(id=pid, version=v, activeVersion=None, valid=valid, messages=msgs, missingNodes=max(pre["minNodes"] - len(ids), 0)), 201

    @app.get("/api/pipelines")
    def pipelines_list():
        conn = read()
        try:
            rows = conn.execute("SELECT p.*, (SELECT COUNT(*) FROM pipeline_versions v WHERE v.pipeline_id=p.id) AS versions,"
                                " (SELECT MAX(created_at) FROM pipeline_versions v WHERE v.pipeline_id=p.id) AS updated,"
                                " (SELECT doc FROM pipeline_versions v WHERE v.pipeline_id=p.id ORDER BY version DESC LIMIT 1) AS doc FROM pipelines p WHERE p.deleted=0 ORDER BY p.id").fetchall()
            return jsonify(pipelines=[{"id": r["id"], "name": r["name"], "preset": _preset_of(r["doc"]), "activeVersion": r["active_version"], "versions": r["versions"], "updatedAt": r["updated"], "demo": bool(r["demo_until"] and r["demo_until"] > time.time()), "demoUntil": r["demo_until"] if (r["demo_until"] and r["demo_until"] > time.time()) else None} for r in rows])
        finally:
            conn.close()

    @app.post("/api/pipelines")
    @auth.require_role("admin")
    def pipeline_create():
        d = request.get_json(silent=True) or {}
        doc = d.get("doc") or dict(EMPTY_DOC, name=str(d.get("name") or "New network"))
        name = str(d.get("name") or doc.get("name") or "New network")[:80]
        doc["name"] = name
        with db_session() as conn:
            net, msgs = check(conn, doc)
            if net is None:
                return jsonify(error=msgs[0]["message"], messages=msgs), 400
            cur = conn.execute("INSERT INTO pipelines (name,created_by,created_at) VALUES (?,?,?)", (name, g.user["email"], fmt_ts(utc_now())))
            pid = cur.lastrowid
            v = save_version(conn, pid, doc, d.get("note") or "created", is_valid(msgs), g.user["email"])
            active = None
            if is_valid(msgs) and doc.get("parts") and d.get("activate", True) and not _not_for_detection(doc):      # a duplicate is created with activate=false
                _make_exclusive(conn, pid)
                conn.execute("UPDATE pipelines SET active_version=? WHERE id=?", (v, pid))
                active = v
            auth.audit(conn, "pipeline_create", f"id={pid} v={v}", user=g.user)
        return jsonify(id=pid, version=v, activeVersion=active, valid=is_valid(msgs), messages=msgs), 201

    @app.get("/api/pipelines/<int:pid>")
    def pipeline_get(pid):
        conn = read()
        try:
            p = pipeline_row(conn, pid)
            if p is None:
                return jsonify(error="Pipeline not found"), 404
            ver = request.args.get("version", type=int)
            v = version_row(conn, pid, ver)
            if v is None:
                return jsonify(error="Version not found"), 404
            doc = json.loads(v["doc"])
            net, msgs = check(conn, doc)
            placed = placed_keys(doc)
            avail = [{"type": n["type"], "number": n["number"], "name": n["name"], "label": f"Node {n['number']}" + (f" - {n['name']}" if n["name"] else ""), "outOfService": bool(n["out_of_service"]), "nodeRef": n["id"], "gatewayId": n["gateway_id"]}
                     for n in conn.execute("SELECT n.*, g.gateway_id AS gateway_id FROM nodes n JOIN gateways g ON g.id=n.gateway_ref WHERE n.state='paired' AND n.type='flow' ORDER BY g.id, n.number").fetchall() if (n["type"], n["number"]) not in placed]
            return jsonify(id=pid, name=p["name"], activeVersion=p["active_version"], version=v["version"], note=v["note"], savedValid=bool(v["valid"]),
                           createdAt=v["created_at"], createdBy=v["created_by"], doc=doc, availableNodes=avail, **summary(conn, doc, net, msgs))
        finally:
            conn.close()

    @app.get("/api/pipelines/<int:pid>/versions")
    def pipeline_versions(pid):
        conn = read()
        try:
            p = pipeline_row(conn, pid)
            if p is None:
                return jsonify(error="Pipeline not found"), 404
            rows = conn.execute("SELECT version, note, valid, created_by, created_at FROM pipeline_versions WHERE pipeline_id=? ORDER BY version DESC", (pid,)).fetchall()
            return jsonify(activeVersion=p["active_version"], versions=[{"version": r["version"], "note": r["note"], "valid": bool(r["valid"]), "createdBy": r["created_by"],
                                                                         "createdAt": r["created_at"], "active": r["version"] == p["active_version"]} for r in rows])
        finally:
            conn.close()

    @app.put("/api/pipelines/<int:pid>")
    @auth.require_role("admin")
    def pipeline_save(pid):
        d = request.get_json(silent=True) or {}
        doc = d.get("doc")
        if not isinstance(doc, dict):
            return jsonify(error="doc is required"), 400
        with db_session() as conn:
            p = pipeline_row(conn, pid)
            if p is None:
                return jsonify(error="Pipeline not found"), 404
            net, msgs = check(conn, doc)
            if net is None:
                return jsonify(error=msgs[0]["message"], messages=msgs), 400
            valid = is_valid(msgs)
            if d.get("name"):
                doc["name"] = str(d["name"])[:80]
                conn.execute("UPDATE pipelines SET name=? WHERE id=?", (doc["name"], pid))
            v = save_version(conn, pid, doc, d.get("note"), valid, g.user["email"])
            activated = False
            if valid and doc.get("parts") and d.get("activate", True) and not _not_for_detection(doc):
                _make_exclusive(conn, pid)
                conn.execute("UPDATE pipelines SET active_version=? WHERE id=?", (v, pid))
                activated = True
            auth.audit(conn, "pipeline_save", f"id={pid} v={v} valid={valid} active={activated}", user=g.user)
        return jsonify(id=pid, version=v, valid=valid, activated=activated, messages=msgs), 201

    @app.patch("/api/pipelines/<int:pid>")
    @auth.require_role("admin")
    def pipeline_rename(pid):
        """Rename only: no new version, nothing else changes."""
        name = str((request.get_json(silent=True) or {}).get("name") or "").strip()[:80]
        if not name:
            return jsonify(error="A name is required"), 400
        with db_session() as conn:
            if pipeline_row(conn, pid) is None:
                return jsonify(error="Pipeline not found"), 404
            conn.execute("UPDATE pipelines SET name=? WHERE id=?", (name, pid))
            auth.audit(conn, "pipeline_rename", f"id={pid}", user=g.user)
        return jsonify(id=pid, name=name)

    @app.post("/api/pipelines/validate")
    def pipeline_validate():
        d = request.get_json(silent=True) or {}
        doc = d.get("doc")
        if not isinstance(doc, dict):
            return jsonify(error="doc is required"), 400
        conn = read()
        try:
            net, msgs = check(conn, doc)
            return jsonify(**summary(conn, doc, net, msgs))
        finally:
            conn.close()

    def _make_exclusive(conn, pid):
        """Only ONE pipeline analyses the real nodes at a time: activating a pipeline switches the others off (their versions are kept)."""
        others = [r["id"] for r in conn.execute("SELECT id FROM pipelines WHERE deleted=0 AND active_version IS NOT NULL AND id!=?", (pid,))]
        for o in others:
            conn.execute("UPDATE pipelines SET active_version=NULL, demo_until=NULL WHERE id=?", (o,))
        return others

    @app.post("/api/pipelines/<int:pid>/activate")
    @auth.require_role("admin")
    def pipeline_activate(pid):
        ver = (request.get_json(silent=True) or {}).get("version")
        with db_session() as conn:
            if pipeline_row(conn, pid) is None:
                return jsonify(error="Pipeline not found"), 404
            v = version_row(conn, pid, ver)
            if v is None:
                return jsonify(error="Version not found"), 404
            if _not_for_detection(json.loads(v["doc"])):
                return jsonify(error="A calibration rig is a test set-up and cannot be used for leak detection.", code="not_for_detection"), 409
            net, msgs = check(conn, json.loads(v["doc"]))
            if not is_valid(msgs):
                return jsonify(error="This version has validation errors and cannot be activated.", messages=msgs), 409
            off = _make_exclusive(conn, pid)
            conn.execute("UPDATE pipelines SET active_version=? WHERE id=?", (v["version"], pid))
            auth.audit(conn, "pipeline_activate", f"id={pid} v={v['version']} deactivated={','.join(map(str, off)) or '-'}", user=g.user)
        return jsonify(ok=True, activeVersion=v["version"], deactivated=off)

    @app.put("/api/pipelines/<int:pid>/demo")
    @auth.require_role("admin")
    def pipeline_demo(pid):
        """Demo mode: suspect after 1 min, confirm after 3 min for this pipeline; switches itself off after 1 hour.  Alerts raised meanwhile are tagged demo."""
        on = (request.get_json(silent=True) or {}).get("on")
        if not isinstance(on, bool):
            return jsonify(error="on must be true or false"), 400
        with db_session() as conn:
            p = pipeline_row(conn, pid)
            if p is None:
                return jsonify(error="Pipeline not found"), 404
            until = int(utc_now().timestamp()) + analysis_service.DEMO_MAX_S if on else None
            conn.execute("UPDATE pipelines SET demo_until=? WHERE id=?", (until, pid))
            auth.audit(conn, "pipeline_demo_on" if on else "pipeline_demo_off", f"id={pid}", user=g.user)
        return jsonify(ok=True, demo=on, demoUntil=until)

    @app.delete("/api/pipelines/<int:pid>")
    @auth.require_role("admin")
    def pipeline_delete(pid):
        with db_session() as conn:
            if pipeline_row(conn, pid) is None:
                return jsonify(error="Pipeline not found"), 404
            conn.execute("UPDATE pipelines SET deleted=1 WHERE id=?", (pid,))
            auth.audit(conn, "pipeline_delete", f"id={pid}", user=g.user)
        return jsonify(ok=True)

    @app.get("/api/pipelines/<int:pid>/export")
    def pipeline_export(pid):
        conn = read()
        try:
            p = pipeline_row(conn, pid)
            v = version_row(conn, pid, request.args.get("version", type=int)) if p else None
            if v is None:
                return jsonify(error="Pipeline not found"), 404
            body = json.dumps(json.loads(v["doc"]), indent=2)
            return Response(body, mimetype="application/json", headers={"Content-Disposition": f'attachment; filename="pipeline-{pid}-v{v["version"]}.json"'})
        finally:
            conn.close()

    # NOTE: import = POST /api/pipelines with {"doc": <exported JSON>} (the same validation, always a new pipeline)

    # -------------------------------------------------------------- live analysis
    @app.get("/api/pipelines/<int:pid>/status")
    def pipeline_status(pid):
        conn = read()
        try:
            p = pipeline_row(conn, pid)
            v = version_row(conn, pid) if p else None
            if v is None or p["active_version"] is None:
                return jsonify(error="No active version: save a valid design first."), 404
            row = {"id": pid, "name": p["name"], "active_version": p["active_version"], "doc": v["doc"], "demo_until": p["demo_until"]}
            res = analysis_service.compute(conn, row, utc_now(), analysis_service.params_from_env(), persist=False)
            # live flow on each placed node (newest sample) for the Live view
            live = {}
            for pt in json.loads(v["doc"]).get("parts", []):
                if pt.get("kind") != NODE_KIND:
                    continue
                ref = analysis_service.resolve_node_ref(conn, node_key(parse({"schema": SCHEMA, "parts": [pt], "links": []}).nodes[pt["id"]]), pt.get("nodeRef"))
                s = conn.execute("SELECT flow_lpm, measured_at FROM flow_samples WHERE node_ref=? ORDER BY measured_at DESC, id DESC LIMIT 1", (ref,)).fetchone() if ref else None
                fresh = s is not None and (utc_now() - parse_ts(s["measured_at"])).total_seconds() <= cfg.node_offline_s
                live[pt["id"]] = {"flowLpm": s["flow_lpm"] if fresh else None, "measuredAt": s["measured_at"] if s else None, "fresh": bool(fresh)}
            res["live"] = live
            return jsonify(res)
        except DocError as e:
            return jsonify(error=str(e)), 409
        finally:
            conn.close()

    @app.get("/api/pipelines/<int:pid>/zones/<zid>/series")
    def zone_series_api(pid, zid):
        hours = min(max(request.args.get("hours", 6, type=float), 0.1), 72)
        conn = read()
        try:
            cut = int((utc_now() - timedelta(hours=hours)).timestamp())
            rows = conn.execute("SELECT bucket_ts, q_in_l, q_out_l, unaccounted_l, complete, confidence, state FROM zone_buckets WHERE pipeline_id=? AND zone_id=? AND bucket_ts>=? ORDER BY bucket_ts",
                                (pid, zid, cut)).fetchall()
            return jsonify(zone=zid, bucketS=analysis_service.params_from_env().bucket_s,
                           buckets=[{"ts": r["bucket_ts"], "qInL": r["q_in_l"], "qOutL": r["q_out_l"], "unaccountedL": r["unaccounted_l"], "complete": bool(r["complete"]),
                                     "confidence": r["confidence"], "state": r["state"]} for r in rows])
        finally:
            conn.close()

    @app.get("/api/pipelines/<int:pid>/zones/<zid>/usage")
    def zone_usage_api(pid, zid):
        """Zone volumes per hour / day / week / month from the finalised balance buckets: water in, water out (sum of the outflow meters) and the unaccounted part."""
        bucket = request.args.get("bucket", "hour")
        if bucket not in _BUCKETS:
            return jsonify(error=f"bucket must be one of {', '.join(_BUCKETS)}"), 400
        days = min(max(request.args.get("days", 1, type=float), 0.04), 400)
        conn = read()
        try:
            if pipeline_row(conn, pid) is None:
                return jsonify(error="Pipeline not found"), 404
            cut = int((utc_now() - timedelta(days=days)).timestamp())
            rows = conn.execute(f"SELECT strftime('{_BUCKETS[bucket]}', bucket_ts, 'unixepoch') AS b, SUM(q_in_l) AS qin, SUM(q_out_l) AS qout, SUM(unaccounted_l) AS un, "
                                "COUNT(*) AS buckets, SUM(CASE WHEN complete=1 THEN 1 ELSE 0 END) AS good FROM zone_buckets WHERE pipeline_id=? AND zone_id=? AND bucket_ts>=? GROUP BY b ORDER BY b",
                                (pid, zid, cut)).fetchall()
            return jsonify(zone=zid, bucket=bucket, usage=[{"bucket": r["b"], "inLiters": round(r["qin"] or 0.0, 3), "outLiters": round(r["qout"] or 0.0, 3),
                                                              "unaccountedLiters": round(r["un"] or 0.0, 3), "buckets": r["buckets"], "completeBuckets": r["good"]} for r in rows])
        finally:
            conn.close()

    # -------------------------------------------------------------- balance thresholds (x+, x-, T_leak) and the guided no-leak learning test
    def _active_row(conn, pid):
        p = pipeline_row(conn, pid)
        v = version_row(conn, pid) if p else None
        if p is None or v is None or p["active_version"] is None:
            return None
        return {"id": pid, "name": p["name"], "active_version": p["active_version"], "doc": v["doc"], "demo_until": p["demo_until"]}

    def _thresholds_json(conn, row, zid):
        doc = json.loads(row["doc"])
        z = partition(parse(doc)).zone(zid)
        if z is None:
            return None
        base = analysis_service.params_from_env()
        p = analysis_service.params_for(doc, base, False, analysis_service.load_thresholds(conn, row["id"])).for_zone(zid)
        t = conn.execute("SELECT source, updated_at, updated_by FROM zone_thresholds WHERE pipeline_id=? AND zone_id=?", (row["id"], zid)).fetchone()
        return {"zone": zid, "strong": z.cls == "strong", "xPlusLpm": p.x_plus_lpm, "xMinusLpm": p.x_minus_lpm, "tLeakS": p.t_leak_s, "windowS": p.window_s,
                "defaults": {"xPlusLpm": base.x_plus_lpm, "xMinusLpm": base.x_minus_lpm, "tLeakS": base.t_leak_s, "demoTLeakS": analysis_service.DEMO_T_LEAK_S},
                "custom": t is not None, "source": t["source"] if t else None, "updatedAt": t["updated_at"] if t else None, "updatedBy": t["updated_by"] if t else None}

    @app.get("/api/pipelines/<int:pid>/zones/<zid>/thresholds")
    def zone_thresholds_get(pid, zid):
        conn = read()
        try:
            row = _active_row(conn, pid)
            out = _thresholds_json(conn, row, zid) if row else None
            return (jsonify(out), 200) if out else (jsonify(error="Zone not found"), 404)
        except DocError as e:
            return jsonify(error=str(e)), 409
        finally:
            conn.close()

    @app.put("/api/pipelines/<int:pid>/zones/<zid>/thresholds")
    @auth.require_role("admin")
    def zone_thresholds_put(pid, zid):
        b = request.get_json(silent=True) or {}
        vals = {}
        for key, col, lo, hi in (("xPlusLpm", "x_plus_lpm", 0.05, 50.0), ("xMinusLpm", "x_minus_lpm", 0.05, 50.0), ("tLeakS", "t_leak_s", 5.0, 3600.0)):
            if key in b:
                v = b[key]
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
                    return jsonify(error=f"{key} must be a number between {lo:g} and {hi:g}"), 400
                vals[col] = float(v)
        if not vals:
            return jsonify(error="Give xPlusLpm, xMinusLpm and/or tLeakS"), 400
        source = "learned" if b.get("source") == "learned" else "manual"
        with db_session() as conn:
            row = _active_row(conn, pid)
            if row is None:
                return jsonify(error="No active version"), 404
            try:
                analysis_service._strong_zone(json.loads(row["doc"]), zid)
            except DocError as e:
                return jsonify(error=str(e)), 409
            cur = conn.execute("SELECT x_plus_lpm, x_minus_lpm, t_leak_s FROM zone_thresholds WHERE pipeline_id=? AND zone_id=?", (pid, zid)).fetchone()
            merged = {c: vals.get(c, cur[c] if cur else None) for c in ("x_plus_lpm", "x_minus_lpm", "t_leak_s")}
            conn.execute("INSERT INTO zone_thresholds (pipeline_id,zone_id,x_plus_lpm,x_minus_lpm,t_leak_s,source,updated_at,updated_by) VALUES (?,?,?,?,?,?,?,?) "
                         "ON CONFLICT(pipeline_id,zone_id) DO UPDATE SET x_plus_lpm=excluded.x_plus_lpm, x_minus_lpm=excluded.x_minus_lpm, t_leak_s=excluded.t_leak_s, "
                         "source=excluded.source, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                         (pid, zid, merged["x_plus_lpm"], merged["x_minus_lpm"], merged["t_leak_s"], source, fmt_ts(utc_now()), g.user["email"]))
            auth.audit(conn, "zone_thresholds_set", f"pipeline={pid} zone={zid} {source} " + " ".join(f"{k}={v:g}" for k, v in vals.items()), user=g.user)
            return jsonify(_thresholds_json(conn, row, zid))

    @app.delete("/api/pipelines/<int:pid>/zones/<zid>/thresholds")
    @auth.require_role("admin")
    def zone_thresholds_reset(pid, zid):
        with db_session() as conn:
            conn.execute("DELETE FROM zone_thresholds WHERE pipeline_id=? AND zone_id=?", (pid, zid))
            auth.audit(conn, "zone_thresholds_reset", f"pipeline={pid} zone={zid}", user=g.user)
            row = _active_row(conn, pid)
            out = _thresholds_json(conn, row, zid) if row else None
            return (jsonify(out), 200) if out else (jsonify(ok=True), 200)

    def _learn_json(conn, row, zid):
        s = conn.execute("SELECT * FROM threshold_sessions WHERE pipeline_id=? AND zone_id=? ORDER BY id DESC LIMIT 1", (row["id"], zid)).fetchone()
        if s is None:
            return {"session": None}
        out = {"session": {"id": s["id"], "status": s["status"], "startedAt": s["started_at"], "endedAt": s["ended_at"], "createdBy": s["created_by"]}}
        if s["status"] == "running":                                    # live progress: what the run has collected so far
            now = int(utc_now().timestamp())
            out["session"]["progress"] = analysis_service.learn_compute(conn, row, zid, s["started_at"], max(now - 8, s["started_at"] + 5))
        elif s["result_json"]:
            out["session"]["result"] = json.loads(s["result_json"])
        return out

    @app.get("/api/pipelines/<int:pid>/zones/<zid>/learn")
    def zone_learn_get(pid, zid):
        conn = read()
        try:
            row = _active_row(conn, pid)
            return jsonify(_learn_json(conn, row, zid)) if row else (jsonify(error="No active version"), 404)
        except DocError as e:
            return jsonify(error=str(e)), 409
        finally:
            conn.close()

    @app.post("/api/pipelines/<int:pid>/zones/<zid>/learn")
    @auth.require_role("admin")
    def zone_learn_post(pid, zid):
        action = (request.get_json(silent=True) or {}).get("action")
        if action not in ("start", "stop", "cancel"):
            return jsonify(error="action must be start, stop or cancel"), 400
        with db_session() as conn:
            row = _active_row(conn, pid)
            if row is None:
                return jsonify(error="No active version"), 404
            try:
                analysis_service._strong_zone(json.loads(row["doc"]), zid)
                now = int(utc_now().timestamp())
                run = conn.execute("SELECT * FROM threshold_sessions WHERE pipeline_id=? AND zone_id=? AND status='running' ORDER BY id DESC LIMIT 1", (pid, zid)).fetchone()
                if action == "start":
                    if run:
                        conn.execute("UPDATE threshold_sessions SET status='cancelled', ended_at=? WHERE id=?", (now, run["id"]))
                    conn.execute("INSERT INTO threshold_sessions (pipeline_id, zone_id, started_at, created_by) VALUES (?,?,?,?)", (pid, zid, now, g.user["email"]))
                    auth.audit(conn, "zone_learn_start", f"pipeline={pid} zone={zid}", user=g.user)
                elif run is None:
                    return jsonify(error="No learning test is running for this zone"), 409
                elif action == "cancel":
                    conn.execute("UPDATE threshold_sessions SET status='cancelled', ended_at=? WHERE id=?", (now, run["id"]))
                else:
                    res = analysis_service.learn_compute(conn, row, zid, run["started_at"], now - 8)
                    conn.execute("UPDATE threshold_sessions SET status='done', ended_at=?, result_json=? WHERE id=?", (now, json.dumps(res), run["id"]))
                    auth.audit(conn, "zone_learn_stop", f"pipeline={pid} zone={zid} windows={res['windows']}", user=g.user)
            except DocError as e:
                return jsonify(error=str(e)), 409
            return jsonify(_learn_json(conn, row, zid))

    @app.get("/api/pipelines/<int:pid>/leaks")
    def pipeline_leaks(pid):
        conn = read()
        try:
            rows = conn.execute("SELECT id, zone_id, pipeline_version, severity, state, message, active, created_at, cleared_at, updated_at FROM alerts "
                                "WHERE pipeline_id=? AND kind='LEAK' ORDER BY id DESC LIMIT 200", (pid,)).fetchall()
            return jsonify(leaks=[{k: r[k] for k in r.keys()} | {"active": bool(r["active"])} for r in rows])
        finally:
            conn.close()

    @app.get("/api/nodes/<int:ref>/usage")
    def node_usage(ref):
        bucket = request.args.get("bucket", "hour")
        if bucket not in _BUCKETS:
            return jsonify(error=f"bucket must be one of {', '.join(_BUCKETS)}"), 400
        days = min(max(request.args.get("days", 1, type=float), 0.04), 400)
        conn = read()
        try:
            if conn.execute("SELECT 1 FROM nodes WHERE id=?", (ref,)).fetchone() is None:
                return jsonify(error="Node not found"), 404
            since = fmt_ts(utc_now() - timedelta(days=days))
            rows = conn.execute(f"SELECT strftime('{_BUCKETS[bucket]}', measured_at) AS b, SUM(delta_pulses*10.0/pulses_per_liter_x10) AS liters, COUNT(*) AS samples, "
                                "MAX(CASE WHEN measured_src='recv' THEN 1 ELSE 0 END) AS low FROM flow_samples WHERE node_ref=? AND measured_at>=? AND anomaly=0 GROUP BY b ORDER BY b",
                                (ref, since)).fetchall()
            return jsonify(nodeRef=ref, bucket=bucket, usage=[{"bucket": r["b"], "liters": round(r["liters"] or 0.0, 3), "samples": r["samples"], "lowConfidence": bool(r["low"])} for r in rows])
        finally:
            conn.close()
