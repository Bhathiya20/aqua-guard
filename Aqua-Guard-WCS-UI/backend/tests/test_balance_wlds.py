"""Responsive balance (WLDS): signed D = Q_in - sum(Q_out) per 5 s window, SUSPICIOUS at once, POSSIBLE_LEAK after T_leak,
MEASUREMENT_INCONSISTENCY for outflow > inflow, PAUSED for missing data; interpolated windows; threshold learning."""
import json
from types import SimpleNamespace

import pytest

import analysis_service
import database
from analysis.balance import (INCONSISTENT, NORMAL, PAUSED, POSSIBLE_LEAK, SUSPICIOUS, FastWindow, balance_wlds, final_fast, learn_thresholds)
from analysis.buckets import Sample, fine_windows
from analysis.rules import ZoneParams
from tests import netdocs as nd
from tests.conftest import login
from tests.v2helpers import NodeSim, T0, dt, feed, registry

MAC_1, MAC_2 = "447b30854b88", "aabbcc000002"
W = 5


def zone_of(res, zid="zone:flow1"):
    return next(z for z in res[0]["zones"] if z["id"] == zid)


def run(cfg, now_s):
    with database.db_session() as c:
        return analysis_service.run_analysis(c, cfg, dt(now_s), base=ZoneParams(tz_offset_min=0))


def alerts(kind=None):
    with database.db_session() as c:
        rows = c.execute("SELECT * FROM alerts ORDER BY id").fetchall()
    return [dict(r) for r in rows if kind is None or r["kind"] == kind]


@pytest.fixture()
def admin(app):
    client = app.test_client()
    h = {"X-CSRF-Token": login(client)}
    return lambda m, url, **kw: getattr(client, m)(url, headers=h, **kw)


@pytest.fixture()
def pipe(app, admin):
    registry([("flow", 1, MAC_1, "Main", "paired"), ("flow", 2, MAC_2, "Garden", "paired")])
    r = admin("post", "/api/pipelines", json={"name": "House", "doc": nd.straight().doc()})
    assert r.status_code == 201 and r.get_json()["valid"]
    return r.get_json()["id"]


def sims():
    return NodeSim(1, MAC_1, step=2), NodeSim(2, MAC_2, step=2)


# ------------------------------------------------------------------ the six scenarios of the brief (database path)
def test_upstream_only_blow_is_suspicious_at_once_then_possible_leak_after_t_leak(cfg, pipe):
    s1, s2 = sims()
    feed(s1, T0, T0 + 30, 3.0); feed(s2, T0, T0 + 30, 0.0)                    # air blown into the upstream node only: D = +3 L/min
    z = zone_of(run(cfg, T0 + 40))
    assert z["balanceState"] == "suspicious" and z["status"] == "normal" and z["watching"] is True      # amber, not green, not yet an alert
    assert 20 <= z["wlds"]["tdS"] < 60 and z["wlds"]["tLeakS"] == 60 and z["wlds"]["dLpm"] > 2.5
    assert alerts() == []
    feed(s1, T0 + 30, T0 + 80, 3.0); feed(s2, T0 + 30, T0 + 80, 0.0)
    z = zone_of(run(cfg, T0 + 90))
    assert z["balanceState"] == "possible_leak" and z["status"] == "suspected" and z["wlds"]["tdS"] >= 60
    a = alerts("LEAK")
    assert len(a) == 1 and a[0]["state"] == "suspected" and a[0]["severity"] == "warning"


def test_downstream_only_is_a_measurement_inconsistency_not_a_leak(cfg, pipe):
    s1, s2 = sims()
    feed(s1, T0, T0 + 40, 0.0); feed(s2, T0, T0 + 40, 3.0)                    # outflow larger than inflow
    z = zone_of(run(cfg, T0 + 50))
    assert z["balanceState"] == "inconsistent" and z["status"] == "normal" and z["level"] == 0 and z["wlds"]["dLpm"] < -2.5
    assert alerts("LEAK") == [] and alerts("measurement_inconsistency") == []                  # not yet T_leak
    feed(s1, T0 + 40, T0 + 90, 0.0); feed(s2, T0 + 40, T0 + 90, 3.0)
    z = zone_of(run(cfg, T0 + 100))
    assert z["balanceState"] == "inconsistent" and z["level"] == 0
    assert alerts("LEAK") == []
    m = alerts("measurement_inconsistency")
    assert len(m) == 1 and m[0]["active"] == 1 and m[0]["severity"] == "warning" and "not a leak" in m[0]["message"]
    feed(s1, T0 + 90, T0 + 130, 2.0); feed(s2, T0 + 90, T0 + 130, 2.0)                        # back inside the band: the warning clears by itself
    z = zone_of(run(cfg, T0 + 140))
    assert z["balanceState"] == "normal" and alerts("measurement_inconsistency")[0]["active"] == 0


