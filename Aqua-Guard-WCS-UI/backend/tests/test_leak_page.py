"""Leak Detection page data: per-zone inflow/outflow nodes with live flow, the node table (state, last seen, zones in/out), pipeline list `preset`,
and calibration rigs never being used for leak detection."""
import pytest

import database
from tests import netdocs as nd
from tests.conftest import login
from tests.v2helpers import NodeSim, T0, feed, registry

MAC_1, MAC_2 = "447b30854b88", "aabbcc000002"
MIN = 60


@pytest.fixture()
def admin(app):
    c = app.test_client()
    h = {"X-CSRF-Token": login(c)}
    return lambda m, url, **kw: getattr(c, m)(url, headers=h, **kw)


@pytest.fixture()
def two_nodes(app):
    registry([("flow", 1, MAC_1, "Main", "paired"), ("flow", 2, MAC_2, "Garden", "paired")])


def make(admin, doc, name="House"):
    r = admin("post", "/api/pipelines", json={"name": name, "doc": doc.doc() if hasattr(doc, "doc") else doc})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def status(admin, pid, monkeypatch, now):
    import analysis_service
    import pipeline_api
    from tests.v2helpers import dt
    monkeypatch.setattr(pipeline_api, "utc_now", lambda: dt(now))
    return admin("get", f"/api/pipelines/{pid}/status").get_json()


def test_zone_lists_its_inflow_and_outflow_nodes_with_live_flow(admin, two_nodes, monkeypatch):
    pid = make(admin, nd.straight())
    s1, s2 = NodeSim(1, MAC_1, step=5), NodeSim(2, MAC_2, step=5)
    feed(s1, T0, T0 + 3 * MIN, 3.0)
    feed(s2, T0, T0 + 3 * MIN, 1.0)
    st = status(admin, pid, monkeypatch, T0 + 3 * MIN)
    z = next(x for x in st["zones"] if x["id"] == "zone:flow1")
    assert [n["number"] for n in z["inflow"]] == [1] and [n["number"] for n in z["outflow"]] == [2]
    assert z["inflow"][0]["flowLpm"] == pytest.approx(3.0, abs=0.3) and z["outflow"][0]["flowLpm"] == pytest.approx(1.0, abs=0.3)
    assert z["inflow"][0]["state"] == "online" and z["outflow"][0]["label"] == "Node 2"
    assert z["settings"]["thresholdLpm"] == 0.3 and z["settings"]["tolerancePct"] == 10.0
    weak = next(x for x in st["zones"] if x["id"] == "zone:flow2")
    assert [n["number"] for n in weak["inflow"]] == [2] and weak["outflow"] == [] and weak["outletCount"] == 1
    src = next(x for x in st["zones"] if x["id"] == "zone:source")
    assert src["inflow"] == []                                                      # nothing measures the water entering the first zone


def test_node_table_has_state_last_seen_and_the_zones_each_node_feeds(admin, two_nodes, monkeypatch):
    pid = make(admin, nd.straight())
    feed(NodeSim(1, MAC_1, step=5), T0, T0 + 3 * MIN, 3.0)                        # Node 2 never reports
    st = status(admin, pid, monkeypatch, T0 + 3 * MIN)
    nodes = {n["number"]: n for n in st["nodes"]}
    assert set(nodes) == {1, 2}
    assert [(z["id"], z["role"]) for z in nodes[1]["zones"]] == [("zone:flow1", "in")] or ("zone:flow1", "in") in [(z["id"], z["role"]) for z in nodes[1]["zones"]]
    roles2 = {(z["id"], z["role"]) for z in nodes[2]["zones"]}
    assert roles2 == {("zone:flow1", "out"), ("zone:flow2", "in")}
    assert nodes[2]["flowLpm"] is None and nodes[2]["lastSeen"] is None
    with database.db_session() as c:
        c.execute("UPDATE nodes SET out_of_service=1 WHERE number=2")
    st = status(admin, pid, monkeypatch, T0 + 3 * MIN)
    assert {n["number"]: n["state"] for n in st["nodes"]}[2] == "out_of_service"


def test_a_node_without_recent_data_shows_no_live_flow_and_offline(admin, two_nodes, monkeypatch):
    pid = make(admin, nd.straight())
    feed(NodeSim(1, MAC_1, step=5), T0, T0 + 3 * MIN, 3.0)
    feed(NodeSim(2, MAC_2, step=5), T0, T0 + 3 * MIN, 1.0)
    with database.db_session() as c:
        c.execute("UPDATE nodes SET last_seen=? WHERE number=1", ("2026-01-01 12:00:00",))
    st = status(admin, pid, monkeypatch, T0 + 20 * MIN)                          # 17 minutes after the last reading
    n1 = next(n for n in st["nodes"] if n["number"] == 1)
    assert n1["flowLpm"] is None and n1["state"] == "offline" and n1["ageS"] > 600


def test_pipeline_list_reports_the_preset_and_rigs_cannot_be_activated(admin, two_nodes):
    with database.db_session() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM nodes ORDER BY number")]
    rig = admin("post", "/api/pipelines/from-preset", json={"preset": "calibration_rig", "nodeIds": ids}).get_json()["id"]
    house = make(admin, nd.straight(), "Plain")
    lst = {p["id"]: p for p in admin("get", "/api/pipelines").get_json()["pipelines"]}
    assert lst[rig]["preset"] == "calibration_rig" and lst[house]["preset"] is None
    r = admin("post", f"/api/pipelines/{rig}/activate", json={"version": 1})
    assert r.status_code == 409 and r.get_json()["code"] == "not_for_detection"
    assert next(p for p in admin("get", "/api/pipelines").get_json()["pipelines"] if p["id"] == house)["activeVersion"] == 1       # the real pipeline stays active
    doc = admin("get", f"/api/pipelines/{rig}").get_json()["doc"]
    r = admin("post", "/api/pipelines", json={"name": "Rig copy", "doc": doc})
    assert r.status_code == 201 and r.get_json()["activeVersion"] is None
    r = admin("put", f"/api/pipelines/{rig}", json={"doc": doc})
    assert r.status_code == 201 and r.get_json()["activated"] is False
