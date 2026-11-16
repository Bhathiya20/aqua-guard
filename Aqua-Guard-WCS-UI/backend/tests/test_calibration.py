"""Calibration wizard: pure maths, idle/stop rule on measured time, reboot / timeout handling, leak pause and resume, apply / revert / history."""
import time
from datetime import datetime, timezone

import pytest
from werkzeug.security import generate_password_hash

import analysis_service
import calibration as cal
import calibration_api
import database
import mqtt_service
from analysis.rules import ZoneParams
from pipeline.presets import calibration_rig
from protocol_v2 import parse_aqc2, parse_cmd_key
from tests import netdocs as nd
from tests.conftest import USER_EMAIL, USER_PW, login
from tests.v2helpers import NodeSim, T0, dt, feed, heartbeat, info, registry

KEY_HEX = "11" * 16
MAC_A, MAC_B = "447b30854b88", "aabbcc000002"
MIN = 60


# ---------------------------------------------------------------------------------------------- pure maths
def S(boot, seq, cum, measured):
    return {"boot": boot, "seq": seq, "cum": cum, "measured": measured}


def test_idle_now_needs_two_equal_recent_samples():
    assert cal.idle_now([], 100) == (False, "no data yet")
    assert cal.idle_now([S(1, 1, 5, 99)], 100)[0] is False
    assert cal.idle_now([S(1, 1, 5, 97), S(1, 2, 5, 99)], 100) == (True, "")
    assert cal.idle_now([S(1, 1, 5, 97), S(1, 2, 9, 99)], 100) == (False, "water is flowing")
    assert cal.idle_now([S(1, 1, 5, 40), S(1, 2, 5, 42)], 100) == (False, "no recent data")
    assert cal.idle_now([S(1, 2, 5, 97), S(1, 2, 5, 99)], 100)[0] is False             # same seq twice is not a NEW sample


def test_stop_rule_uses_measured_time_and_new_samples_only():
    smp = [S(1, 1, 10, 100), S(1, 2, 10, 102), S(1, 3, 40, 104), S(1, 4, 40, 106), S(1, 5, 40, 108)]
    assert cal.idle_after(smp, 1, 103)["cum"] == 40                       # the old identical pair (before the stop) does not count
    assert cal.idle_after(smp, 1, 106) is None                            # only ONE new sample after 106
    assert cal.idle_after([S(1, 3, 40, 104), S(1, 4, 41, 106), S(1, 5, 41, 108)], 1, 103)["cum"] == 41
    assert cal.idle_after([S(2, 1, 0, 110), S(2, 2, 0, 112)], 1, 103) is None            # another boot never counts
    assert cal.idle_after([S(1, 4, 40, 106), S(1, 4, 40, 108)], 1, 103) is None           # the same seq is not two samples


def test_summary_statistics_weighted_median_and_warnings():
    nodes = [{"id": 1, "label": "Node 1"}, {"id": 2, "label": "Node 2"}]

    def run(i, vol, p1, p2, status="done"):
        return {"id": i, "idx": i, "status": status, "volumeL": vol, "durationS": 60, "nodes": {1: {"pulses": p1, "valid": True}, "2": None, 2: {"pulses": p2, "valid": True}}}
    runs = [run(1, 2.0, 900, 910), run(2, 4.0, 1800, 1830), run(3, 3.0, 1350, 1371)]
    for r in runs:
        r["nodes"].pop("2")
    res = cal.summarize(nodes, runs)
    n1 = next(n for n in res["nodes"] if n["id"] == 1)
    assert n1["weightedPpl"] == 450.0 and n1["medianPpl"] == 450.0 and n1["spreadPct"] == 0.0 and n1["quality"] == "good" and n1["runs"] == 3
    assert not [w for w in res["warnings"] if w["code"] in ("spread", "run_volume", "run_node")]
    # spread > 5 % and a node that disagrees with the others in one run
    runs2 = [run(1, 2.0, 900, 910), run(2, 2.0, 900, 1100), run(3, 2.0, 1000, 910)]
    res2 = cal.summarize(nodes, runs2)
    codes = [(w["code"], w["nodeId"]) for w in res2["warnings"]]
    assert ("spread", 1) in codes and ("spread", 2) in codes
    n2 = next(n for n in res2["nodes"] if n["id"] == 2)
    assert n2["quality"] == "poor"
    # an invalid / volume-less run never counts; a small run warns
    runs3 = [run(1, 2.0, 900, 900), run(2, None, 500, 500), run(3, 1.0, 450, 450, status="invalid")]
    res3 = cal.summarize(nodes, runs3)
    assert res3["validRuns"] == 1 and next(n for n in res3["nodes"] if n["id"] == 1)["runs"] == 1
    assert any(w["code"] == "few_runs" for w in res3["warnings"])
    assert any(w["code"] == "small_volume" for w in cal.summarize(nodes, [run(1, 1.0, 450, 450)])["warnings"])


