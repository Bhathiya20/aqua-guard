"""Acceptance scenarios: the N-node plant simulator -> v2 ingest -> pipeline analysis -> alerts.  Simulated data, not hardware evidence."""
import pytest

import analysis_service
import database
import sim_v2
from analysis.rules import ZoneParams
from sim_v2 import Faults, Plant, deliver_all
from tests import netdocs as nd
from tests.conftest import login
from tests.v2helpers import dt

DAY0 = 1_767_225_600                      # 2026-01-01 00:00:00 UTC
NOON = DAY0 + 12 * 3600
MIN = 60


def P(**kw):
    return ZoneParams(**{"tz_offset_min": 0, "suspect_min": 5, "confirm_min": 10, "resolve_min": 5, **kw})


@pytest.fixture()
def admin(app):
    c = app.test_client()
    h = {"X-CSRF-Token": login(c)}
    return lambda m, url, **kw: getattr(c, m)(url, headers=h, **kw)


def scenario(cfg, admin, doc, t0, seconds, leaks=None, faults=None, step=5, params=None, **doc_extra):
    d = doc.doc(**doc_extra) if hasattr(doc, "doc") else doc
    plant = Plant(d, leaks=leaks)
    n = deliver_all(cfg, plant, t0, seconds, faults, step)
    r = admin("post", "/api/pipelines", json={"name": "Sim", "doc": d})
    assert r.status_code == 201 and r.get_json()["valid"], r.get_json()
    with database.db_session() as c:
        res = analysis_service.run_analysis(c, cfg, dt(t0 + seconds), base=params or P())
    zones = {z["id"]: z for z in res[0]["zones"]}
    with database.db_session() as c:
        al = [dict(x) for x in c.execute("SELECT * FROM alerts WHERE kind='LEAK' ORDER BY id").fetchall()]
    return plant, zones, al, n


def test_leak_between_two_nodes_is_confirmed_and_located(cfg, admin):
    leaks = [{"part": "p1", "lpm": 1.5, "start": 0}]                                  # on p1, downstream of node 1 (pos .5), upstream of node 2
    plant, z, al, n = scenario(cfg, admin, nd.straight(), NOON, 30 * MIN, leaks)
    assert n > 500
    assert z["zone:flow1"]["status"] == "confirmed" and z["zone:flow1"]["class"] == "strong"
    assert z["zone:flow2"]["status"] != "confirmed" and z["zone:source"]["status"] == "not_monitored"
    assert [(a["zone_id"], a["state"], a["active"]) for a in al] == [("zone:flow1", "confirmed", 1)]
    b = z["zone:flow1"]["balance"]
    assert abs(b["unaccounted"] - 1.5) < 0.2 and b["qIn"] > b["qOut"]


def test_same_plant_without_a_leak_stays_quiet(cfg, admin):
    plant, z, al, _ = scenario(cfg, admin, nd.straight(), NOON, 30 * MIN)
    assert al == [] and z["zone:flow1"]["status"] == "normal"
    assert plant.flow(1, 5, NOON) >= 0


def test_a_leak_downstream_of_the_last_sensor_is_not_seen_by_the_strong_zone(cfg, admin):
    """Leak after node 2 is invisible to the balance between node 1 and node 2 (honest limit of a 'weak' zone)."""
    _, z, al, _ = scenario(cfg, admin, nd.straight(), NOON, 30 * MIN, [{"part": "p2", "lpm": 1.5, "start": 0, "pos": 1.0}])
    assert z["zone:flow1"]["status"] == "normal" and al == []


def test_silent_sensor_means_incomplete_data_never_a_leak(cfg, admin):
    _, z, al, _ = scenario(cfg, admin, nd.straight(), NOON, 30 * MIN, None, Faults(silent={2: [(0, 30 * MIN)]}))
    assert al == [] and z["zone:flow1"]["status"] in ("incomplete", "normal") and z["zone:flow1"]["completeBuckets"] == 0


def test_late_retransmission_is_bucketed_by_measurement_time(cfg, admin):
    faults = Faults(silent={2: [(10 * MIN, 14 * MIN)]}, late={2: 3 * MIN})
    _, z, al, _ = scenario(cfg, admin, nd.straight(), NOON, 22 * MIN, None, faults, params=P(suspect_min=3, confirm_min=6))
    assert al == [] and z["zone:flow1"]["status"] == "normal"
    with database.db_session() as c:
        late = c.execute("SELECT COUNT(*) n FROM flow_samples WHERE recovered=1").fetchone()["n"]
    assert late > 0


