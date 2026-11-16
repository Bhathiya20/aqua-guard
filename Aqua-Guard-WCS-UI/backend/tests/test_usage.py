"""Per-node usage profiles (duration detector): pure state machine + the database lifecycle (one alert per event, auto-resolve, schedules, night flow, demo)."""
import pytest

import analysis_service
import database
import usage_service
from analysis import usage as U
from tests.conftest import login
from tests.v2helpers import NodeSim, T0, dt, feed, registry

MAC = "447b30854b88"
OFF = 330                       # Asia/Colombo
P = dict(U.FACTORY)             # defaults: T_lim 15 min / T_max 30 min, quiet 22:00-05:00, on 0.3 / off 0.15, no-flow confirm 10 s


def prof(**kw):
    return dict(P, **kw)


# --------------------------------------------------------------------------- pure state machine
def test_noisy_flow_does_not_reset_the_timer():
    p = prof(t_lim_s=60, t_max_s=120)
    run = U.Run()
    smp = []
    for i in range(0, 100, 2):                                   # 100 s of flow that jitters: dips to 0.2 (between off and on) and short zero drops (< no-flow confirm)
        q = 0.0 if i in (30, 32, 34) else 0.2 if i % 10 == 0 and i else 2.0
        smp.append((1000.0 + i, q))
    U.advance(run, smp, p)
    c = U.classify(run, 1100.0, p, OFF)
    assert run.flowing and run.flow_since == 1000.0                 # one run since the first sample: the jitter never restarted it
    assert c["status"] == "PROLONGED_USAGE" and c["tFlowS"] >= 98


def test_flow_stops_only_after_the_no_flow_confirm():
    p = prof()
    run = U.Run()
    U.advance(run, [(1000.0 + i, 2.0) for i in range(0, 20, 2)], p)
    U.advance(run, [(1020.0 + i, 0.0) for i in range(0, 8, 2)], p)
    assert run.flowing                                              # 6 s without flow < 10 s confirm
    U.advance(run, [(1028.0 + i, 0.0) for i in range(0, 8, 2)], p)
    assert not run.flowing and U.classify(run, 1034.0, p, OFF)["status"] == "NO_FLOW"


def test_no_data_is_unknown_never_normal():
    assert U.classify(U.Run(), 5000.0, prof(), OFF)["status"] == "UNKNOWN"
    run = U.Run()
    U.advance(run, [(1000.0, 0.0)], prof())
    assert U.classify(run, 1000.0 + U.FRESH_S + 5, prof(), OFF)["status"] == "UNKNOWN"      # newest sample is stale


def test_presets_resolve_and_a_node_without_edits_uses_the_defaults():
    d = prof(t_lim_s=111, t_max_s=222)
    assert U.resolve(d, None)["t_lim_s"] == 111
    wr = U.resolve(d, {"preset": "washroom", "fields": {}})
    assert (wr["t_lim_s"], wr["t_max_s"]) == (90, 240)
    assert U.resolve(d, {"preset": "laboratory", "fields": {}})["t_max_s"] == int(7.5 * 3600)
    assert U.resolve(d, {"preset": "canteen", "fields": {"t_lim_s": 400}})["t_lim_s"] == 400
    assert U.minimal_override(d, dict(wr, preset="washroom")) == {"preset": "washroom", "fields": {}}      # nothing stored that the preset already gives


def test_validation_rejects_nonsense():
    assert U.validate(prof()) == []
    assert U.validate(prof(t_max_s=10, t_lim_s=60))
    assert U.validate(prof(flow_off_lpm=1.0, flow_on_lpm=0.5))
    assert U.validate(prof(quiet_start="25:00"))
    assert U.validate(prof(schedule=[{"days": [0], "start": "17:00", "end": "08:00"}]))


# --------------------------------------------------------------------------- database lifecycle
@pytest.fixture()
def admin(app):
    c = app.test_client()
    h = {"X-CSRF-Token": login(c)}
    return lambda m, url, **kw: getattr(c, m)(url, headers=h, **kw)


@pytest.fixture()
def node(app):
    registry([("flow", 1, MAC, "Washroom tap", "paired")])
    with database.db_session() as c:
        return c.execute("SELECT id FROM nodes WHERE number=1").fetchone()["id"]


def tick(t):
    with database.db_session() as c:
        usage_service.run_usage(c, dt(t))


def usage_alerts():
    with database.db_session() as c:
        return [dict(r) for r in c.execute("SELECT * FROM alerts WHERE alert_key LIKE 'USAGE:%' ORDER BY id").fetchall()]


def run_flow(sim, t0, secs, rate=2.0, every=10):
    """Deliver `secs` seconds of flow and run the check every `every` seconds."""
    t = t0
    while t < t0 + secs:
        n = min(every, t0 + secs - t)
        feed(sim, t, t + n, rate)
        t += n
        tick(t)
    return t


def set_profile(admin, node, **body):
    r = admin("put", f"/api/nodes/{node}/usage-profile", json=body)
    assert r.status_code == 200, r.get_json()


def test_washroom_tap_open_four_minutes_is_critical_and_auto_resolves(app, admin, node):
    set_profile(admin, node, preset="washroom")
    sim = NodeSim(1, MAC, step=2)
    t = run_flow(sim, T0, 60)                                       # 1 min: still normal
    assert usage_alerts() == []
    t = run_flow(sim, t, 60)                                        # 2 min > T_lim 90 s: warning
    a = usage_alerts()
    assert len(a) == 1 and a[0]["kind"] == "usage_prolonged" and a[0]["severity"] == "warning" and a[0]["active"] == 1 and a[0]["node_ref"] == node
    t = run_flow(sim, t, 150)                                       # 4.5 min > T_max 240 s: escalates IN PLACE (one alert per event)
    a = usage_alerts()
    assert len(a) == 1 and a[0]["kind"] == "usage_continuous" and a[0]["severity"] == "critical" and a[0]["confirmed_at"]
    assert "Washroom tap" in a[0]["message"] and a[0]["lost_l"] > 5          # 4+ min at 2 L/min: the volume is accumulated
    t = run_flow(sim, t, 30, rate=0.0)                              # tap closed: no-flow confirmed -> resolved by itself
    a = usage_alerts()
    assert len(a) == 1 and a[0]["active"] == 0 and a[0]["cleared_at"] and a[0]["resolved_by"] is None