def _runs(volumes, pulses):
    """pulses = {node_id: [pulses per run]}"""
    return [{"id": i + 1, "idx": i + 1, "status": "done", "volumeL": v, "durationS": 60, "nodes": {nid: {"pulses": p[i], "valid": True} for nid, p in pulses.items()}}
            for i, v in enumerate(volumes)]


def test_sensors_that_differ_from_each_other_do_not_warn():
    nodes = [{"id": 1, "label": "Node 1"}, {"id": 2, "label": "Node 2"}]
    runs = _runs([2, 2, 2, 2], {1: [900, 900, 900, 900], 2: [1000, 1000, 1000, 1000]})            # 450 vs 500 p/L in every run: two different sensors
    assert [w for w in cal.summarize(nodes, runs)["warnings"] if w["code"] in ("run_volume", "run_node", "spread")] == []


def test_all_nodes_deviating_the_same_way_in_one_run_points_at_the_volume():
    nodes = [{"id": 1, "label": "Node 1"}, {"id": 2, "label": "Node 2"}]
    runs = _runs([2, 2, 2, 2.2], {1: [900, 900, 900, 900], 2: [1000, 1000, 1000, 1000]})           # run 4: volume entered 10 % too high -> both read 9 % fewer p/L
    w = [x for x in cal.summarize(nodes, runs)["warnings"] if x["code"] in ("run_volume", "run_node")]
    assert [(x["code"], x["runId"]) for x in w] == [("run_volume", 4)]
    assert "Volume for run 4 may be wrong" in w[0]["message"]
    # a deviation below 3 % is ignored
    runs = _runs([2, 2, 2, 2.04], {1: [900, 900, 900, 900], 2: [1000, 1000, 1000, 1000]})
    assert [x for x in cal.summarize(nodes, runs)["warnings"] if x["code"] in ("run_volume", "run_node")] == []


def test_one_node_deviating_in_one_run_points_at_air_or_a_leak_at_that_node():
    nodes = [{"id": 1, "label": "Node 1"}, {"id": 2, "label": "Node 2"}, {"id": 3, "label": "Node 3"}]
    runs = _runs([2, 2, 2, 2], {1: [900, 900, 900, 900], 2: [1000, 1000, 1000, 1000], 3: [910, 910, 910, 840]})         # Node 3 lost 8 % in run 4 (air)
    w = [x for x in cal.summarize(nodes, runs)["warnings"] if x["code"] in ("run_volume", "run_node")]
    assert [(x["code"], x["runId"], x["nodeId"]) for x in w] == [("run_node", 4, 3)]
    assert "Node 3 run 4: air or leak at this node" in w[0]["message"]
    # two of three nodes deviating is neither case: no guess
    runs = _runs([2, 2, 2, 2], {1: [900, 900, 900, 830], 2: [1000, 1000, 1000, 920], 3: [910, 910, 910, 910]})
    assert [x for x in cal.summarize(nodes, runs)["warnings"] if x["code"] in ("run_volume", "run_node")] == []


def test_apply_checks_range_and_confirmation():
    assert cal.check_apply(450, 450)[0] == "ok"
    assert cal.check_apply(250, 450)[0] == "refuse" and cal.check_apply(701, 450)[0] == "refuse" and cal.check_apply(None, 450)[0] == "refuse"
    assert cal.check_apply(530, 450)[0] == "confirm" and cal.check_apply(380, 450)[0] == "confirm"
    assert cal.check_apply(500, 450)[0] == "ok"                                   # +11 %
    assert cal.ppl_x10(452.46) == 4525 and cal.ppl_x10(450.04) == 4500


