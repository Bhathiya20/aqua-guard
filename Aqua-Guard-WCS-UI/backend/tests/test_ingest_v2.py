"""Protocol v2 ingestion: registry mirror, numbered-node telemetry, no double storing, boot sessions, measurement time, identity after Replace."""
import json

import pytest

import database
import gateway_v2
import mqtt_service
from database import db_session
from ingest import fmt_ts
from protocol_v2 import cmd_tag
from tests.conftest import flow_msg
from tests.v2helpers import PREFIX, T0, NodeSim, deliver, dt, feed, fmt_mac, heartbeat, info, registry

MAC_A, MAC_B = "447b30854b88", "aabbcc000002"


def rows(sql, *a):
    c = database.get_db()
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


# --------------------------------------------------------------------------- registry mirror
def test_registry_mirror_creates_numbered_nodes(app):
    registry([("flow", 1, MAC_A, "Kitchen", "paired"), ("flow", 2, "112233445566", "Garden", "pending"), ("rain", 1, MAC_B, "Roof", "paired")])
    n = rows("SELECT type, number, mac, name, state FROM nodes ORDER BY type, number")
    assert [(r["type"], r["number"], r["mac"], r["name"], r["state"]) for r in n] == [
        ("flow", 1, MAC_A, "Kitchen", "paired"), ("flow", 2, "112233445566", "Garden", "pending"), ("rain", 1, MAC_B, "Roof", "paired")]
    assert rows("SELECT COUNT(*) c FROM gateways")[0]["c"] == 1


def test_node_dropped_from_the_registry_is_marked_removed_and_history_kept(app):
    registry([("flow", 1, MAC_A, "Kitchen", "paired"), ("flow", 2, "112233445566", "Garden", "paired")])
    deliver(NodeSim(1, MAC_A).msgs(T0, T0 + 2, 1.0)[0][1], T0)
    registry([("flow", 2, "112233445566", "Garden", "paired")], rev=2)
    assert rows("SELECT state FROM nodes WHERE number=1")[0]["state"] == "removed"
    assert rows("SELECT COUNT(*) c FROM flow_samples WHERE node_ref IS NOT NULL")[0]["c"] == 1          # readings are not deleted


def test_replace_keeps_number_and_records_both_devices(app):
    registry([("flow", 1, MAC_A, "Kitchen", "paired")])
    ref = rows("SELECT id FROM nodes")[0]["id"]
    registry([("flow", 1, MAC_B, "Kitchen", "paired")], rev=2)
    n = rows("SELECT id, mac FROM nodes")
    assert len(n) == 1 and n[0]["id"] == ref and n[0]["mac"] == MAC_B
    assert {r["mac"] for r in rows("SELECT mac FROM node_hw_history WHERE node_ref=?", ref)} == {MAC_A, MAC_B}


# --------------------------------------------------------------------------- telemetry, identity
def test_telemetry_row_references_the_node_and_records_the_hardware_mac(app):
    registry([("flow", 1, MAC_A, "Kitchen", "paired")])
    a = NodeSim(1, MAC_A)
    feed(a, T0, T0 + 20, 3.0)
    r = rows("SELECT node_id, node_ref, hw_mac, measured_src, session_id FROM flow_samples")
    assert len(r) == 10 and {x["hw_mac"] for x in r} == {MAC_A} and {x["node_ref"] for x in r} == {rows("SELECT id FROM nodes")[0]["id"]}
    assert {x["measured_src"] for x in r} == {"gw_epoch"} and {x["node_id"] for x in r} == {1}
    s = rows("SELECT hw_mac, boot_id, samples FROM node_sessions")
    assert len(s) == 1 and s[0]["hw_mac"] == MAC_A and s[0]["samples"] == 10


