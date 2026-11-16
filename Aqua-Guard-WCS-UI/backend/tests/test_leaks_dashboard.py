"""The dashboard's /api/leaks/* and alert endpoints now run on the pipeline analysis (the old two-node detector is gone)."""
import pytest

import analysis_service
import database
import services
from sim_v2 import Faults, Plant, deliver_all
from tests import netdocs as nd
from tests.conftest import login
from tests.test_simulator_v2 import NOON, MIN, P
from tests.v2helpers import dt


@pytest.fixture()
def admin(app):
    c = app.test_client()
    h = {"X-CSRF-Token": login(c)}
    return lambda m, url, **kw: getattr(c, m)(url, headers=h, **kw)


def run_scenario(cfg, admin, doc, seconds, leaks=None, faults=None, with_pipeline=True):
    d = doc.doc()
    deliver_all(cfg, Plant(d, leaks=leaks), NOON, seconds, faults, 5)
    if with_pipeline:
        assert admin("post", "/api/pipelines", json={"name": "Sim", "doc": d}).status_code == 201
    with database.db_session() as c:
        analysis_service.run_analysis(c, cfg, dt(NOON + seconds), base=P())


def test_no_pipeline_is_never_a_false_all_clear(cfg, admin):
    lv = admin("get", "/api/leaks/live").get_json()
    assert lv["state"] == "NOT_CONFIGURED" and lv["status"] == "unavailable" and lv["severity"] == "none" and lv["activeEvent"] is None
    assert admin("get", "/api/leaks/summary").get_json()["state"] == "NOT_CONFIGURED"


def test_pipeline_without_a_monitored_zone_is_not_configured(cfg, admin):
    admin("post", "/api/pipelines", json={"name": "Empty", "doc": nd.unmetered().doc()})
    with database.db_session() as c:
        analysis_service.run_analysis(c, cfg, dt(NOON), base=P())
    lv = admin("get", "/api/leaks/live").get_json()
    assert lv["state"] == "NOT_CONFIGURED" and "inflow node" in lv["reason"]


def test_healthy_system_is_normal_with_numbers(cfg, admin):
    run_scenario(cfg, admin, nd.straight(), 20 * MIN)
    lv = admin("get", "/api/leaks/live").get_json()
    assert lv["state"] == "NORMAL" and lv["status"] == "normal" and lv["activeEvent"] is None and lv["node1Active"] is True
    assert abs(lv["flowDifference"]) < 0.3 and lv["thresholds"]["suspectAfterS"] > 0
    assert admin("get", "/api/leaks/events").get_json() == []
    assert services.leak_state(database.get_db())["state"] == "NORMAL"


def test_confirmed_leak_flows_through_live_events_summary_and_alerts(cfg, admin):
    run_scenario(cfg, admin, nd.straight(), 30 * MIN, [{"part": "p1", "lpm": 1.5, "start": 0}])
    lv = admin("get", "/api/leaks/live").get_json()
    assert lv["state"] == "CONFIRMED" and lv["status"] == "leak" and lv["severity"] == "critical" and lv["zone"] == "zone:flow1"
    assert abs(lv["flowDifference"] - 1.5) < 0.3 and lv["node1Flow"] > lv["node2Flow"] and lv["activeEvent"]["state"] == "CONFIRMED"
    ev = admin("get", "/api/leaks/events").get_json()
    assert len(ev) == 1 and ev[0]["state"] == "CONFIRMED" and ev[0]["status"] == "active" and ev[0]["confirmedAt"]
    for k in ("node1Flow", "node2Flow", "flowDifference", "differencePercentage", "peakDifference"):      # the analytics page calls .toFixed() on these
        assert isinstance(ev[0][k], (int, float)), k
    assert ev[0]["peakDifference"] >= 1.2
    s = admin("get", "/api/leaks/summary").get_json()
    assert s["activeLeaks"] == 1 and s["suspectedLeaks"] == 0 and s["state"] == "CONFIRMED"
    al = admin("get", "/api/alerts?active=1").get_json()
    leak = [a for a in al if a["kind"] == "leak_confirmed"]
    assert len(leak) == 1 and leak[0]["zone"] == "zone:flow1" and leak[0]["pipelineVersion"] == 1 and leak[0]["severity"] == "critical"
    # the old two-node alert keys never come back
    assert not [a for a in al if a["key"] in ("LEAK_CONFIRMED", "LEAK_SUSPECTED", "FLOW_SENSOR_FAULT")]
    with database.db_session() as c:
        services.evaluate_alerts(c, cfg, dt(NOON + 30 * MIN))
        assert c.execute("SELECT COUNT(*) FROM alerts WHERE kind='LEAK' AND active=1").fetchone()[0] == 1     # the generic evaluator leaves leak alerts alone


def test_resolved_event_stays_in_the_history(cfg, admin):
    d = nd.straight().doc()
    plant = Plant(d, leaks=[{"part": "p1", "lpm": 1.5, "start": 0, "end": 14 * MIN}])
    deliver_all(cfg, plant, NOON, 26 * MIN, None, 5)
    admin("post", "/api/pipelines", json={"name": "Sim", "doc": d})
    with database.db_session() as c:
        analysis_service.run_analysis(c, cfg, dt(NOON + 12 * MIN), base=P())
        analysis_service.run_analysis(c, cfg, dt(NOON + 26 * MIN), base=P())
    ev = admin("get", "/api/leaks/events").get_json()
    assert len(ev) == 1 and ev[0]["state"] == "RESOLVED" and ev[0]["status"] == "resolved" and ev[0]["resolvedAt"]
    assert admin("get", "/api/leaks/live").get_json()["state"] == "NORMAL"
    s = admin("get", "/api/leaks/summary").get_json()
    assert s["resolvedEvents"] == 1 and s["activeLeaks"] == 0
