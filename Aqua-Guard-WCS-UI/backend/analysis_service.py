"""Database adapter around the pure analysis package: load buckets, evaluate the active pipelines, persist zone state and alerts.

One alert per leak EVENT: it is opened when a zone leaves 'normal', updated in place when it goes suspected -> confirmed, and cleared when
the zone is back to normal; the active-alert unique index plus the explicit lookup below make duplicates impossible.  Every alert stores
the pipeline id and the VERSION that was active when it was raised.
"""
import json
import os
from datetime import datetime, timezone

from bisect import bisect_left

from analysis.balance import fast_series, final_fast
from analysis.buckets import Sample, fine_windows, node_buckets
from analysis.rules import LEVEL_NAME, Evaluation, ZoneParams, evaluate_zone, final_only, zone_series
from analysis.zones import NONE, STRONG, partition
from ingest import fmt_ts, parse_ts
from pipeline.model import DocError, node_key, parse

SETTING_FIELDS = ("abs_min_lpm", "tolerance", "suspect_min", "confirm_min", "resolve_min", "night_start_min", "night_end_min",
                  "continuous_suspect_h", "continuous_confirm_h", "continuous_min_lpm", "x_plus_lpm", "x_minus_lpm", "t_leak_s")
THRESHOLD_FIELDS = ("x_plus_lpm", "x_minus_lpm", "t_leak_s")


def _hhmm(s, default):
    try:
        h, m = s.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return default


SITE_TZ = "Asia/Colombo"