def test_history_stays_continuous_after_replace_but_each_reading_keeps_its_device(app):
    registry([("flow", 1, MAC_A, "Kitchen", "paired")])
    old = NodeSim(1, MAC_A, boot=1000)
    feed(old, T0, T0 + 60, 3.0)
    registry([("flow", 1, MAC_B, "Kitchen", "paired")], rev=2)                                    # hardware swapped, same number
    new = NodeSim(1, MAC_B, boot=7777, t_boot=T0 + 100)
    feed(new, T0 + 120, T0 + 180, 3.0)
    r = rows("SELECT node_ref, hw_mac, boot_id FROM flow_samples ORDER BY measured_at")
    assert len({x["node_ref"] for x in r}) == 1                                                      # one node row: continuous history
    assert [x["hw_mac"] for x in r if x["boot_id"] == 1000] == [MAC_A] * 30 and [x["hw_mac"] for x in r if x["boot_id"] == 7777] == [MAC_B] * 30
    assert {s["hw_mac"] for s in rows("SELECT hw_mac FROM node_sessions")} == {MAC_A, MAC_B}


def test_unregistered_but_forwarded_telemetry_creates_the_node_row(app):
    deliver(NodeSim(3, MAC_B).msgs(T0, T0 + 2, 1.0)[0][1], T0)                                       # the gateway only forwards verified frames
    n = rows("SELECT number, mac, state FROM nodes")
    assert [(x["number"], x["mac"], x["state"]) for x in n] == [(3, MAC_B, "paired")]


def test_payload_and_topic_must_agree(app):
    m = NodeSim(1, MAC_A).msgs(T0, T0 + 2, 1.0)[0][1]
    with db_session() as conn, pytest.raises(Exception):
        gateway_v2.ingest_telemetry(conn, m, dt(T0), gid="gw-test", topic_type="flow", topic_number=2)
    with db_session() as conn, pytest.raises(Exception):
        gateway_v2.ingest_telemetry(conn, dict(m, type="rain"), dt(T0), gid="gw-test", topic_type="flow", topic_number=1)


# --------------------------------------------------------------------------- one reading, one row
def test_same_reading_on_new_and_legacy_topic_is_stored_once(app, cfg):
    registry([("flow", 1, MAC_A, "Kitchen", "paired")])
    m = NodeSim(1, MAC_A).msgs(T0, T0 + 2, 2.0)[0][1]
    m["rxEpoch"] = T0
    body = json.dumps(m).encode()
    assert mqtt_service.handle_message(cfg, f"{PREFIX}/gw-test/node/1", body, dt(T0)) == "stored"
    assert mqtt_service.handle_message(cfg, f"{PREFIX}/node1", body, dt(T0)) == "duplicate"
    assert mqtt_service.handle_message(cfg, f"{PREFIX}/node1", body, dt(T0)) == "duplicate"
    assert rows("SELECT COUNT(*) c FROM flow_samples")[0]["c"] == 1
    # legacy first, new topic second: still one row
    m2 = NodeSim(1, MAC_A, boot=2000).msgs(T0 + 10, T0 + 12, 2.0)[0][1]
    m2["rxEpoch"] = T0 + 10
    b2 = json.dumps(m2).encode()
    assert mqtt_service.handle_message(cfg, f"{PREFIX}/node1", b2, dt(T0 + 10)) == "stored"
    assert mqtt_service.handle_message(cfg, f"{PREFIX}/gw-test/node/1", b2, dt(T0 + 10)) == "duplicate"
    assert rows("SELECT COUNT(*) c FROM flow_samples")[0]["c"] == 2


def test_v1_payloads_on_the_legacy_topic_still_use_the_v1_path(app, cfg):
    res = mqtt_service.handle_message(cfg, f"{PREFIX}/node1", json.dumps(flow_msg(node=1, seq=1)).encode(), dt(T0))
    assert res == "stored"
    r = rows("SELECT node_ref, measured_src FROM flow_samples")[0]
    assert r["node_ref"] is None and r["measured_src"] == "legacy"


def test_rain_node_numbers_are_accepted(app, cfg):
    m = {"schema": 3, "node": 1, "type": "rain", "mac": fmt_mac(MAC_B), "boot": 5000, "seq": 1, "recovered": False, "backlog": False, "rain": False, "waterLevel": 40.0,
         "levelValid": True, "distance": 12.5, "turbidity": 3500, "turbidityValid": True, "waterClear": True, "tankFull": False, "valveOpen": False, "valveState": 0,
         "valveReason": 0, "buzzer": False, "firstFlush": False, "dryRun": True, "faults": 0, "rainEvents": 0, "uptime": 2, "rxEpoch": T0, "channel": 9, "rssi": -60}
    assert mqtt_service.handle_message(cfg, f"{PREFIX}/gw-test/rain/1", json.dumps(m).encode(), dt(T0)) == "stored"
    assert rows("SELECT COUNT(*) c FROM rain_samples")[0]["c"] == 1 and rows("SELECT type, number FROM nodes")[0]["type"] == "rain"


