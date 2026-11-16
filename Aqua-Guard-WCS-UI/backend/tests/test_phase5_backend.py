"""Phase 5 backend support: live numbers per node, overview, v2 node alerts, alert resolve, draft preview, battery 'none'."""
import time
from datetime import datetime, timezone, timedelta

import pytest

from werkzeug.security import generate_password_hash

import database
import services
from ingest import fmt_ts
from tests import netdocs as nd
from tests.conftest import USER_EMAIL, USER_PW, login
from tests.v2helpers import NodeSim, deliver, heartbeat, info, registry

MAC_A, MAC_B = "447b30854b88", "aabbcc000002"


def real_now():
    return datetime.now(timezone.utc)


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def admin(client):
    h = {"X-CSRF-Token": login(client)}
    return lambda m, url, **kw: getattr(client, m)(url, headers=h, **kw)


@pytest.fixture()
def plant(app):
    n = real_now()
    registry([("flow", 1, MAC_A, "Main", "paired"), ("flow", 2, MAC_B, "Garden", "paired")], now=n)
    info(now=n)
    heartbeat(now=n, epoch=int(time.time()))


def feed_now(number, mac, seconds=60, rate=6.0, skip=(), battery=7400):
    t1 = int(time.time())
    sim = NodeSim(number, mac=mac, t_boot=t1 - seconds)
    for t, m in sim.msgs(t1 - seconds, t1, rate):
        m["batteryMv"] = battery
        if any(a <= t < b for a, b in skip):
            continue
        deliver(m, t)
    return sim


def test_nodes_carry_live_flow_and_usage(admin, plant):
    feed_now(1, MAC_A, seconds=60, rate=6.0)
    r = admin("get", "/api/nodes").get_json()["nodes"]
    one = next(x for x in r if x["number"] == 1)["live"]
    assert one["flowLpm"] == pytest.approx(6.0, abs=0.01)
    assert one["weekLiters"] == pytest.approx(6.0, abs=0.5)           # 60 s at 6 L/min
    assert one["todayLiters"] <= one["weekLiters"]
    assert one["ageS"] < 10 and one["lossPct24h"] == 0.0
    two = next(x for x in r if x["number"] == 2)["live"]
    assert two["flowLpm"] is None and two["lastFlowLpm"] is None and two["todayLiters"] == 0.0     # never reported: no invented zero flow


def test_missing_packets_are_counted_from_sequence_gaps(admin, plant):
    t = int(time.time())
    feed_now(1, MAC_A, seconds=60, skip=[(t - 30, t - 20)])             # 5 samples of 2 s never arrive
    live = next(x for x in admin("get", "/api/nodes").get_json()["nodes"] if x["number"] == 1)["live"]
    assert live["missedPackets24h"] >= 4 and live["lossPct24h"] > 5


def test_battery_none_clears_an_old_value(admin, plant):
    feed_now(1, MAC_A, seconds=10, battery=7400)
    assert next(x for x in admin("get", "/api/nodes").get_json()["nodes"] if x["number"] == 1)["batteryMv"] == 7400
    t1 = int(time.time())
    sim = NodeSim(1, mac=MAC_A, boot=2000, t_boot=t1 - 20)
    for t, m in sim.msgs(t1 - 4, t1, 1.0):
        m["batteryMv"] = None
        deliver(m, t)
    assert next(x for x in admin("get", "/api/nodes").get_json()["nodes"] if x["number"] == 1)["batteryMv"] is None


def test_overview_without_pipeline_sums_nodes_and_says_so(admin, plant):
    feed_now(1, MAC_A, rate=6.0)
    feed_now(2, MAC_B, rate=2.0)
    o = admin("get", "/api/summary").get_json()
    assert o["gateway"]["state"] == "online" and o["gateway"]["timeSynced"] is True
    assert o["nodes"] == {"total": 2, "online": 2, "offline": 0}
    assert o["total"]["basis"] == "sum_of_nodes" and o["total"]["flowLpm"] == pytest.approx(8.0, abs=0.05)
    assert o["total"]["weekLiters"] == pytest.approx(o["total"]["todayLiters"], abs=0.5) or o["total"]["weekLiters"] >= o["total"]["todayLiters"]
    assert o["pipeline"] is None and o["leaks"] == {"suspected": 0, "confirmed": 0}
    assert o["battery"]["withBattery"] == 2 and o["battery"]["low"] == []