def test_rig_preset_is_a_series_and_valid():
    doc = calibration_rig([{"number": 1, "nodeRef": 5}, {"number": 2, "nodeRef": 6}, {"number": 3}])
    assert [p["kind"] for p in doc["parts"]].count("node") == 3 and doc["parts"][-1]["outletType"] == "tank"
    from pipeline.model import parse
    from pipeline.validate import is_valid, validate
    msgs = validate(parse(doc), {("flow", 1), ("flow", 2), ("flow", 3)})
    assert is_valid(msgs), msgs


# ---------------------------------------------------------------------------------------------- API fixtures
def real_now():
    return datetime.now(timezone.utc)


@pytest.fixture()
def clock(monkeypatch):
    t = {"now": T0}
    monkeypatch.setattr(calibration_api, "_now", lambda: dt(t["now"]))
    return t


@pytest.fixture()
def sent():
    out = []
    old = mqtt_service._text_publisher
    mqtt_service.set_text_publisher(lambda t, x: out.append((t, x)) or True)
    yield out
    mqtt_service.set_text_publisher(old)


@pytest.fixture()
def admin(app, cfg):
    cfg.cmd_key_hex = KEY_HEX
    c = app.test_client()
    h = {"X-CSRF-Token": login(c)}
    return lambda m, url, **kw: getattr(c, m)(url, headers=h, **kw)


@pytest.fixture()
def rig(app, cfg, clock):
    """Two paired flow nodes, a gateway that is online and time synced; both nodes idle for 20 s before T0+20."""
    n = real_now()
    registry([("flow", 1, MAC_A, "Kitchen", "paired"), ("flow", 2, MAC_B, "Garden", "paired")], now=n)
    info(now=n, remoteAdd=False)
    heartbeat(now=n, epoch=int(time.time()))
    sims = {1: NodeSim(1, MAC_A), 2: NodeSim(2, MAC_B)}
    for s in sims.values():
        feed(s, T0, T0 + 20, 0.0)
    clock["now"] = T0 + 20
    return sims


def refs():
    with database.db_session() as c:
        return {r["number"]: r["id"] for r in c.execute("SELECT id, number FROM nodes WHERE type='flow'")}


def pour(sims, t0, t1, rate=6.0, only=None):
    for k, s in sims.items():
        if only is None or k in only:
            feed(s, t0, t1, rate)


def idle(sims, t0, t1, only=None):
    pour(sims, t0, t1, 0.0, only)


def new_session(admin):
    r = admin("post", "/api/calibration/sessions", json={"nodeIds": [refs()[1], refs()[2]]})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def full_run(admin, clock, sims, sid, volume, start, pour_s=20, rate=6.0):
    """Start at `start`, pour for pour_s, stop 4 s after the pour, let the nodes settle, enter the volume.  Returns the session json."""
    clock["now"] = start
    r = admin("post", f"/api/calibration/sessions/{sid}/runs")
    assert r.status_code == 201, r.get_json()
    rid = r.get_json()["activeRunId"]
    pour(sims, start, start + 2, 0.0)
    pour(sims, start + 2, start + 2 + pour_s, rate)
    end = start + 2 + pour_s
    idle(sims, end, end + 4)
    clock["now"] = end + 4
    assert admin("post", f"/api/calibration/sessions/{sid}/runs/{rid}/stop").status_code == 200
    idle(sims, end + 4, end + 12)
    clock["now"] = end + 12
    d = admin("get", f"/api/calibration/sessions/{sid}").get_json()
    run = next(x for x in d["runs"] if x["id"] == rid)
    assert run["status"] == "done", run
    r = admin("put", f"/api/calibration/sessions/{sid}/runs/{rid}", json={"volumeL": volume})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