# --------------------------------------------------------------------------- volume per boot session
def test_volume_per_boot_session_and_counter_reset(app):
    registry([("flow", 1, MAC_A, "Kitchen", "paired")])
    a = NodeSim(1, MAC_A, boot=1000)
    feed(a, T0, T0 + 120, 3.0)                                                                        # 60 samples, 3 L/min -> 6 L
    b = NodeSim(1, MAC_A, boot=2000, t_boot=T0 + 200)                                                 # reboot: the counter restarts at 0
    feed(b, T0 + 202, T0 + 322, 1.5)                                                                  # 60 samples, 1.5 L/min -> 3 L
    per_boot = {r["boot_id"]: r["l"] for r in rows("SELECT boot_id, SUM(delta_pulses*10.0/pulses_per_liter_x10) l FROM flow_samples GROUP BY boot_id")}
    assert per_boot[1000] == pytest.approx(6.0, abs=0.05) and per_boot[2000] == pytest.approx(3.0, abs=0.05)
    assert rows("SELECT MIN(delta_pulses) m FROM flow_samples")[0]["m"] >= 0
    assert rows("SELECT COUNT(*) c FROM flow_samples WHERE anomaly=1")[0]["c"] == 0
    assert rows("SELECT COUNT(*) c FROM node_sessions")[0]["c"] == 2


def test_lost_packets_do_not_lose_volume(app):
    registry([("flow", 1, MAC_A, "Kitchen", "paired")])
    feed(NodeSim(1, MAC_A), T0, T0 + 120, 3.0, skip=[(T0 + 40, T0 + 60)])                            # 10 samples never arrive
    total = rows("SELECT SUM(delta_pulses*10.0/pulses_per_liter_x10) l FROM flow_samples")[0]["l"]
    assert total == pytest.approx(6.0, abs=0.05)                                                      # the counter difference covers them


# --------------------------------------------------------------------------- measurement time
def test_measured_at_comes_from_the_boot_anchor_not_the_arrival_time(app):
    registry([("flow", 1, MAC_A, "Kitchen", "paired")])
    sim = NodeSim(1, MAC_A)
    held = feed(sim, T0, T0 + 120, 2.0, skip=[(T0 + 40, T0 + 70)], late=T0 + 200)                     # 15 samples arrive 130-160 s late, flagged recovered
    assert len(held) == 15
    from ingest import parse_ts
    r = rows("SELECT seq, measured_at, recovered, received_at FROM flow_samples ORDER BY seq")
    assert len(r) == 60
    for x in r:                                                    # EVERY row (live or recovered) sits at the second the water flowed: t = (boot) + uptime
        assert x["measured_at"] == fmt_ts(dt(T0 - 10 + (x["seq"] - 1) * 2)), x["seq"]
    late = [x for x in r if x["recovered"]]
    assert len(late) == 15
    assert all(T0 + 40 <= int(parse_ts(x["measured_at"]).timestamp()) < T0 + 70 and x["received_at"] == fmt_ts(dt(T0 + 200)) for x in late)   # arrival is still recorded


def test_only_late_samples_at_first_still_end_up_in_the_right_second(app):
    """A session whose FIRST delivered sample is a late retransmission: the anchor is refined by the next live sample and rows are re-timed."""
    registry([("flow", 1, MAC_A, "Kitchen", "paired")])
    sim = NodeSim(1, MAC_A)
    msgs = sim.msgs(T0, T0 + 20, 2.0)
    deliver(msgs[0][1], T0 + 90, recovered=True)                                                      # arrives 90 s late as the very first sample
    first = rows("SELECT measured_at FROM flow_samples")[0]["measured_at"]
    assert first == fmt_ts(dt(T0 + 90))                                                               # provisional: the delivery delay is not known yet
    deliver(msgs[5][1], T0 + 10)                                                                      # a live sample reveals the true anchor
    from ingest import parse_ts
    t_first = int(parse_ts(rows("SELECT measured_at FROM flow_samples ORDER BY seq LIMIT 1")[0]["measured_at"]).timestamp())
    assert t_first == T0 + 0                                                                          # re-timed to when it was really measured