def test_overview_uses_the_source_meter_of_the_active_pipeline(admin, plant):
    feed_now(1, MAC_A, rate=6.0)
    feed_now(2, MAC_B, rate=2.0)
    r = admin("post", "/api/pipelines", json={"name": "House", "doc": nd.straight().doc()})
    assert r.status_code == 201 and r.get_json()["activeVersion"] == 1
    o = admin("get", "/api/summary").get_json()
    assert o["total"]["basis"] == "source_meter" and o["total"]["node"] == 1 and o["total"]["flowLpm"] == pytest.approx(6.0, abs=0.05)
    assert o["pipeline"]["name"] == "House"


def run_alerts(cfg, now=None):
    with database.db_session() as c:
        services.evaluate_alerts(c, cfg, now or real_now())


def active(admin):
    return {a["key"]: a for a in admin("get", "/api/alerts?active=1").get_json()}


def test_v2_node_alerts_replace_the_v1_slot_alerts(admin, plant, cfg):
    feed_now(1, MAC_A, seconds=10)
    run_alerts(cfg)
    assert [k for k in active(admin) if k.startswith(("NODE_OFFLINE", "SENSOR_OFFLINE"))] == []
    later = real_now() + timedelta(seconds=cfg.node_offline_s + 40)                 # node 1 went silent; the gateway kept beating
    with database.db_session() as c:
        c.execute("UPDATE gateways SET last_heartbeat_at=?", (fmt_ts(later),))
    run_alerts(cfg, later)
    a = active(admin)
    assert any(k.startswith("NODE_OFFLINE:") for k in a), a
    assert not any(k.startswith("SENSOR_OFFLINE") for k in a)
    msg = next(v for k, v in a.items() if k.startswith("NODE_OFFLINE:"))
    assert msg["kind"] == "node_offline" and "Node 1" in msg["message"]


def test_gateway_offline_alert_uses_the_gateway_registry(admin, plant, cfg):
    run_alerts(cfg, real_now() + timedelta(seconds=cfg.gateway_offline_s + 30))
    a = active(admin)
    assert any(k.startswith("GATEWAY_OFFLINE:") for k in a) and a[next(k for k in a if k.startswith("GATEWAY_OFFLINE:"))]["kind"] == "gateway_offline"
    # a gateway outage must not also blame every node
    assert not any(k.startswith("NODE_OFFLINE:") for k in a)


def test_low_battery_alert_and_no_battery_is_not_low(admin, plant, cfg):
    feed_now(1, MAC_A, seconds=10, battery=6000)
    feed_now(2, MAC_B, seconds=10, battery=7600)
    run_alerts(cfg)
    a = active(admin)
    lows = [v for k, v in a.items() if k.startswith("LOW_BATTERY:")]
    assert len(lows) == 1 and "Node 1" in lows[0]["message"] and lows[0]["nodeRef"] is not None
    o = admin("get", "/api/summary").get_json()
    assert [b["number"] for b in o["battery"]["low"]] == [1]


def test_missing_packets_alert(admin, plant, cfg):
    t = int(time.time())
    feed_now(1, MAC_A, seconds=120, skip=[(t - 90, t - 40)])          # 25 of ~60 packets missing
    run_alerts(cfg)
    assert any(k.startswith("MISSING_PACKETS:") for k in active(admin))


def test_alert_acknowledge_and_resolve_are_admin_only(client, admin, plant, cfg):
    feed_now(1, MAC_A, seconds=10, battery=6000)
    run_alerts(cfg)
    aid = next(v["id"] for k, v in active(admin).items() if k.startswith("LOW_BATTERY:"))
    with database.db_session() as c:
        c.execute("INSERT INTO users (first_name,last_name,email,password_hash,role,must_change_password) VALUES ('U','S',?,?, 'user', 0)", (USER_EMAIL, generate_password_hash(USER_PW)))
    uc = client.application.test_client()
    uh = {"X-CSRF-Token": login(uc, USER_EMAIL, USER_PW)}
    assert uc.post(f"/api/alerts/{aid}/ack", headers=uh).status_code == 403
    assert uc.post(f"/api/alerts/{aid}/resolve", headers=uh, json={"reason": "test"}).status_code == 403
    assert admin("post", f"/api/alerts/{aid}/ack").status_code == 200
    assert admin("post", f"/api/alerts/{aid}/resolve").status_code == 400                      # a manual resolve needs a reason
    assert admin("post", f"/api/alerts/{aid}/resolve", json={"reason": "nonsense"}).status_code == 400
    assert admin("post", f"/api/alerts/{aid}/resolve", json={"reason": "sensor_fault", "note": "battery swapped"}).status_code == 200
    assert aid not in [v["id"] for v in active(admin).values()]
    assert admin("post", "/api/alerts/99999/resolve", json={"reason": "test"}).status_code == 404