def test_balanced_flow_is_normal_and_green(cfg, pipe):
    s1, s2 = sims()
    feed(s1, T0, T0 + 90, 2.5); feed(s2, T0, T0 + 90, 2.5)
    z = zone_of(run(cfg, T0 + 100))
    assert z["balanceState"] == "normal" and z["status"] == "normal" and z["watching"] is False and alerts() == []
    assert abs(z["wlds"]["dLpm"]) < 0.1 and z["wlds"]["qInLpm"] == pytest.approx(2.5, abs=0.1)


def test_a_missing_node_pauses_the_check_instead_of_showing_normal(cfg, pipe):
    s1, _ = sims()
    feed(s1, T0, T0 + 90, 2.0)                                                  # node 2 never reports
    z = zone_of(run(cfg, T0 + 100))
    assert z["balanceState"] == "paused" and z["status"] == "incomplete" and alerts() == []


def test_a_node_that_goes_silent_pauses_within_seconds(cfg, pipe):
    s1, s2 = sims()
    feed(s1, T0, T0 + 120, 2.0); feed(s2, T0, T0 + 60, 2.0)                    # node 2 stops at 60 s
    assert zone_of(run(cfg, T0 + 70))["balanceState"] in ("normal", "paused")
    z = zone_of(run(cfg, T0 + 110))
    assert z["balanceState"] == "paused" and z["status"] == "incomplete"      # 50 s later: never "normal", never zero flow


def test_one_noisy_window_is_not_a_state(cfg, pipe):
    s1, s2 = sims()
    feed(s1, T0, T0 + 60, lambda t: 9.0 if T0 + 30 <= t < T0 + 34 else 2.0)      # one 4 s burst on one meter
    feed(s2, T0, T0 + 60, 2.0)
    z = zone_of(run(cfg, T0 + 70))
    assert z["balanceState"] == "normal" and alerts() == []


def test_demo_mode_uses_a_ten_second_t_leak(cfg, admin, pipe):
    s1, s2 = sims()
    feed(s1, T0, T0 + 30, 3.0); feed(s2, T0, T0 + 30, 0.0)
    assert admin("put", f"/api/pipelines/{pipe}/demo", json={"on": True}).status_code == 200
    with database.db_session() as c:
        c.execute("UPDATE pipelines SET demo_until=?", (T0 + 3600,))
    z = zone_of(run(cfg, T0 + 40))
    assert z["wlds"]["tLeakS"] == 10 and z["balanceState"] == "possible_leak" and z["status"] == "suspected"
    assert alerts("LEAK")[0]["demo"] == 1


# ------------------------------------------------------------------ pure state machine
def wins(pattern, p=None, start=1000):
    """pattern: one char per 5 s window: + (D above x+), - (below -x-), o (normal), . (incomplete)."""
    out = []
    for i, ch in enumerate(pattern):
        ts = start + i * W
        if ch == ".":
            out.append(FastWindow(ts, None, None, None, None, None, False, "high"))
        else:
            d = {"+": 0.5, "-": -0.5, "o": 0.0}[ch]                         # litres per 5 s: 0.5 L = 6 L/min
            out.append(FastWindow(ts, 1.0 + d, 1.0, d, 0.05, 0.05, True, "high"))
    return out


Z = SimpleNamespace(id="z")


def state(pattern, p=None, now=None):
    p = p or ZoneParams()
    ws = wins(pattern)
    now = now if now is not None else (ws[-1].ts + W + p.fast_grace_s if ws else 2000)
    r = balance_wlds(Z, final_fast(ws, now, p), now, p)
    return r.extra["state"], r.extra["tdS"], r.level