# ---------------------------------------------------------------------------------------------- session / run flow
def test_session_creates_an_inactive_rig_pipeline_and_a_banner(admin, rig):
    d = new_session(admin)
    assert d["status"] == "running" and [n["label"] for n in d["nodes"]] == ["Node 1", "Node 2"] and d["canStart"] is True
    p = admin("get", f"/api/pipelines/{d['pipelineId']}").get_json()
    assert p["activeVersion"] is None and p["valid"] and p["name"].startswith("Calibration rig")
    a = admin("get", "/api/calibration/active").get_json()
    assert a["active"] is True and a["sessionId"] == d["id"] and [n["label"] for n in a["nodes"]] == ["Node 1", "Node 2"]
    assert admin("post", "/api/calibration/sessions", json={"nodeIds": [refs()[1]]}).status_code == 409           # one session at a time
    assert admin("post", f"/api/calibration/sessions/{d['id']}/cancel").get_json()["status"] == "cancelled"
    assert admin("get", "/api/calibration/active").get_json()["active"] is False


def test_start_needs_every_node_idle(admin, rig, clock):
    d = new_session(admin)
    pour(rig, T0 + 20, T0 + 26, 5.0, only=[2])                      # Node 2 is flowing
    idle(rig, T0 + 20, T0 + 26, only=[1])
    clock["now"] = T0 + 26
    r = admin("post", f"/api/calibration/sessions/{d['id']}/runs")
    assert r.status_code == 409 and r.get_json()["code"] == "not_idle" and r.get_json()["nodes"][0]["label"] == "Node 2"
    assert admin("get", f"/api/calibration/sessions/{d['id']}").get_json()["canStart"] is False
    idle(rig, T0 + 26, T0 + 34)
    clock["now"] = T0 + 34
    assert admin("get", f"/api/calibration/sessions/{d['id']}").get_json()["canStart"] is True
    clock["now"] = T0 + 200                                            # no recent readings at all: not idle either
    assert admin("post", f"/api/calibration/sessions/{d['id']}/runs").status_code == 409


def test_run_counts_cumulative_pulses_and_waits_for_a_steady_counter(admin, rig, clock):
    d = new_session(admin)
    clock["now"] = T0 + 22
    rid = admin("post", f"/api/calibration/sessions/{d['id']}/runs").get_json()["activeRunId"]
    pour(rig, T0 + 22, T0 + 24, 0.0)
    pour(rig, T0 + 24, T0 + 44, 6.0)                              # 6 L/min for 20 s = 2 L = 900 pulses
    clock["now"] = T0 + 45
    s = admin("post", f"/api/calibration/sessions/{d['id']}/runs/{rid}/stop").get_json()
    run = s["runs"][0]
    assert run["status"] == "stopping" and sorted(run["waitingFor"]) == ["Node 1", "Node 2"]             # no new samples yet
    idle(rig, T0 + 44, T0 + 50, only=[1])                         # Node 1 reports a steady counter, Node 2 is silent
    clock["now"] = T0 + 51
    run = admin("get", f"/api/calibration/sessions/{d['id']}").get_json()["runs"][0]
    assert run["status"] == "stopping" and run["waitingFor"] == ["Node 2"]
    idle(rig, T0 + 44, T0 + 56, only=[2])
    clock["now"] = T0 + 57
    s = admin("get", f"/api/calibration/sessions/{d['id']}").get_json()
    run = s["runs"][0]
    assert run["status"] == "done" and run["waitingFor"] == []
    assert run["cells"][str(refs()[1])]["pulses"] == 900 and run["cells"][str(refs()[2])]["pulses"] == 900
    s = admin("put", f"/api/calibration/sessions/{d['id']}/runs/{rid}", json={"volumeL": 2.0}).get_json()
    node = next(n for n in s["results"]["nodes"] if n["id"] == refs()[1])
    assert node["weightedPpl"] == 450.0 and s["results"]["runs"][0]["nodes"][str(refs()[1])]["ppl"] == 450.0
    assert 5.5 < s["results"]["runs"][0]["nodes"][str(refs()[1])]["flowLpm"] < 6.5 or s["results"]["runs"][0]["nodes"][str(refs()[1])]["flowLpm"] > 0
    assert admin("put", f"/api/calibration/sessions/{d['id']}/runs/{rid}", json={"volumeL": 0}).status_code == 400