def test_a_leak_can_be_resolved_by_hand_with_a_reason_and_lands_in_the_history(admin, plant):
    with database.db_session() as c:
        c.execute("INSERT INTO alerts (alert_key,kind,severity,message,active,created_at,state,suspected_at,lost_l,peak_unaccounted_lpm) VALUES ('LEAK:1:zone:flow1','LEAK','critical','x',1,?, 'confirmed',?,3.5,2.0)",
                  (fmt_ts(real_now()), fmt_ts(real_now())))
        aid = c.execute("SELECT id FROM alerts").fetchone()["id"]
    assert admin("post", f"/api/alerts/{aid}/resolve").status_code == 400
    assert admin("post", f"/api/alerts/{aid}/resolve", json={"reason": "false_alarm", "note": "valve test"}).status_code == 200
    h = admin("get", "/api/alerts/history").get_json()["history"]
    assert len(h) == 1 and h[0]["type"] == "leak" and h[0]["resolvedBy"] == "admin@test.local" and h[0]["reasonLabel"] == "False alarm" and h[0]["note"] == "valve test"
    assert h[0]["lostL"] == 3.5 and h[0]["peakLpm"] == 2.0 and h[0]["durationS"] is not None


def test_validate_gives_a_zone_preview_with_node_numbers_for_valid_docs(admin, plant):
    j = admin("post", "/api/pipelines/validate", json={"doc": nd.straight().doc()}).get_json()
    assert j["valid"] and j["partial"] is False
    z = {x["id"]: x for x in j["zones"]}
    assert z["zone:flow1"]["inNode"] == 1 and z["zone:flow1"]["outNodes"] == [2]


def test_validate_of_a_draft_with_errors_still_answers(admin, plant):
    d = nd.straight()
    d.add("dangling", "pipe")                                        # a pipe connected to nothing
    j = admin("post", "/api/pipelines/validate", json={"doc": d.doc()}).get_json()
    assert j["messages"] and "valid" in j
    if not j["valid"]:
        assert j.get("partial", True) is True or "zones" not in j


def test_the_four_node_example_pipeline_is_valid_and_has_the_expected_zones(admin):
    """examples/four-node-house.json (Node 2 on the main pipe, Nodes 3-5 on three branches) is what the click-through guide imports."""
    import json
    from pathlib import Path
    doc = json.loads((Path(__file__).resolve().parents[2] / "examples" / "four-node-house.json").read_text(encoding="utf-8"))
    registry([("flow", n, f"aabbcc00000{n}", nm, "paired") for n, nm in ((2, "Main outlet"), (3, "Com 34 last one"), (4, "Com 37 2nd one"), (5, "Com 36 3rd one"))])
    j = admin("post", "/api/pipelines/validate", json={"doc": doc}).get_json()
    assert j["valid"], j["messages"]
    assert {z["id"]: z["class"] for z in j["zones"]} == {"zone:source": "not_monitored", "zone:flow2": "strong", "zone:flow3": "weak", "zone:flow4": "weak", "zone:flow5": "weak"}
    assert j["coverage"]["text"] == "4 of 5 zones leak-monitored; 3 of 3 outlets covered"


def test_retired_detector_alerts_never_stay_active(admin, plant, cfg):
    now_s = fmt_ts(real_now())
    with database.db_session() as c:
        for key, kind in (("FLOW_SENSOR_FAULT", "sensor_fault"), ("LEAK_CONFIRMED", "leak"), ("SENSOR_OFFLINE:node2", "sensor_offline"), ("GATEWAY_OFFLINE", "gateway_offline")):
            c.execute("INSERT INTO alerts (alert_key,kind,severity,message,active,created_at) VALUES (?,?,'warning','old',1,?)", (key, kind, now_s))
    run_alerts(cfg)
    assert not [k for k in active(admin) if k in ("FLOW_SENSOR_FAULT", "LEAK_CONFIRMED", "SENSOR_OFFLINE:node2", "GATEWAY_OFFLINE")]


def test_two_gateways_never_guess_the_latest(admin, plant, cfg):
    """Overview shows the ONLINE gateway even if another one spoke later; nodes carry their gateway; adding a node needs an explicit gateway."""
    import gateway_v2
    n = real_now()
    with database.db_session() as c:
        c.execute("INSERT INTO gateways (gateway_id, name, first_seen, last_heartbeat_at) VALUES ('gw-other', 'gw-other', ?, ?)", (fmt_ts(n - timedelta(days=2)), fmt_ts(n - timedelta(days=2))))
        assert gateway_v2.sole_gateway(c) is None
    s = admin("get", "/api/summary").get_json()
    assert s["gateway"]["gatewayId"] != "gw-other" and s["gateways"]["total"] == 2 and s["gateways"]["online"] == 1
    assert {x["gatewayId"] for x in admin("get", "/api/nodes").get_json()["nodes"]} == {s["gateway"]["gatewayId"]}
    r = admin("post", "/api/nodes", json={"type": "flow", "mac": "AA:BB:CC:00:11:22", "code": "RPZ1-6DGP-ZA5Q"})
    assert r.status_code == 400 and "gateway" in r.get_data(as_text=True).lower()