def test_night_flow_in_a_single_weak_zone(cfg, admin):
    """One node on the main supply (the real installation) finds a slow constant leak: water is still flowing at 03:00."""
    start = DAY0 + 1 * 3600 + 30 * MIN
    leaks = [{"part": "p2", "lpm": 0.6, "start": 0}]
    with database.db_session() as c:       # this short run (2 h 40 min) uses its own night window for the zone: a per-zone override of the quiet hours (pipeline id 1)
        c.execute("INSERT INTO usage_settings (scope, json) VALUES (?,?)", ("zone:1:zone:flow1", '{"quiet_start":"02:00","quiet_end":"04:00"}'))
    _, z, al, _ = scenario(cfg, admin, nd.main_only(), start, 2 * 3600 + 40 * MIN, leaks, step=5)
    f = z["zone:flow1"]
    assert f["timeRules"]["source"]["quietStart"] == "zone" and f["settings"]["nightStart"] == "02:00"
    assert f["class"] == "weak" and f["status"] in ("suspected", "confirmed") and any(r["rule"] == "night_flow" and r["level"] for r in f["rules"])
    assert [a["zone_id"] for a in al] == ["zone:flow1"]


def test_night_without_a_leak_is_clean(cfg, admin):
    start = DAY0 + 1 * 3600 + 30 * MIN
    _, z, al, _ = scenario(cfg, admin, nd.main_only(), start, 2 * 3600 + 40 * MIN, None, step=5)
    assert z["zone:flow1"]["status"] == "normal" and al == []


def test_node_reboot_resets_the_counter_without_a_false_leak_or_lost_volume(cfg, admin):
    faults = Faults(resets={1: [15 * MIN]})
    plant, z, al, _ = scenario(cfg, admin, nd.straight(), NOON, 30 * MIN, None, faults)
    assert al == []
    truth = sum(plant.flow(1, t, NOON) * 5 / 60.0 for t in range(0, 30 * MIN, 5))
    with database.db_session() as c:
        got = c.execute("SELECT SUM(delta_pulses*10.0/pulses_per_liter_x10) v, COUNT(DISTINCT boot_id) b FROM flow_samples WHERE node_ref=(SELECT id FROM nodes WHERE number=1)").fetchone()
    assert got["b"] == 2 and abs(got["v"] - truth) <= 0.02 * truth + 0.1


def test_unsynced_gateway_still_analyses_with_lower_confidence(cfg, admin):
    leaks = [{"part": "p1", "lpm": 1.5, "start": 0}]
    # without a gateway clock the backend receive time is used; the virtual clock is the message arrival time handed to the handler
    _, z, al, _ = scenario(cfg, admin, nd.straight(), NOON, 30 * MIN, leaks, Faults(unsynced=True))
    assert z["zone:flow1"]["status"] in ("suspected", "confirmed") and len(al) == 1
    assert z["zone:flow1"]["balance"]["confidence"] == "low"


def test_simulator_registry_matches_the_pipeline(cfg, admin):
    plant = Plant(nd.tee_tree().doc())
    assert sorted(plant.nodes) == [1, 2] and sim_v2.sim_mac(2) == "02:51:4D:00:00:02"
    assert [n["number"] for n in sim_v2.registry_json(plant)["nodes"]] == [1, 2]


def test_simulator_refuses_the_real_database(monkeypatch, tmp_path):
    import sim_guard
    monkeypatch.delenv("AQUAGUARD_DB_PATH", raising=False)
    with pytest.raises(SystemExit):
        sim_guard.require_demo_db()
    monkeypatch.setenv("AQUAGUARD_DB_PATH", "C:/x/database/aquaguard.db")
    with pytest.raises(SystemExit):
        sim_guard.require_demo_db()
    monkeypatch.setenv("AQUAGUARD_DB_PATH", "C:/x/.demo/demo.db")
    sim_guard.require_demo_db()
    with pytest.raises(SystemExit):
        sim_guard.require_demo_db(mqtt=True)