def test_unsynced_gateway_clock_falls_back_to_backend_time_with_lower_confidence(app):
    registry([("flow", 1, MAC_A, "Kitchen", "paired")])
    sim = NodeSim(1, MAC_A)
    feed(sim, T0, T0 + 20, 2.0, rx_epoch=False)
    assert {r["measured_src"] for r in rows("SELECT measured_src FROM flow_samples")} == {"recv"}
    assert rows("SELECT anchor_src FROM node_sessions")[0]["anchor_src"] == "recv"
    feed(sim, T0 + 20, T0 + 30, 2.0)                                                                  # the gateway got NTP time: the session switches to gw_epoch and old rows are re-timed
    assert rows("SELECT anchor_src FROM node_sessions")[0]["anchor_src"] == "gw_epoch"
    assert {r["measured_src"] for r in rows("SELECT measured_src FROM flow_samples")} == {"gw_epoch"}


def test_battery_rssi_and_last_seen_are_mirrored_on_the_node(app):
    registry([("flow", 1, MAC_A, "Kitchen", "paired")])
    feed(NodeSim(1, MAC_A), T0, T0 + 10, 1.0)
    n = rows("SELECT battery_mv, rssi, last_seen FROM nodes")[0]
    assert n["battery_mv"] == 7400 and n["rssi"] == -55 and n["last_seen"] == fmt_ts(dt(T0 + 8))


# --------------------------------------------------------------------------- gateway status
def test_gateway_online_offline_and_time_status(app, cfg):
    info(now=dt(T0))
    heartbeat(now=dt(T0))
    with db_session() as conn:
        g = gateway_v2.sole_gateway(conn)
        assert gateway_v2.gateway_state(cfg, g, dt(T0 + 20))["state"] == "online"
        s = gateway_v2.gateway_state(cfg, g, dt(T0 + 20))
        assert s["timeStatus"] == "synced" and s["heartbeatAgeS"] == 20
        assert gateway_v2.gateway_state(cfg, g, dt(T0 + 100))["state"] == "offline"                    # no heartbeat for > 45 s
    with db_session() as conn:
        gateway_v2.handle_status(conn, "offline", dt(T0 + 5))                                           # last will: explicit offline wins over a fresh heartbeat
        g = gateway_v2.sole_gateway(conn)
        assert gateway_v2.gateway_state(cfg, g, dt(T0 + 6))["state"] == "offline"
        gateway_v2.handle_status(conn, "online", dt(T0 + 7))
        g = gateway_v2.sole_gateway(conn)
        assert gateway_v2.gateway_state(cfg, g, dt(T0 + 8))["state"] == "online"


def test_gateway_time_not_synced_is_reported_from_the_heartbeat(app, cfg):
    heartbeat(now=dt(T0), epoch=0)
    with db_session() as conn:
        s = gateway_v2.gateway_state(cfg, gateway_v2.sole_gateway(conn), dt(T0 + 1))
    assert s["state"] == "online" and s["timeStatus"] == "not_synced" and s["timeSynced"] is False


def test_heartbeat_stores_rx_counters_including_queue_drops(app):
    heartbeat()
    g = rows("SELECT rx_frames, rx_rejected, rx_bad_tag, rx_bad_value, rx_queue_dropped, queue_depth, queue_overflow FROM gateways")[0]
    assert (g["rx_frames"], g["rx_rejected"], g["rx_bad_tag"], g["rx_bad_value"], g["rx_queue_dropped"], g["queue_depth"], g["queue_overflow"]) == (100, 3, 2, 1, 5, 2, 1)
    assert rows("SELECT COUNT(*) c FROM gateway_heartbeats")[0]["c"] == 1                              # the v1 health history keeps working


def test_info_topic_updates_gateway_facts(app):
    info(ip="192.168.1.77", tls=True, tlsVerified=True, remoteAdd=True)
    g = rows("SELECT ip, tls, tls_verified, remote_add, fw, time_synced FROM gateways")[0]
    assert (g["ip"], g["tls"], g["tls_verified"], g["remote_add"], g["fw"], g["time_synced"]) == ("192.168.1.77", 1, 1, 1, "2.0", 1)