def test_lab_long_use_inside_its_schedule_is_fine_outside_it_alerts(app, admin, node):
    sched = [{"days": [0, 1, 2, 3, 4], "start": "08:00", "end": "17:00"}]      # weekdays; T0 is Thursday
    set_profile(admin, node, preset="custom", tLimS=60, tMaxS=120, schedule=sched)
    inside = T0 - 5 * 3600 - 1800                                     # 12:00 local on Thursday
    sim = NodeSim(1, MAC, step=2, t_boot=inside - 10)
    run_flow(sim, inside, 400)                                        # 6+ minutes, far over T_max: allowed by the schedule
    assert usage_alerts() == []
    with database.db_session() as c:
        st = c.execute("SELECT status, flowing FROM usage_state").fetchone()
    assert st["flowing"] == 1 and st["status"] == "NORMAL"           # a 6 minute run over T_max, but inside the allowed schedule
    outside = T0                                                      # 17:30 local, after the window; quiet hours not reached
    sim2 = NodeSim(1, MAC, step=2, boot=2000, t_boot=outside - 10)
    run_flow(sim2, outside, 200)
    a = usage_alerts()
    assert len(a) == 1 and a[0]["kind"] == "usage_continuous"


def test_night_flow_alerts_unless_the_node_may_run_24h(app, admin, node):
    night = T0 + int(5.5 * 3600)                                      # 23:30 local
    sim = NodeSim(1, MAC, step=2, t_boot=night - 10)
    run_flow(sim, night, 40)
    assert usage_alerts() == []                                       # 40 s < the 60 s confirm: not yet
    run_flow(sim, night + 40, 40)
    a = usage_alerts()
    assert len(a) == 1 and a[0]["kind"] == "night_flow" and a[0]["severity"] == "critical"
    assert "quiet hours" in a[0]["message"]
    # the same flow on a node that is allowed to run 24 h gives nothing
    with database.db_session() as c:
        c.execute("DELETE FROM alerts"); c.execute("DELETE FROM usage_state")
    set_profile(admin, node, preset="custom", allow24h=True)
    sim2 = NodeSim(1, MAC, step=2, boot=3000, t_boot=night + 4000)
    run_flow(sim2, night + 5000, 120)
    assert usage_alerts() == []


def test_missing_data_is_unknown_not_normal(app, admin, node):
    assert admin("get", "/api/usage/live").get_json()["nodes"][0]["status"] == "UNKNOWN"


def test_default_settings_page_and_audit_trail(app, admin, node):
    r = admin("put", "/api/usage/defaults", json={"tLimS": 120, "tMaxS": 300, "quietStart": "21:00"})
    assert r.status_code == 200 and r.get_json()["profile"]["tLimS"] == 120
    assert admin("get", f"/api/nodes/{node}/usage-profile").get_json()["profile"]["tLimS"] == 120        # an unedited node follows the defaults
    assert admin("put", "/api/usage/defaults", json={"tLimS": 500, "tMaxS": 100}).status_code == 400
    set_profile(admin, node, preset="canteen")
    p = admin("get", f"/api/nodes/{node}/usage-profile").get_json()
    assert p["custom"] is True and p["profile"]["tLimS"] == 300 and p["profile"]["tMaxS"] == 600
    assert admin("delete", f"/api/nodes/{node}/usage-profile").status_code == 200
    assert admin("get", f"/api/nodes/{node}/usage-profile").get_json()["profile"]["tLimS"] == 120
    audit = admin("get", "/api/usage/audit").get_json()["audit"]
    assert [x["action"] for x in audit][:3] == ["usage_node", "usage_node", "usage_defaults"]
    assert audit[0]["user"] == "admin@test.local" and audit[0]["ts"]


def test_demo_limits_raise_an_alert_in_a_minute_and_expire(app, admin, node):
    assert admin("put", f"/api/nodes/{node}/usage-profile/demo", json={"on": True}).status_code == 200
    sim = NodeSim(1, MAC, step=2, t_boot=T0 - 10)
    run_flow(sim, T0, 100)                                            # demo T_lim 30 s / T_max 60 s
    a = usage_alerts()
    assert len(a) == 1 and a[0]["kind"] == "usage_continuous" and a[0]["demo"] == 1 and "[demo]" in a[0]["message"]
    with database.db_session() as c:
        c.execute("DELETE FROM usage_settings WHERE scope LIKE 'demo:%'")            # demo switched off (or its hour is over)
    run_flow(sim, T0 + 100, 10)
    assert usage_alerts()[0]["active"] == 0 and "demo ended" in usage_alerts()[0]["message"]


def test_the_balance_run_also_runs_the_usage_check(app, admin, node):
    sim = NodeSim(1, MAC, step=2, t_boot=T0 - 10)
    set_profile(admin, node, preset="custom", tLimS=20, tMaxS=300)
    feed(sim, T0, T0 + 60, 2.0)
    with database.db_session() as c:
        analysis_service.run_analysis(c, None, dt(T0 + 60))               # the zone analysis tick (no pipeline at all) still checks the node
    assert usage_alerts() and usage_alerts()[0]["kind"] == "usage_prolonged"