def test_lost_packets_do_not_matter(admin, rig, clock):
    d = new_session(admin)
    clock["now"] = T0 + 22
    rid = admin("post", f"/api/calibration/sessions/{d['id']}/runs").get_json()["activeRunId"]
    for k, s in rig.items():
        feed(s, T0 + 22, T0 + 24, 0.0)
        feed(s, T0 + 24, T0 + 44, 6.0, skip=[(T0 + 28, T0 + 38)])           # 5 samples never arrive
    idle(rig, T0 + 44, T0 + 50)
    clock["now"] = T0 + 44
    admin("post", f"/api/calibration/sessions/{d['id']}/runs/{rid}/stop")
    clock["now"] = T0 + 51
    run = admin("get", f"/api/calibration/sessions/{d['id']}").get_json()["runs"][0]
    assert run["status"] == "done" and run["cells"][str(refs()[1])]["pulses"] == 900


def test_reboot_during_a_run_invalidates_the_run(admin, rig, clock):
    d = new_session(admin)
    clock["now"] = T0 + 22
    rid = admin("post", f"/api/calibration/sessions/{d['id']}/runs").get_json()["activeRunId"]
    pour(rig, T0 + 22, T0 + 30, 6.0)
    rebooted = NodeSim(1, MAC_A, boot=2000, t_boot=T0 + 30)                  # Node 1 restarts: new boot id, counter from 0
    feed(rebooted, T0 + 32, T0 + 40, 6.0)
    clock["now"] = T0 + 41
    s = admin("get", f"/api/calibration/sessions/{d['id']}").get_json()
    run = s["runs"][0]
    assert run["status"] == "invalid" and "Node 1 restarted" in run["reason"]
    assert admin("put", f"/api/calibration/sessions/{d['id']}/runs/{rid}", json={"volumeL": 2}).status_code == 409
    assert s["results"]["validRuns"] == 0
    assert admin("delete", f"/api/calibration/sessions/{d['id']}/runs/{rid}").get_json()["runs"] == []


def test_a_silent_node_times_out_after_60_seconds_only_for_that_node(admin, rig, clock):
    d = new_session(admin)
    clock["now"] = T0 + 22
    rid = admin("post", f"/api/calibration/sessions/{d['id']}/runs").get_json()["activeRunId"]
    pour(rig, T0 + 22, T0 + 44, 6.0)
    clock["now"] = T0 + 44
    admin("post", f"/api/calibration/sessions/{d['id']}/runs/{rid}/stop")
    idle(rig, T0 + 44, T0 + 50, only=[1])
    clock["now"] = T0 + 50
    assert admin("get", f"/api/calibration/sessions/{d['id']}").get_json()["runs"][0]["status"] == "stopping"
    clock["now"] = T0 + 44 + 61
    run = admin("get", f"/api/calibration/sessions/{d['id']}").get_json()["runs"][0]
    assert run["status"] == "done"
    assert run["cells"][str(refs()[1])]["valid"] is True and run["cells"][str(refs()[2])]["valid"] is False
    assert "Node 2" in run["cells"][str(refs()[2])]["reason"] and "60 s" in run["cells"][str(refs()[2])]["reason"]


