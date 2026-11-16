"""Gateway heartbeat/offline detection, sensor-vs-gateway offline, MQTT message handler, alerts."""
import json
from datetime import timedelta

import database
import ingest
import mqtt_service
import services
from database import db_session
from tests.conftest import T0, flow_msg, gw_heartbeat, rain_msg

PFX = "waterleak/project001"


def node_stat(node, age, **kw):
    d = {"node": node, "bound": True, "ageS": age, "lastSeenEpoch": 0, "boot": 1, "received": 100, "duplicates": 3, "recovered": 2,
         "lost": 1, "missing": 0, "outOfOrder": 2, "nacksSent": 4, "sessions": 1, "invalid": 0, "cumAck": 100,
         "nodeUptime": 500, "nodeStatus": 0, "nodeRetransmits": 5, "nodeDropped": 0, "nodeRediscoveries": 1, "rssi": -55}
    d.update(kw)
    return d


def test_gateway_online_by_heartbeat_then_offline_after_three_missed(app, cfg):
    with db_session() as c:
        ingest.ingest_gateway_heartbeat(c, gw_heartbeat(), T0)
    c = database.get_db()
    assert services.gateway_status(c, cfg, T0 + timedelta(seconds=14))["online"] is True
    assert services.gateway_status(c, cfg, T0 + timedelta(seconds=44))["online"] is True     # < 3 x 15 s
    gw = services.gateway_status(c, cfg, T0 + timedelta(seconds=46))
    c.close()
    assert gw["online"] is False and gw["heartbeatAgeSeconds"] == 46
    assert cfg.gateway_offline_s == 3 * cfg.gateway_heartbeat_s                              # backend agrees with firmware interval


def test_one_shot_online_status_message_no_longer_marks_gateway_online_forever(app, cfg):
    with db_session() as c:
        ingest.ingest_gateway_status(c, "online", T0)
    c = database.get_db()
    gw = services.gateway_status(c, cfg, T0 + timedelta(seconds=5))
    c.close()
    assert gw["online"] is False            # only heartbeats prove liveness


def test_last_will_offline_marks_gateway_offline_immediately(app, cfg):
    with db_session() as c:
        ingest.ingest_gateway_heartbeat(c, gw_heartbeat(), T0)
        ingest.ingest_gateway_status(c, "offline", T0 + timedelta(seconds=2))
    c = database.get_db()
    gw = services.gateway_status(c, cfg, T0 + timedelta(seconds=3))
    c.close()
    assert gw["online"] is False and gw["lastWillOffline"] is True


def test_sensor_offline_vs_gateway_offline_are_distinguished(app, cfg):
    t = T0
    with db_session() as c:
        ingest.ingest_gateway_heartbeat(c, gw_heartbeat(nodes=[node_stat(1, 1), node_stat(2, 1), node_stat(3, 1)]), t)
        ingest.ingest_flow(c, flow_msg(node=1, seq=1), t)
        ingest.ingest_flow(c, flow_msg(node=2, seq=1), t)
    # 60 s later: gateway still beating, node 2 silent (gateway says it last heard it 55 s ago)
    t2 = T0 + timedelta(seconds=60)
    with db_session() as c:
        ingest.ingest_gateway_heartbeat(c, gw_heartbeat(nodes=[node_stat(1, 1), node_stat(2, 55), node_stat(3, 1)]), t2)
        ingest.ingest_flow(c, flow_msg(node=1, seq=30, cum=13500), t2)
    c = database.get_db()
    gw, devs = services.device_status(c, cfg, t2)
    by = {d["device"]: d for d in devs}
    assert gw["online"] and by["node1"]["state"] == "online"
    assert by["node2"]["state"] == "offline"             # SENSOR offline
    # 2 minutes after the last heartbeat the gateway itself is gone: nodes are 'unknown', not blamed
    t3 = t2 + timedelta(seconds=120)
    gw, devs = services.device_status(c, cfg, t3)
    c.close()
    assert not gw["online"] and all(d["state"] == "unknown" for d in devs)


def test_node_comm_stats_and_alerts(app, cfg):
    t = T0
    with db_session() as c:
        ingest.ingest_gateway_heartbeat(c, gw_heartbeat(nodes=[node_stat(1, 1, received=100, lost=0, recovered=0)]), t)
        ingest.ingest_gateway_heartbeat(c, gw_heartbeat(nodes=[node_stat(1, 1, received=160, lost=10, recovered=8)]), t + timedelta(minutes=5))
        services.evaluate_alerts(c, cfg, t + timedelta(minutes=5, seconds=1))
    c = database.get_db()
    keys = {a["key"] for a in services.list_alerts(c, active_only=True)}
    c.close()
    assert "COMM_DEGRADED" in keys


def test_alerts_tank_full_unsafe_valve_and_clear(app, cfg):
    t = T0
    with db_session() as c:
        ingest.ingest_gateway_heartbeat(c, gw_heartbeat(), t)
        ingest.ingest_rain(c, rain_msg(seq=1, rain=True, level=97.0, full=True, clear=False, faults=0x11, valveState=6), t)
        services.evaluate_alerts(c, cfg, t)
    c = database.get_db()
    keys = {a["key"] for a in services.list_alerts(c, active_only=True)}
    c.close()
    assert {"TANK_FULL", "UNSAFE_TURBIDITY", "VALVE_FAULT"} <= keys
    with db_session() as c:
        ingest.ingest_gateway_heartbeat(c, gw_heartbeat(), t + timedelta(seconds=10))
        ingest.ingest_rain(c, rain_msg(seq=2, rain=True, level=60.0, full=False, clear=True), t + timedelta(seconds=10))
        services.evaluate_alerts(c, cfg, t + timedelta(seconds=10))
    c = database.get_db()
    keys = {a["key"] for a in services.list_alerts(c, active_only=True)}
    hist = services.list_alerts(c)
    c.close()
    assert not ({"TANK_FULL", "UNSAFE_TURBIDITY", "VALVE_FAULT"} & keys)
    assert any(a["key"] == "TANK_FULL" and not a["active"] and a["clearedAt"] for a in hist)