def test_state_machine_thresholds():
    assert state("oooooooo")[0] == NORMAL
    assert state("ooooooo+")[0] == NORMAL                                      # a single window is noise
    assert state("oooooo++")[0] == SUSPICIOUS
    s, td, lv = state("o" * 4 + "+" * 8)                                      # 8 windows = 40 s of 60 s
    assert (s, lv) == (SUSPICIOUS, 0) and 40 <= td < 60
    s, td, lv = state("o" * 4 + "+" * 14)                                     # 70 s
    assert (s, lv) == (POSSIBLE_LEAK, 1) and td >= 60
    assert state("o" * 4 + "+" * 14, ZoneParams(t_leak_s=10))[0] == POSSIBLE_LEAK
    assert state("o" * 4 + "-" * 3)[0] == INCONSISTENT
    assert state("o" * 4 + "." * 7)[0] == PAUSED                              # no complete window among the newest six
    assert state("")[0] == PAUSED


def test_a_single_normal_window_inside_a_run_does_not_reset_the_timer_but_two_do():
    s, td, _ = state("o" * 3 + "+" * 5 + "o" + "+" * 4)
    assert s == SUSPICIOUS and td >= 55                                       # 10 windows of run (+ grace), counting through one blip
    assert state("o" * 3 + "+" * 6 + "o" + "+" * 5)[2] == 1                  # ... and 12 windows reach T_leak
    s, td, _ = state("o" * 3 + "+" * 10 + "oo" + "+" * 3)
    assert s == SUSPICIOUS and td < 30                                        # two normal windows ended the first run
    p = ZoneParams()
    _, td, lv = state("+" * 40 + "." * 3 + "+" * 4, p)                         # a 15 s hole is bridged
    assert lv == 1 and td >= 100
    _, td, lv = state("+" * 40 + "." * 8 + "+" * 4, p)                         # a 40 s hole separates two episodes
    assert lv == 0 and td < 30


def test_confirmed_after_confirm_min_and_clear_after_resolve_min():
    p = ZoneParams(confirm_min=2, resolve_min=0.5)                            # 2 min / 30 s
    assert state("+" * 30, p)[2] == 2
    ws = wins("+" * 30 + "o" * 5)
    now = ws[-1].ts + W + p.fast_grace_s
    r = balance_wlds(Z, final_fast(ws, now, p), now, p)
    assert r.extra["state"] == NORMAL and r.clear is False                    # D is back in band but the leak is only closed after resolve_min of normal flow
    ws = wins("+" * 30 + "o" * 8)
    now = ws[-1].ts + W + p.fast_grace_s
    assert balance_wlds(Z, final_fast(ws, now, p), now, p).clear is True


def test_band_edges_use_a_signed_difference():
    p = ZoneParams()
    ws = [FastWindow(1000 + i * W, 1.0, 1.0 - 0.025, 0.025, 0.025, 0.025, True, "high") for i in range(10)]   # D == x+ exactly: not above it
    assert balance_wlds(Z, final_fast(ws, 1000 + 10 * W + 8, p), 1000 + 10 * W + 8, p).extra["state"] == NORMAL


# ------------------------------------------------------------------ windows: phase independence, holes, learning
def test_windows_of_two_nodes_with_different_report_phases_do_not_create_a_difference():
    a = [Sample(t, 0.2) for t in range(1000, 1200, 2)]                         # 6 L/min, reports on even seconds
    b = [Sample(t, 0.2) for t in range(1001, 1201, 2)]                         # same flow, odd seconds
    wa, wb = fine_windows(a, 1010, 1190, 5), fine_windows(b, 1010, 1190, 5)
    assert all(w.complete for w in wa.values()) and all(abs(wa[k].liters - wb[k].liters) < 1e-9 for k in wa)
    assert wa[1010].liters == pytest.approx(0.5)                              # 6 L/min x 5 s


def test_a_hole_or_missing_sequence_makes_the_window_incomplete_never_zero():
    s = [Sample(t, 0.2) for t in (1000, 1002, 1004, 1006, 1022)]               # 16 s without data
    w = fine_windows(s, 1000, 1025, 5)
    assert w[1000].complete and not w[1005].complete and not w[1010].complete and not w[1015].complete
    s2 = [Sample(1000, 0.2), Sample(1002, 0.2), Sample(1004, 0.2, gap_before=1), Sample(1006, 0.2)]
    w2 = fine_windows(s2, 1000, 1010, 5)
    assert not w2[1000].complete                                               # the sample after a loss may hold earlier volume