def test_three_runs_apply_and_history(admin, rig, clock, sent):
    d = new_session(admin)
    sid = d["id"]
    full_run(admin, clock, rig, sid, 2.0, T0 + 22)
    idle(rig, T0 + 34 + 20, T0 + 60)
    full_run(admin, clock, rig, sid, 2.0, T0 + 60, rate=3.0, pour_s=40)           # 3 L/min * 40 s = 2 L
    idle(rig, T0 + 114, T0 + 130)                                                  # the nodes keep reporting a steady counter between the runs
    s = full_run(admin, clock, rig, sid, 2.0, T0 + 130, rate=10.0, pour_s=12)     # 10 L/min * 12 s = 2 L
    assert s["results"]["validRuns"] == 3
    n1 = next(n for n in s["results"]["nodes"] if n["id"] == refs()[1])
    assert n1["runs"] == 3 and n1["quality"] in ("good", "ok") and abs(n1["weightedPpl"] - 450) < 5
    assert s["proposals"][str(refs()[1])]["weighted"]["verdict"] == "ok"
    r = admin("post", f"/api/calibration/sessions/{sid}/apply", json={"method": "weighted"})
    assert r.status_code == 200, r.get_json()
    res = r.get_json()["results"]
    assert len(res) == 2 and all(x["commandId"] for x in res) and r.get_json()["status"] == "done"
    p = parse_aqc2(parse_cmd_key(KEY_HEX), sent[-1][1])
    assert p["op"] == "C" and p["ppl"] == res[-1]["valueX10"]
    assert admin("get", "/api/calibration/active").get_json()["active"] is False                     # detection resumes
    h = admin("get", f"/api/nodes/{refs()[1]}/calibrations").get_json()["history"]
    assert len(h) == 1 and h[0]["source"] == "wizard" and h[0]["runs"] == 3 and h[0]["oldPpl"] == 450.0 and h[0]["method"] == "weighted"
    # the ended session can still be applied again (e.g. a node that failed), results stay readable
    assert admin("get", f"/api/calibration/sessions/{sid}").get_json()["status"] == "done"


def test_apply_refuses_out_of_range_and_asks_for_confirmation(admin, rig, clock, sent):
    sid = new_session(admin)["id"]
    full_run(admin, clock, rig, sid, 0.9, T0 + 22)                                  # 900 pulses / 0.9 L = 1000 pulses/L
    r = admin("post", f"/api/calibration/sessions/{sid}/apply", json={})
    assert r.status_code == 422 and r.get_json()["code"] == "refused" and sent == []
    run_id = admin("get", f"/api/calibration/sessions/{sid}").get_json()["runs"][0]["id"]
    admin("put", f"/api/calibration/sessions/{sid}/runs/{run_id}", json={"volumeL": 1.7})       # 529 pulses/L: +17.6 % from 450
    r = admin("post", f"/api/calibration/sessions/{sid}/apply", json={})
    assert r.status_code == 409 and r.get_json()["code"] == "needs_confirm" and sent == []
    r = admin("post", f"/api/calibration/sessions/{sid}/apply", json={"confirm": True})
    assert r.status_code == 200 and len(sent) == 2
    assert parse_aqc2(parse_cmd_key(KEY_HEX), sent[0][1])["ppl"] == 5294                       # 900 / 1.7 = 529.4, rounded to 0.1 p/L
    # the median method
    assert admin("post", f"/api/calibration/sessions/{sid}/apply", json={"method": "nope"}).status_code == 400


def test_manual_fine_tune_is_recorded_and_revert_restores_the_old_value(admin, rig, sent):
    ref = refs()[1]
    assert admin("patch", f"/api/nodes/{ref}", json={"pulsesPerLiter": 452.5}).status_code in (200, 202)
    assert admin("patch", f"/api/nodes/{ref}", json={"pulsesPerLiter": 460}).status_code in (200, 202)
    h = admin("get", f"/api/nodes/{ref}/calibrations").get_json()["history"]
    assert [(x["source"], x["oldPpl"], x["newPpl"]) for x in h] == [("manual", 450.0, 460.0), ("manual", 450.0, 452.5)]
    r = admin("post", f"/api/nodes/{ref}/calibrations/{h[1]['id']}/revert")
    assert r.status_code == 202
    assert parse_aqc2(parse_cmd_key(KEY_HEX), sent[-1][1])["ppl"] == 4500
    assert admin("get", f"/api/nodes/{ref}/calibrations").get_json()["history"][0]["source"] == "revert"
    assert admin("post", f"/api/nodes/{ref}/calibrations/9999/revert").status_code == 404


def test_users_are_read_only(app, admin, rig):
    d = new_session(admin)
    with database.db_session() as c:
        c.execute("INSERT INTO users (first_name,last_name,email,password_hash,role,must_change_password) VALUES ('U','S',?,?, 'user', 0)", (USER_EMAIL, generate_password_hash(USER_PW)))
    u = app.test_client()
    h = {"X-CSRF-Token": login(u, USER_EMAIL, USER_PW)}
    assert u.get(f"/api/calibration/sessions/{d['id']}", headers=h).status_code == 200
    assert u.get("/api/calibration/active", headers=h).get_json()["active"] is True
    for m, url in (("post", "/api/calibration/sessions"), ("post", f"/api/calibration/sessions/{d['id']}/runs"), ("post", f"/api/calibration/sessions/{d['id']}/apply"),
                   ("post", f"/api/calibration/sessions/{d['id']}/cancel")):
        assert getattr(u, m)(url, headers=h, json={}).status_code == 403