def test_implausible_battery_is_not_reported_as_low_or_fine(admin, plant, cfg):
    with database.db_session() as c:
        c.execute("UPDATE nodes SET battery_mv=200 WHERE number=2")
        c.execute("UPDATE nodes SET battery_mv=6000 WHERE number=1")
    run_alerts(cfg)
    b = admin("get", "/api/summary").get_json()["battery"]
    assert [x["number"] for x in b["implausible"]] == [2] and [x["number"] for x in b["low"]] == [1]
    ids = {n["number"]: n["id"] for n in admin("get", "/api/nodes").get_json()["nodes"]}
    assert f"LOW_BATTERY:{ids[2]}" not in active(admin) and f"LOW_BATTERY:{ids[1]}" in active(admin)


def test_node_link_history(admin, plant, cfg):
    feed_now(1, MAC_A, seconds=20)
    r = admin("get", "/api/nodes/1/link?hours=2").get_json()
    assert r["hours"] == 2 and r["points"] and {"hour", "missed", "received", "lossPct", "rssi"} <= set(r["points"][0])
    assert admin("get", "/api/nodes/999/link").status_code == 404


def test_out_of_service_node_raises_no_offline_alert_and_resolves_the_old_one(admin, plant, cfg):
    feed_now(1, MAC_A, seconds=10)
    later = real_now() + timedelta(seconds=cfg.node_offline_s + 40)
    with database.db_session() as c:
        c.execute("UPDATE gateways SET last_heartbeat_at=?", (fmt_ts(later),))
    run_alerts(cfg, later)
    assert any(k.startswith("NODE_OFFLINE:") for k in active(admin))
    nid = admin("get", "/api/nodes").get_json()["nodes"][0]["id"]
    assert admin("put", f"/api/nodes/{nid}/service", json={"outOfService": "yes"}).status_code == 400
    assert admin("put", f"/api/nodes/{nid}/service", json={"outOfService": True}).status_code == 200
    run_alerts(cfg, later)
    assert not any(k.startswith(("NODE_OFFLINE:", "MISSING_PACKETS:")) for k in active(admin))
    n = next(x for x in admin("get", "/api/nodes").get_json()["nodes"] if x["id"] == nid)
    assert n["state"] == "out_of_service" and n["outOfService"] is True
    assert admin("put", f"/api/nodes/{nid}/service", json={"outOfService": False}).status_code == 200      # back in service: the alert is raised again
    run_alerts(cfg, later)
    assert any(k.startswith("NODE_OFFLINE:") for k in active(admin))


def _beat(t):
    from datetime import datetime, timezone
    from tests.v2helpers import heartbeat
    heartbeat(now=datetime.fromtimestamp(t, tz=timezone.utc), epoch=int(t))


def test_gaps_while_the_gateway_was_offline_are_not_radio_loss(admin, plant):
    t = int(time.time())
    for k in range(-180, 1, 10):                                         # a heartbeat every 10 s, except t-130 .. t-50: the gateway was away for 80 s (> 45 s)
        if not (-130 < k < -50):
            _beat(t + k)
    feed_now(1, MAC_A, seconds=180, skip=[(t - 120, t - 60)])            # 30 samples never arrived, all inside that outage
    live = next(x for x in admin("get", "/api/nodes").get_json()["nodes"] if x["number"] == 1)["live"]
    assert live["gatewayOutagePackets24h"] >= 25 and live["missedPackets24h"] == 0 and live["lossPct24h"] == 0.0
    assert live["gatewayOutageMin24h"] >= 1


def test_gaps_with_a_healthy_gateway_stay_radio_loss(admin, plant):
    t = int(time.time())
    for k in range(0, 61, 10):
        _beat(t - 60 + k)
    feed_now(1, MAC_A, seconds=60, skip=[(t - 30, t - 20)])
    live = next(x for x in admin("get", "/api/nodes").get_json()["nodes"] if x["number"] == 1)["live"]
    assert live["missedPackets24h"] >= 4 and live["lossPct24h"] > 5 and live["gatewayOutagePackets24h"] == 0