def test_learned_thresholds_are_mean_plus_minus_three_sd():
    p = ZoneParams()
    import random
    rnd = random.Random(3)
    ser = []
    for i in range(60):
        d_lpm = 0.05 + rnd.gauss(0, 0.05)                                       # a slightly positive offset with noise, 3 L/min flow
        d = d_lpm * W / 60.0
        q = 3.0 * W / 60.0
        ser.append(FastWindow(1000 + i * W, q, q - d, d, 0.025, 0.025, True, "high"))
    r = learn_thresholds(ser, p)
    assert r["windows"] == 60 and r["usable"] is True and r["warnings"] == []
    assert r["xPlusLpm"] == pytest.approx(max(0.3, r["meanDLpm"] + 3 * r["sdDLpm"]), abs=0.002)
    assert r["xMinusLpm"] == pytest.approx(max(0.3, 3 * r["sdDLpm"] - r["meanDLpm"]), abs=0.002)
    noisy = [FastWindow(1000 + i * W, 0.25, 0.25 - (0.1 if i % 2 else -0.1), 0.1 if i % 2 else -0.1, 0.025, 0.025, True, "high") for i in range(40)]
    big = learn_thresholds(noisy, p)
    assert big["xPlusLpm"] > 0.3 and any("not a clean" in w for w in big["warnings"])
    assert learn_thresholds([], p)["usable"] is False
    flow0 = [FastWindow(1000 + i * W, 0.0, 0.0, 0.0, 0.025, 0.025, True, "high") for i in range(40)]
    assert any("almost no flow" in w for w in learn_thresholds(flow0, p)["warnings"])


def test_learn_compute_on_stored_samples_and_threshold_endpoints(cfg, admin, pipe):
    s1, s2 = sims()
    feed(s1, T0, T0 + 150, 2.0); feed(s2, T0, T0 + 150, 2.0)
    with database.db_session() as c:
        row = c.execute("SELECT p.id, p.name, p.active_version, p.demo_until, v.doc FROM pipelines p JOIN pipeline_versions v ON v.pipeline_id=p.id AND v.version=p.active_version").fetchone()
        res = analysis_service.learn_compute(c, dict(row), "zone:flow1", T0 + 10, T0 + 140)
    assert res["windows"] >= 24 and res["usable"] and res["xPlusLpm"] == 0.3 and abs(res["meanDLpm"]) < 0.05

    url = f"/api/pipelines/{pipe}/zones/zone:flow1/thresholds"
    cur = admin("get", url).get_json()
    assert cur["xPlusLpm"] == 0.3 and cur["tLeakS"] == 60 and cur["custom"] is False and cur["strong"] is True
    assert admin("put", url, json={"xPlusLpm": 0}).status_code == 400
    assert admin("put", url, json={"tLeakS": "x"}).status_code == 400
    assert admin("put", url, json={}).status_code == 400
    r = admin("put", url, json={"xPlusLpm": 0.8, "xMinusLpm": 0.6, "tLeakS": 30, "source": "learned"}).get_json()
    assert (r["xPlusLpm"], r["xMinusLpm"], r["tLeakS"], r["source"], r["custom"]) == (0.8, 0.6, 30, "learned", True)
    assert admin("put", f"/api/pipelines/{pipe}/zones/zone:flow2/thresholds", json={"xPlusLpm": 1}).status_code == 409      # weak zone: balance cannot prove a leak
    s3 = zone_of(run(cfg, T0 + 160))
    assert s3["settings"]["xPlusLpm"] == 0.8 and s3["settings"]["tLeakS"] == 30                # in force in the analysis
    assert admin("delete", url).get_json()["custom"] is False
    with database.db_session() as c:
        assert c.execute("SELECT COUNT(*) n FROM audit_log WHERE action LIKE 'zone_thresholds%'").fetchone()["n"] == 2


def test_learning_session_api_start_stop_cancel(cfg, admin, pipe):
    url = f"/api/pipelines/{pipe}/zones/zone:flow1/learn"
    assert admin("get", url).get_json() == {"session": None}
    assert admin("post", url, json={"action": "stop"}).status_code == 409
    assert admin("post", url, json={"action": "x"}).status_code == 400
    r = admin("post", url, json={"action": "start"}).get_json()
    assert r["session"]["status"] == "running" and "progress" in r["session"]
    r = admin("post", url, json={"action": "stop"}).get_json()
    assert r["session"]["status"] == "done" and r["session"]["result"]["usable"] is False        # nothing flowed: the result says so
    admin("post", url, json={"action": "start"})
    assert admin("post", url, json={"action": "cancel"}).get_json()["session"]["status"] == "cancelled"