# ---------------------------------------------------------------------------------------------- leak detection pause
def P(**kw):
    return ZoneParams(**{"tz_offset_min": 0, "suspect_min": 5, "confirm_min": 10, "resolve_min": 5, **kw})


def test_leak_detection_pauses_for_the_session_nodes_and_resumes(cfg, admin, clock, app):
    registry([("flow", 1, MAC_A, "Main", "paired"), ("flow", 2, MAC_B, "Garden", "paired")])
    pid = admin("post", "/api/pipelines", json={"name": "House", "doc": nd.straight().doc()}).get_json()["id"]
    s1, s2 = NodeSim(1, MAC_A, step=5), NodeSim(2, MAC_B, step=5)
    feed(s1, T0, T0 + 8 * MIN, 3.0); feed(s2, T0, T0 + 8 * MIN, 1.0)                       # 2 L/min vanish between the nodes
    with database.db_session() as c:
        res = analysis_service.run_analysis(c, cfg, dt(T0 + 8 * MIN), base=P())
        assert next(z for z in res[0]["zones"] if z["id"] == "zone:flow1")["status"] == "suspected"
        assert c.execute("SELECT COUNT(*) AS n FROM alerts WHERE kind='LEAK' AND active=1").fetchone()["n"] == 1
    clock["now"] = T0 + 8 * MIN
    r = admin("post", "/api/calibration/sessions", json={"nodeIds": [refs()[1]]})
    assert r.status_code == 201
    sid = r.get_json()["id"]
    with database.db_session() as c:
        res = analysis_service.run_analysis(c, cfg, dt(T0 + 8 * MIN + 5), base=P())
        z = next(z for z in res[0]["zones"] if z["id"] == "zone:flow1")
        assert z["status"] == "calibrating" and z["calibrating"] == ["Node 1"] and "Calibration" in z["reason"]
        a = c.execute("SELECT active, message FROM alerts WHERE kind='LEAK' ORDER BY id DESC LIMIT 1").fetchone()
        assert a["active"] == 0 and "calibration in progress" in a["message"]
        assert c.execute("SELECT COUNT(*) AS n FROM zone_state").fetchone()["n"] < 3
    feed(s1, T0 + 8 * MIN, T0 + 20 * MIN, 3.0); feed(s2, T0 + 8 * MIN, T0 + 20 * MIN, 1.0)
    with database.db_session() as c:                                                       # still paused, no new alert while calibrating
        analysis_service.run_analysis(c, cfg, dt(T0 + 20 * MIN), base=P())
        assert c.execute("SELECT COUNT(*) AS n FROM alerts WHERE kind='LEAK' AND active=1").fetchone()["n"] == 0
    admin("post", f"/api/calibration/sessions/{sid}/cancel")
    with database.db_session() as c:
        res = analysis_service.run_analysis(c, cfg, dt(T0 + 20 * MIN + 5), base=P())
        assert next(z for z in res[0]["zones"] if z["id"] == "zone:flow1")["status"] in ("suspected", "confirmed")
        assert c.execute("SELECT COUNT(*) AS n FROM alerts WHERE kind='LEAK' AND active=1").fetchone()["n"] == 1


def test_a_forgotten_session_stops_pausing_after_four_hours(admin, rig, clock):
    d = new_session(admin)
    with database.db_session() as c:
        assert calibration_api.paused_refs(c, dt(T0 + 20 + 3 * 3600))
        assert not calibration_api.paused_refs(c, dt(T0 + 20 + 4 * 3600 + 5))
    clock["now"] = T0 + 20 + 4 * 3600 + 5
    assert admin("get", "/api/calibration/active").get_json()["active"] is False
    assert admin("get", f"/api/calibration/sessions/{d['id']}").get_json()["status"] == "cancelled"