# --------------------------------------------------------------------------- pairing events + command results
def event(key, kind="D", mac="447b30854b88", number=0, forged=False):
    body = f"AQE1 {kind} {number} {mac} 1 512 9 -55 0001e240 1 - - {T0}"
    tag = cmd_tag(key, body)
    return body + " " + ("0" * 16 if forged else tag)


def test_pairing_events_are_verified_with_the_command_key(app, cfg):
    cfg.cmd_key_hex = "11" * 16
    key = bytes([0x11]) * 16
    assert mqtt_service.handle_message(cfg, f"{PREFIX}/pairing/event", event(key).encode(), dt(T0)) == "stored"
    assert mqtt_service.handle_message(cfg, f"{PREFIX}/pairing/event", event(key, "O", number=2).encode(), dt(T0)) == "stored"
    assert mqtt_service.handle_message(cfg, f"{PREFIX}/pairing/event", event(key, forged=True).encode(), dt(T0)) == "rejected"
    assert [r["kind"] for r in rows("SELECT kind FROM pairing_events_v2 ORDER BY id")] == ["nearby", "joined"]


def test_command_results_resolve_the_oldest_pending_command(app):
    registry([("flow", 1, MAC_A, "Kitchen", "paired")])
    with db_session() as conn:
        gid = gateway_v2.sole_gateway(conn)["id"]
        for op in ("A", "N"):
            conn.execute("INSERT INTO gateway_commands (gateway_ref, op, created_at, status) VALUES (?,?,?, 'sent')", (gid, op, fmt_ts(dt(T0))))
        gateway_v2.handle_cmd_result(conn, "gw-test", {"result": "duplicate_mac", "rc": -1, "number": 0}, dt(T0 + 1))
        gateway_v2.handle_cmd_result(conn, "gw-test", {"result": "ok", "rc": 3, "number": 4}, dt(T0 + 2))
    r = rows("SELECT op, status, result, number FROM gateway_commands ORDER BY id")
    assert [(x["op"], x["status"], x["result"]) for x in r] == [("A", "error", "duplicate_mac"), ("N", "ok", "ok")] and r[1]["number"] == 4


def test_live_mqtt_path_without_an_explicit_clock(app, cfg):
    """The paho thread calls handle_message() with no `now`; this crashed on the first real broker run (found in the live check)."""
    import json
    import mqtt_service
    pre = cfg.mqtt_topic_prefix.rstrip("/")
    reg = {"schema": 3, "gateway": "gw-live", "rev": 1, "epoch": 0, "nodes": []}
    assert mqtt_service.handle_message(cfg, f"{pre}/gw-live/nodes/registry", json.dumps(reg).encode()) == "stored"
    assert mqtt_service.handle_message(cfg, f"{pre}/gw-live/gateway/info", json.dumps({"schema": 3, "gateway": "gw-live", "timeSynced": True, "epoch": 1_767_268_800}).encode()) == "stored"
    assert mqtt_service.handle_message(cfg, f"{pre}/gateway/heartbeat", json.dumps({"schema": 3, "gateway": "gw-live", "uptime": 5, "epoch": 1_767_268_800}).encode()) == "stored"


def test_v2_telemetry_does_not_bring_retired_v1_devices_back_online(app, cfg):
    """The gateway may still publish the legacy node<N>/rainwater topics: they must not refresh the frozen v1 device rows."""
    import database
    with database.db_session() as c:
        before = c.execute("SELECT COUNT(*) AS n FROM devices WHERE device_key IN ('node1','node2','rainwater')").fetchone()["n"]
    from tests.v2helpers import NodeSim, T0, deliver, feed, registry
    registry([("flow", 1, "447b30854b88", "Main", "paired")])
    feed(NodeSim(1, "447b30854b88", step=5), T0, T0 + 60, 3.0)
    with database.db_session() as c:
        after = c.execute("SELECT COUNT(*) AS n FROM devices WHERE device_key IN ('node1','node2','rainwater')").fetchone()["n"]
    assert after == before