def site_offset_min(now: datetime | None = None) -> int:
    """Minutes east of UTC of the SITE (Asia/Colombo, +05:30, no DST), independent of the server's own time zone.  AQUAGUARD_TZ_OFFSET_MIN overrides."""
    try:
        from zoneinfo import ZoneInfo
        off = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(SITE_TZ)).utcoffset()
        return int(off.total_seconds() // 60)
    except Exception:
        return 330                                       # tzdata missing (some Windows installs): Colombo has been +05:30 since 2006


DEMO_SUSPECT_MIN, DEMO_CONFIRM_MIN, DEMO_MAX_S = 1, 3, 3600
DEMO_T_LEAK_S = 10                                       # demo mode: D above x+ for 10 s = possible leak (normally 60 s)


def demo_active(row, now_s: int) -> bool:
    try:
        until = row["demo_until"]
    except (IndexError, KeyError):
        return False
    return bool(until and until > now_s)


def params_from_env() -> ZoneParams:
    e = os.environ
    p = ZoneParams()
    p.tz_offset_min = int(e["AQUAGUARD_TZ_OFFSET_MIN"]) if "AQUAGUARD_TZ_OFFSET_MIN" in e else site_offset_min()
    for env, attr, cast in (("AQUAGUARD_AN_ABS_MIN_LPM", "abs_min_lpm", float), ("AQUAGUARD_AN_TOLERANCE", "tolerance", float),
                            ("AQUAGUARD_AN_SUSPECT_MIN", "suspect_min", float), ("AQUAGUARD_AN_CONFIRM_MIN", "confirm_min", float),
                            ("AQUAGUARD_AN_BUCKET_S", "bucket_s", int), ("AQUAGUARD_AN_GRACE_S", "grace_s", int)):
        if env in e:
            try:
                setattr(p, attr, cast(e[env]))
            except ValueError:
                pass
    p.night_start_min = _hhmm(e.get("AQUAGUARD_AN_NIGHT_START"), p.night_start_min)
    p.night_end_min = _hhmm(e.get("AQUAGUARD_AN_NIGHT_END"), p.night_end_min)
    return p


def load_thresholds(conn, pipeline_id: int) -> dict:
    """Per-zone x+ / x- / T_leak chosen on the Leak Detection page (typed in, or learned from a no-leak test and accepted)."""
    out = {}
    for r in conn.execute("SELECT zone_id, x_plus_lpm, x_minus_lpm, t_leak_s FROM zone_thresholds WHERE pipeline_id=?", (pipeline_id,)).fetchall():
        out[r["zone_id"]] = {k: r[k] for k in THRESHOLD_FIELDS if r[k] is not None}
    return out


def params_for(doc: dict, base: ZoneParams, demo: bool = False, thresholds: dict | None = None) -> ZoneParams:
    """Per-zone threshold/duration overrides: the versioned document ("zoneSettings", part of the saved configuration) first, then the thresholds table."""
    zs = doc.get("zoneSettings") if isinstance(doc, dict) else None
    p = ZoneParams(**{k: getattr(base, k) for k in base.__dataclass_fields__})
    if isinstance(zs, dict):
        p.overrides = {z: {k: v for k, v in s.items() if k in SETTING_FIELDS and isinstance(v, (int, float)) and not isinstance(v, bool)}
                       for z, s in zs.items() if isinstance(s, dict)}
    for z, vals in (thresholds or {}).items():
        p.overrides.setdefault(z, {}).update(vals)
    if demo:                                             # demo mode: a short water test shows a leak in seconds / minutes (every zone, balance rule only)
        p.suspect_min, p.confirm_min, p.t_leak_s = DEMO_SUSPECT_MIN, DEMO_CONFIRM_MIN, DEMO_T_LEAK_S
        for z in list(p.overrides):
            p.overrides[z] = {k: v for k, v in p.overrides[z].items() if k not in ("suspect_min", "confirm_min", "t_leak_s")}
    return p


def active_pipelines(conn):
    return conn.execute("SELECT p.id, p.name, p.active_version, p.demo_until, v.doc FROM pipelines p JOIN pipeline_versions v "
                        "ON v.pipeline_id=p.id AND v.version=p.active_version WHERE p.deleted=0 AND p.active_version IS NOT NULL").fetchall()


def resolve_node_ref(conn, key, node_ref):
    if node_ref and conn.execute("SELECT 1 FROM nodes WHERE id=? AND type=?", (node_ref, key[0])).fetchone():
        return node_ref
    r = conn.execute("SELECT id FROM nodes WHERE type=? AND number=? AND state!='removed' ORDER BY id LIMIT 2", (key[0], key[1])).fetchall()
    return r[0]["id"] if len(r) == 1 else None            # ambiguous (two gateways) or unknown: no data, never a guess


def load_samples(conn, node_ref, t0: int, t1: int):
    rows = conn.execute("SELECT measured_at, delta_pulses, pulses_per_liter_x10, gap_before, measured_src, boot_id FROM flow_samples "
                        "WHERE node_ref=? AND measured_at>=? AND measured_at<=? AND anomaly=0 ORDER BY measured_at",
                        (node_ref, fmt_ts(datetime.fromtimestamp(t0, timezone.utc)), fmt_ts(datetime.fromtimestamp(t1, timezone.utc)))).fetchall()
    return [Sample(int(parse_ts(r["measured_at"]).timestamp()), r["delta_pulses"] * 10.0 / max(r["pulses_per_liter_x10"], 1), r["gap_before"] or 0,
                   r["measured_src"] or "legacy", r["boot_id"]) for r in rows]


def _node_fresh(conn, ref, now: datetime) -> bool:
    """True when the node reported within the node-offline window (a node that never reported is not fresh)."""
    r = conn.execute("SELECT last_seen FROM nodes WHERE id=?", (ref,)).fetchone()
    return bool(r and r["last_seen"] and (now - parse_ts(r["last_seen"])).total_seconds() <= cfg_node_offline_s())


def _node_oos(conn, ref) -> bool:
    r = conn.execute("SELECT out_of_service FROM nodes WHERE id=?", (ref,)).fetchone()
    return bool(r and r["out_of_service"])


def cfg_node_offline_s() -> int:
    try:
        return int(os.environ.get("AQUAGUARD_NODE_OFFLINE_S", "") or 0) or 30
    except ValueError:
        return 30


def _node_detail(conn, m, ref, now: datetime):
    """Everything the Leak Detection page shows about one meter: state, last seen and the live flow of its newest reading."""
    name, last_seen, state, flow, age = "", None, "missing", None, None
    if ref is not None:
        n = conn.execute("SELECT name, last_seen FROM nodes WHERE id=?", (ref,)).fetchone()
        name, last_seen = (n["name"] or "") if n else "", n["last_seen"] if n else None
        state = "out_of_service" if _node_oos(conn, ref) else "online" if _node_fresh(conn, ref, now) else "offline"
        s = conn.execute("SELECT flow_lpm, measured_at FROM flow_samples WHERE node_ref=? AND anomaly=0 ORDER BY measured_at DESC, id DESC LIMIT 1", (ref,)).fetchone()
        if s is not None:
            age = max(0.0, (now - parse_ts(s["measured_at"])).total_seconds())
            if state == "online" and age <= cfg_node_offline_s():
                flow = round(s["flow_lpm"], 3)
    return {"partId": m.part_id, "number": m.key[1], "label": m.label, "name": name, "nodeRef": ref, "state": state, "lastSeen": last_seen,
            "flowLpm": flow, "ageS": None if age is None else round(age, 1)}


def compute(conn, row, now: datetime, base: ZoneParams, persist: bool = False):
    """Evaluate one pipeline row.  Returns {zones: [...], coverage, suggestions, version, pipelineId}."""
    doc = json.loads(row["doc"])
    net = parse(doc)
    part = partition(net)
    now_s = int(now.timestamp())
    pipe_demo = demo_active(row, now_s)
    import clock_service
    clk = clock_service.load_clock(conn, now_s)                  # demo clock: only changes how the time rules read "now"
    demo = pipe_demo or clk.forced                               # alerts raised meanwhile are tagged demo
    p = params_for(doc, base, pipe_demo, load_thresholds(conn, row["id"]))
    tmodel = clock_service.apply_to_params(p, conn, row["id"], clk)
    t0 = now_s - int(p.lookback_h * 3600)
    prev = {r["zone_id"]: r for r in conn.execute("SELECT * FROM zone_state WHERE pipeline_id=?", (row["id"],)).fetchall()}
    cache: dict = {}
    zones_out = []
    import calibration_api
    details: dict = {}
    cal_refs = calibration_api.paused_refs(conn, now)           # nodes in a running calibration session: their zones are not checked meanwhile
    for z in part.zones:
        series_by_node = {}
        for m in z.meters():
            if m.key not in cache:
                ref = resolve_node_ref(conn, m.key, m.node_ref)
                smp = load_samples(conn, ref, t0 - 60, now_s + 60) if ref else []
                cache[m.key] = (ref, node_buckets(smp, t0, now_s, p.bucket_s, p.max_gap_s), smp)
            series_by_node[m.key] = cache[m.key][1]
        series = zone_series(z, series_by_node, p, t0, now_s) if z.in_meter else []
        fast = None
        if z.in_meter and z.cls == STRONG:                     # responsive balance: short windows over the last confirm_min (+ margin) only
            zq = p.for_zone(z.id)
            f0 = now_s - int(zq.confirm_min * 60 + 300)
            nw = {}
            for m in z.meters():
                smp = cache[m.key][2]
                lo = bisect_left(smp, f0 - 30, key=lambda x: x.t) if smp else 0
                nw[m.key] = fine_windows(smp[lo:], f0, now_s, zq.window_s, zq.fast_max_gap_s)
            fast = final_fast(fast_series(z, nw, zq, f0, now_s), now_s, zq)
        pv = prev.get(z.id)
        prev_level = {"suspected": 1, "confirmed": 2}.get(pv["state"], 0) if pv else 0
        calibrating = [m.label for m in z.meters() if cache[m.key][0] in cal_refs]
        if calibrating and z.cls != NONE:
            ev = Evaluation(0, "calibrating", "Calibration in progress: " + ", ".join(calibrating), [], None)
        else:
            ev = evaluate_zone(z, series, now_s, prev_level, p, fast)
        zp = p.for_zone(z.id)
        fin = final_only(series, now_s, zp)
        # live balance in L/min from the newest complete bucket (null when there is none: never zero)
        last = ev.last_bucket
        mins = zp.bucket_s / 60.0
        bal = None if last is None or not last.complete else {"qIn": round(last.q_in_l / mins, 3), "qOut": round(last.q_out_l / mins, 3),
                                                              "unaccounted": round(last.unacc_l / mins, 3), "thresholdLpm": round(last.thr_l / mins, 3), "bucketTs": last.ts,
                                                              "confidence": last.confidence}
        wl = next((r.extra for r in ev.results if r.extra), None)
        if wl and wl["dLpm"] is not None:                       # live numbers from the newest complete short windows (not the minute bucket)
            bal = {"qIn": round(wl["qInLpm"], 3), "qOut": round(wl["qOutLpm"], 3), "unaccounted": round(wl["dLpm"], 3), "thresholdLpm": round(wl["xPlusLpm"], 3),
                   "bucketTs": last.ts if last else None, "confidence": last.confidence if last else "high"}
        info = {"id": z.id, "name": z.name, "class": z.cls, "status": ev.status, "level": ev.level, "reason": ev.reason or z.reason, "equation": z.equation,
                "inMeter": z.in_meter.label if z.in_meter else None, "outMeters": [m.label for m in z.out_meters], "outlets": [net.label(o) for o in z.outlets], "outletIds": list(z.outlets),
                "pipes": [{"id": pid, "fraction": round(f, 3)} for pid, f in z.pipes], "parts": z.parts, "balance": bal, "meterFaultHint": ev.meter_fault_hint,
                "suggestions": z.suggestions, "parent": z.parent, "rules": [{"rule": r.rule, "level": r.level, "clear": r.clear, "reason": r.reason, "progress": r.progress} for r in ev.results],
                "watching": ev.status == "normal" and any((r.progress or {}).get("counting") for r in ev.results),
                "balanceState": (wl or {}).get("state"), "wlds": None if wl is None else {k: (round(v, 3) if isinstance(v, float) else v) for k, v in wl.items()},
                "settings": _settings_json(zp, p.tz_offset_min, demo),
                "timeRules": clock_service.zone_time_rules(tmodel, z.id),
                "completeBuckets": sum(1 for b in fin if b.complete), "finalBuckets": len(fin),
                "nodesMissing": [m.label for m in z.meters() if cache[m.key][0] is None],
                "nodesOffline": [m.label for m in z.meters() if cache[m.key][0] is not None and not _node_oos(conn, cache[m.key][0]) and not _node_fresh(conn, cache[m.key][0], now)],
                "nodesOutOfService": [m.label for m in z.meters() if cache[m.key][0] is not None and _node_oos(conn, cache[m.key][0])]}
        info["calibrating"] = calibrating if ev.status == "calibrating" else []
        if not persist:                                          # the page that shows the comparison (not needed by the periodic analysis)
            def detail(m):
                if m.part_id not in details:
                    details[m.part_id] = _node_detail(conn, m, cache[m.key][0], now)
                return details[m.part_id]
            info["inflow"] = [detail(z.in_meter)] if z.in_meter else []
            info["outflow"] = [detail(m) for m in z.out_meters]
            info["outletCount"] = len(z.outlets)
        zones_out.append(info)
        if persist and ev.status == "calibrating":
            if pv:                                                   # close a running event and forget the zone state: detection starts clean afterwards
                _close_alert(conn, row["id"], z.id, now, "calibration in progress")
                conn.execute("DELETE FROM zone_state WHERE pipeline_id=? AND zone_id=?", (row["id"], z.id))
        elif persist:
            _persist_zone(conn, row, now, z, ev, info, prev_level, pv, series, zp, now_s)
    if persist:
        keep = {z.id for z in part.zones}
        for zid, pv in prev.items():
            if zid not in keep:                       # the zone no longer exists in the active version: close its event
                _close_alert(conn, row["id"], zid, now, "pipeline configuration changed")
                conn.execute("DELETE FROM zone_state WHERE pipeline_id=? AND zone_id=?", (row["id"], zid))
    nodes_out = []
    if not persist:
        for pid_, d_ in details.items():
            nodes_out.append(dict(d_, zones=[{"id": z["id"], "name": z["name"], "role": "in"} for z in zones_out if any(x["partId"] == pid_ for x in z["inflow"])] +
                                  [{"id": z["id"], "name": z["name"], "role": "out"} for z in zones_out if any(x["partId"] == pid_ for x in z["outflow"])]))
        nodes_out.sort(key=lambda x: x["number"])
    return {"nodes": nodes_out, "pipelineId": row["id"], "name": row["name"], "version": row["active_version"], "demo": demo, "demoUntil": row["demo_until"] if pipe_demo else None, "zones": zones_out, "coverage": part.coverage, "suggestions": part.suggestions,
            "clock": {"mode": clk.mode, "since": clk.since, "until": clk.until}, "pipelineDemo": pipe_demo}


def _settings_json(zp: ZoneParams, tz_off: int, demo: bool) -> dict:
    """The values that are really in force for one zone (shown on the Leak Detection page and the zone panel)."""
    from analysis.rules import hhmm
    return {"thresholdLpm": zp.abs_min_lpm, "tolerancePct": round(zp.tolerance * 100, 1), "suspectMin": zp.suspect_min, "confirmMin": zp.confirm_min, "resolveMin": zp.resolve_min,
            "bucketS": zp.bucket_s, "windowS": zp.window_s, "xPlusLpm": zp.x_plus_lpm, "xMinusLpm": zp.x_minus_lpm, "tLeakS": zp.t_leak_s, "nightStart": hhmm(zp.night_start_min), "nightEnd": hhmm(zp.night_end_min), "continuousSuspectMin": round(zp.continuous_suspect_h * 60),
            "continuousConfirmMin": round(zp.continuous_confirm_h * 60), "tz": SITE_TZ, "tzOffsetMin": tz_off, "demo": demo}


def _alert_key(pid, zid):
    return f"LEAK:{pid}:{zid}"


def _persist_zone(conn, row, now, z, ev, info, prev_level, pv, series, zp, now_s):
    ts = fmt_ts(now)
    since = pv["since"] if pv and prev_level == ev.level and pv["since"] else (ts if ev.level else None)
    alert_id = pv["alert_id"] if pv else None
    key = _alert_key(row["id"], z.id)
    if ev.level and not prev_level or (ev.level and not conn.execute("SELECT 1 FROM alerts WHERE alert_key=? AND active=1", (key,)).fetchone()):
        sev = "critical" if ev.level == 2 else "warning"
        msg = f"{'[demo] ' if info['settings']['demo'] else ''}{LEVEL_NAME[ev.level].capitalize()} leak in {z.name}: {ev.reason}"
        exists = conn.execute("SELECT id FROM alerts WHERE alert_key=? AND active=1", (key,)).fetchone()
        if exists:
            alert_id = exists["id"]
        else:
            cur = conn.execute("INSERT INTO alerts (alert_key,kind,severity,message,active,created_at,zone_id,pipeline_id,pipeline_version,state,updated_at,demo,suspected_at,acc_at,lost_l) VALUES (?,?,?,?,1,?,?,?,?,?,?,?,?,?,0)",
                               (key, "LEAK", sev, msg, ts, z.id, row["id"], row["active_version"], LEVEL_NAME[ev.level], ts, 1 if info["settings"]["demo"] else 0, ts, ts))
            alert_id = cur.lastrowid
    elif ev.level and ev.level != prev_level:
        conn.execute("UPDATE alerts SET severity=?, message=?, state=?, updated_at=? WHERE alert_key=? AND active=1",
                     ("critical" if ev.level == 2 else "warning", f"{'[demo] ' if info['settings']['demo'] else ''}{LEVEL_NAME[ev.level].capitalize()} leak in {z.name}: {ev.reason}", LEVEL_NAME[ev.level], ts, key))
    elif not ev.level and prev_level:
        _close_alert(conn, row["id"], z.id, now, "back to normal")
        alert_id = None
    _persist_measurement_alert(conn, row, now, z, info, zp)
    bal = info["balance"]
    if alert_id and ev.level:                         # numbers for the event list / dashboard; the peak only ever grows
        un = (bal or {}).get("unaccounted")
        conn.execute("UPDATE alerts SET q_in_lpm=COALESCE(?,q_in_lpm), q_out_lpm=COALESCE(?,q_out_lpm), unaccounted_lpm=COALESCE(?,unaccounted_lpm),"
                     " peak_unaccounted_lpm=MAX(COALESCE(peak_unaccounted_lpm,0), COALESCE(?,0)),"
                     " confirmed_at=CASE WHEN ?=2 AND confirmed_at IS NULL THEN ? ELSE confirmed_at END WHERE id=?",
                     ((bal or {}).get("qIn"), (bal or {}).get("qOut"), un, un, ev.level, ts, alert_id))
        import usage_service
        usage_service._acc(conn, alert_id, un, now)           # estimated water lost = integral of D over the event
    conn.execute("INSERT INTO zone_state (pipeline_id,zone_id,version,zone_class,state,since,processed_until,state_json,q_in_lpm,q_out_lpm,unaccounted_lpm,complete,confidence,alert_id,updated_at)"
                 " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(pipeline_id,zone_id) DO UPDATE SET version=excluded.version, zone_class=excluded.zone_class,"
                 " state=excluded.state, since=excluded.since, processed_until=excluded.processed_until, state_json=excluded.state_json, q_in_lpm=excluded.q_in_lpm,"
                 " q_out_lpm=excluded.q_out_lpm, unaccounted_lpm=excluded.unaccounted_lpm, complete=excluded.complete, confidence=excluded.confidence,"
                 " alert_id=excluded.alert_id, updated_at=excluded.updated_at",
                 (row["id"], z.id, row["active_version"], z.cls, info["status"] if info["status"] in ("suspected", "confirmed") else "normal", since, now_s,
                  json.dumps({"status": info["status"], "rules": info["rules"]}), (info["balance"] or {}).get("qIn"), (info["balance"] or {}).get("qOut"),
                  (info["balance"] or {}).get("unaccounted"), int(info["balance"] is not None), (info["balance"] or {}).get("confidence"), alert_id, ts))
    # finalised buckets of the last 2 h (charts + leak history); late data simply overwrites them on the next tick
    for b in final_only(series, now_s, zp):
        if b.ts >= now_s - 7200:
            conn.execute("INSERT INTO zone_buckets (pipeline_id,zone_id,bucket_ts,q_in_l,q_out_l,unaccounted_l,complete,confidence,state,version) VALUES (?,?,?,?,?,?,?,?,?,?)"
                         " ON CONFLICT(pipeline_id,zone_id,bucket_ts) DO UPDATE SET q_in_l=excluded.q_in_l, q_out_l=excluded.q_out_l, unaccounted_l=excluded.unaccounted_l,"
                         " complete=excluded.complete, confidence=excluded.confidence, state=excluded.state, version=excluded.version",
                         (row["id"], z.id, b.ts, b.q_in_l, b.q_out_l, b.unacc_l, int(b.complete), b.confidence, info["status"], row["active_version"]))


def _meas_key(pid, zid):
    return f"MEAS:{pid}:{zid}"


def _persist_measurement_alert(conn, row, now, z, info, zp):
    """MEASUREMENT_INCONSISTENCY (outflow larger than inflow) is a sensor / calibration / synchronisation problem, NOT a leak: it gets its own
    warning (kind measurement_inconsistency) once it has lasted T_leak, and clears by itself when D is back inside the band."""
    key = _meas_key(row["id"], z.id)
    wl = info.get("wlds") or {}
    active = conn.execute("SELECT id FROM alerts WHERE alert_key=? AND active=1", (key,)).fetchone()
    ts = fmt_ts(now)
    if info.get("balanceState") == "inconsistent" and (wl.get("tdS") or 0) >= zp.t_leak_s:
        msg = (f"{'[demo] ' if info['settings']['demo'] else ''}Measurement inconsistency in {z.name}: outflow is larger than inflow by {-wl['dLpm']:.2f} L/min "
               f"- check the sensors, the calibration or the test setup (not a leak)")
        if active:
            conn.execute("UPDATE alerts SET message=?, updated_at=? WHERE id=?", (msg, ts, active["id"]))
        else:
            conn.execute("INSERT INTO alerts (alert_key,kind,severity,message,active,created_at,zone_id,pipeline_id,pipeline_version,state,updated_at,demo,suspected_at) VALUES (?,?,?,?,1,?,?,?,?,?,?,?,?)",
                         (key, "measurement_inconsistency", "warning", msg, ts, z.id, row["id"], row["active_version"], "inconsistent", ts, 1 if info["settings"]["demo"] else 0, ts))
    elif active and info.get("balanceState") != "inconsistent":
        conn.execute("UPDATE alerts SET active=0, cleared_at=?, state='resolved', updated_at=?, message=message || ' (back inside the band)' WHERE id=?", (ts, ts, active["id"]))


def _close_alert(conn, pid, zid, now, why):
    conn.execute("UPDATE alerts SET active=0, cleared_at=?, state='resolved', updated_at=?, message=message || ' (' || ? || ')' WHERE alert_key=? AND active=1",
                 (fmt_ts(now), fmt_ts(now), why, _alert_key(pid, zid)))


def close_stale_alerts(conn, now, results, failed=()):
    """Leak alerts nothing evaluates any more would stay active forever: close them.
    * a DEMO alert when its pipeline is no longer in demo mode (demo ended, auto-off after 1 h, or switched off);
    * any leak alert whose zone is not in an active pipeline any more (pipeline switched off / re-saved with other zones)."""
    live = {_alert_key(r["pipelineId"], z["id"]) for r in results for z in r["zones"]}
    demo_pids = {r["pipelineId"] for r in results if r.get("demo")}        # pipeline demo mode OR a forced demo clock
    ts = fmt_ts(now)
    live |= {_meas_key(r["pipelineId"], z["id"]) for r in results for z in r["zones"]}
    for a in conn.execute("SELECT id, alert_key, pipeline_id, demo FROM alerts WHERE kind IN ('LEAK','measurement_inconsistency') AND active=1").fetchall():
        if a["pipeline_id"] in failed:
            continue                                   # the pipeline is broken right now: do not guess
        if a["alert_key"] not in live:
            why = "zone or pipeline no longer active"
        elif a["demo"] and a["pipeline_id"] not in demo_pids:
            why = "demo mode ended"
        else:
            continue
        conn.execute("UPDATE alerts SET active=0, cleared_at=?, state='resolved', updated_at=?, message=message || ' (' || ? || ')' WHERE id=?", (ts, ts, why, a["id"]))
        conn.execute("UPDATE zone_state SET alert_id=NULL WHERE alert_id=?", (a["id"],))
        if why == "demo mode ended" and a["alert_key"].startswith("LEAK:"):
            # the zone's saved rule state still says "suspected" (reached with the 1 min demo thresholds): drop it, or the next normal run
            # would raise the same leak again as a REAL alert
            _, pid, zid = a["alert_key"].split(":", 2)
            conn.execute("DELETE FROM zone_state WHERE pipeline_id=? AND zone_id=?", (int(pid), zid))


def run_analysis(conn, cfg, now: datetime | None = None, base: ZoneParams | None = None) -> list:
    from ingest import utc_now
    import calibration_api
    now = now or utc_now()
    calibration_api.expire_sessions(conn, now)
    import clock_service
    clock_service.expire_clock(conn, now.timestamp())             # the demo clock switches itself off after 1 h (audit entry by the system)
    base = base or params_from_env()
    out = []
    failed = set()
    for row in active_pipelines(conn):
        try:
            out.append(compute(conn, row, now, base, persist=True))
        except DocError as e:
            failed.add(row["id"])
            print(f"[ANALYSIS] pipeline {row['id']} v{row['active_version']} unusable: {e}")
    close_stale_alerts(conn, now, out, failed)
    try:
        import usage_service
        usage_service.run_usage(conn, now)            # per-node duration / night-flow check: independent of the zone balance
    except Exception as e:                      # noqa: BLE001  a usage problem must never stop the leak analysis
        print(f"[USAGE] check failed: {e}")
    conn.execute("DELETE FROM zone_buckets WHERE bucket_ts < ?", (int(now.timestamp()) - 30 * 86400,))
    return out


# ---------------------------------------------------------------- threshold learning (guided no-leak test)
def _strong_zone(doc: dict, zone_id: str):
    net = parse(doc)
    z = partition(net).zone(zone_id)
    if z is None or z.cls != STRONG or z.in_meter is None:
        raise DocError("Balance thresholds apply to strong zones only (an inflow meter and no unmetered outlets).")
    return z


def learn_compute(conn, row, zone_id: str, t_start: int, t_end: int, base: ZoneParams | None = None) -> dict:
    """x+/x- = mean +- 3 SD of the signed difference D over the windows of a no-leak run [t_start, t_end] (seconds)."""
    from analysis.balance import learn_thresholds
    doc = json.loads(row["doc"])
    z = _strong_zone(doc, zone_id)
    p = params_for(doc, base or params_from_env(), False, load_thresholds(conn, row["id"])).for_zone(zone_id)
    nw = {}
    for m in z.meters():
        ref = resolve_node_ref(conn, m.key, m.node_ref)
        smp = load_samples(conn, ref, t_start - 30, t_end + 30) if ref else []
        nw[m.key] = fine_windows(smp, t_start, t_end, p.window_s, p.fast_max_gap_s)
    return learn_thresholds(fast_series(z, nw, p, t_start, t_end), p)