def test_alert_not_duplicated_on_repeated_evaluation(app, cfg):
    with db_session() as c:
        ingest.ingest_gateway_heartbeat(c, gw_heartbeat(), T0)
        for i in range(5):
            services.evaluate_alerts(c, cfg, T0 + timedelta(seconds=100 + i))     # gateway stale -> GATEWAY_OFFLINE every time
    c = database.get_db()
    n = c.execute("SELECT COUNT(*) FROM alerts WHERE alert_key='GATEWAY_OFFLINE'").fetchone()[0]
    c.close()
    assert n == 1


def test_invalid_rain_level_is_not_stored_as_zero(app):
    with db_session() as c:
        ingest.ingest_rain(c, rain_msg(seq=1, level_valid=False), T0)
    c = database.get_db()
    r = c.execute("SELECT level_pct, level_valid FROM rain_samples").fetchone()
    c.close()
    assert r["level_valid"] == 0 and r["level_pct"] is None


# ---------------------------------------------------------------- MQTT message handler
def msg(cfg, topic, payload):
    body = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
    body = body.encode() if isinstance(body, str) else body
    return mqtt_service.handle_message(cfg, f"{PFX}/{topic}", body, T0)


def test_handler_routes_and_dedups(app, cfg):
    assert msg(cfg, "node1", flow_msg(node=1, seq=1)) == "stored"
    assert msg(cfg, "node1", flow_msg(node=1, seq=1)) == "duplicate"          # broker redelivery
    assert msg(cfg, "node2", flow_msg(node=2, seq=1)) == "stored"
    assert msg(cfg, "rainwater", rain_msg(seq=1)) == "stored"
    assert msg(cfg, "gateway/heartbeat", gw_heartbeat()) == "stored"
    assert msg(cfg, "gateway/status", "online") == "stored"


def test_handler_rejects_malformed_and_wrong_schema(app, cfg):
    assert msg(cfg, "node1", "{not json") == "rejected"
    assert msg(cfg, "node1", flow_msg(node=1, schema=1)) == "rejected"
    legacy = {"node": 1, "flowRate": 1.2, "totalLiters": 5.0, "uptime": 10}      # old firmware, no schema/boot/seq
    assert msg(cfg, "node1", legacy) == "rejected"
    assert msg(cfg, "node1", flow_msg(node=2, seq=1)) == "rejected"                # topic/payload mismatch
    assert msg(cfg, "rainwater", rain_msg(seq=2, turb=99999)) == "rejected"
    assert msg(cfg, "gateway/status", "banana") == "rejected"
    assert msg(cfg, "node1", b"x" * 20000) == "rejected"
    assert msg(cfg, "unknown/topic", flow_msg()) == "ignored"
    c = database.get_db()
    assert c.execute("SELECT COUNT(*) FROM flow_samples").fetchone()[0] == 0
    c.close()


def test_single_wildcard_subscription_only():
    import inspect
    src = inspect.getsource(mqtt_service.start_mqtt)
    assert src.count("subscribe(") == 1 and '"/#"' in src


def test_valve_fault_alert_explains_the_cause(app, cfg):
    """'tank level sensor out of range' (nothing in front) vs a real sensor error; always says the valve is held closed."""
    def msg_of(**kw):
        with db_session() as c:
            ingest.ingest_gateway_heartbeat(c, gw_heartbeat(), T0)
            ingest.ingest_rain(c, rain_msg(seq=kw.pop("seq"), level_valid=False, valveState=6, dryRun=True, **kw), T0)
            services.evaluate_alerts(c, cfg, T0)
        c = database.get_db()
        a = next(x for x in services.list_alerts(c, active_only=True) if x["key"] == "VALVE_FAULT")
        c.close()
        return a["message"]
    m = msg_of(seq=1, faults=0x0001, outOfRange=True)
    assert "tank level sensor out of range" in m and "no tank in front" in m and "held closed (dry-run)" in m
    assert "ultrasonic" in msg_of(seq=2, faults=0x0001, outOfRange=False)                     # a real echo failure keeps its own wording
    c = database.get_db()
    row = c.execute("SELECT out_of_range FROM rain_samples ORDER BY id").fetchall()
    c.close()
    assert [r["out_of_range"] for r in row] == [1, 0]


def test_alert_texts_use_human_durations():
    assert [services.human_s(x) for x in (45, 180, 6583, 7200, 90000, 86400)] == ["45 s", "3 min", "1 h 49 min", "2 h", "1 d 1 h", "1 d"]


def test_rainwater_live_shows_the_newest_boot_even_with_a_stale_device_row(app):
    """v2 ingest keeps no 'rainwater' devices row; an old row from a previous boot must not hide the live readings."""
    from tests.conftest import login
    with db_session() as c:
        ingest.ingest_rain(c, rain_msg(boot=111, seq=146), T0 - timedelta(days=1))                       # the old node boot
        for i in range(1, 4):
            ingest.ingest_rain(c, rain_msg(boot=222, seq=i, level=50.0 + i), T0 + timedelta(seconds=2 * i), v2={"gid": 1})   # v2: devices untouched
    cl = app.test_client(); login(cl)
    live = cl.get("/api/rainwater/live").get_json()["rainwater"]
    assert live["bootId"] == 222 and live["seq"] == 3 and live["waterLevel"] == 53.0
