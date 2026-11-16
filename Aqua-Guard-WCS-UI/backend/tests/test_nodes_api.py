"""Node REST API: signed AQC2 commands, preflight refusals (key, gateway, time, TLS), results, permissions, secrecy of the pairing code."""
import time
from datetime import datetime, timezone

import pytest
from werkzeug.security import generate_password_hash

import database
import gateway_v2
import mqtt_service
from protocol_v2 import parse_aqc2, parse_cmd_key
from tests.conftest import USER_EMAIL, USER_PW, login
from tests.v2helpers import heartbeat, info, registry

KEY_HEX = "11" * 16
MAC_A, MAC_B, MAC_NEW = "447b30854b88", "aabbcc000002", "a1b2c3d4e5f6"
CODE = "RPZ1-6DGP-ZA5Q"


def real_now():
    return datetime.now(timezone.utc)


@pytest.fixture()
def sent(cfg):
    """Captured MQTT publications (topic, text); the publisher is restored afterwards."""
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
def online(app):
    """A gateway that is online (fresh heartbeat), time-synced, with two paired flow nodes."""
    n = real_now()
    registry([("flow", 1, MAC_A, "Kitchen", "paired"), ("flow", 2, MAC_B, "Garden", "paired")], now=n)
    info(now=n, remoteAdd=False)
    heartbeat(now=n, epoch=int(time.time()))


def set_gw(**cols):
    with database.db_session() as c:
        c.execute("UPDATE gateways SET " + ",".join(f"{k}=?" for k in cols), tuple(cols.values()))


def ref(number):
    with database.db_session() as c:
        return c.execute("SELECT id FROM nodes WHERE number=? AND type='flow'", (number,)).fetchone()["id"]


def line(sent, i=-1):
    return parse_aqc2(parse_cmd_key(KEY_HEX), sent[i][1])


# --------------------------------------------------------------------------- key guard
@pytest.mark.parametrize("bad", ["", "00" * 16, "zz" * 16, "11" * 15])
def test_commands_are_refused_without_a_valid_site_key(app, cfg, admin, online, sent, bad):
    cfg.cmd_key_hex = bad
    r = admin("patch", f"/api/nodes/{ref(1)}", json={"name": "X"})
    assert r.status_code == 503 and r.get_json()["code"] == "command_key_missing" and sent == []
    assert admin("get", "/api/gateways").get_json()["commandsEnabled"] is False


# --------------------------------------------------------------------------- allowed without TLS
def test_rename_is_signed_and_matches_the_protocol(admin, online, sent):
    r = admin("patch", f"/api/nodes/{ref(1)}", json={"name": "Kitchen sink"})
    assert r.status_code == 202 and r.get_json()["status"] == "sent"
    topic, text = sent[0]
    assert topic == "waterleak/project001/gw-test/gateway/cmd" or topic.endswith("/gw-test/gateway/cmd")
    p = line(sent)
    assert p["op"] == "N" and p["number"] == 1 and p["type"] == 1 and p["name"] == "Kitchen sink" and p["code"] is None
    assert abs(p["ts"] - time.time()) < 5


def test_calibrate_and_tx_reduce(admin, online, sent):
    admin("patch", f"/api/nodes/{ref(1)}", json={"pulsesPerLiter": 452.5, "txReduce": True})
    p = line(sent)
    assert p["op"] == "C" and p["ppl"] == 4525 and p["flags"] == 3 and p["number"] == 1           # legacy txReduce = 8.5 dBm = level code 1 + legacy bit
    assert admin("patch", f"/api/nodes/{ref(1)}", json={"pulsesPerLiter": 5}).status_code == 400
    assert admin("patch", f"/api/nodes/{ref(1)}", json={}).status_code == 400
    assert admin("patch", f"/api/nodes/{ref(1)}", json={"name": "  "}).status_code == 400
    assert len(sent) == 1


@pytest.mark.parametrize("dbm,flags", [(8.5, 0x03), (11, 0x04), (13, 0x06), (15, 0x08)])
def test_tx_power_levels_are_pushed_as_flags(admin, online, sent, dbm, flags):
    r = admin("patch", f"/api/nodes/{ref(1)}", json={"txPowerDbm": dbm})
    assert r.status_code == 202
    p = line(sent)
    assert p["op"] == "C" and p["flags"] == flags and p["number"] == 1 and p["ppl"] == 4500        # calibration untouched (default 450 p/L)


@pytest.mark.parametrize("bad", [0, 9, 19.5, 12, "x", None])
def test_tx_power_only_accepts_the_four_levels(admin, online, sent, bad):
    assert admin("patch", f"/api/nodes/{ref(1)}", json={"txPowerDbm": bad}).status_code == 400
    assert sent == []


def test_calibration_change_keeps_the_nodes_tx_level(admin, online, sent):
    from database import db_session
    rid = ref(1)
    with db_session() as c:
        c.execute("UPDATE nodes SET tx_dbm_x10=130 WHERE id=?", (rid,))
    admin("patch", f"/api/nodes/{ref(1)}", json={"pulsesPerLiter": 460})
    assert line(sent)["flags"] == 0x06


def test_node_json_reports_the_tx_power_from_the_registry(admin, online):
    from database import db_session
    n = next(x for x in admin("get", "/api/nodes").get_json()["nodes"] if x["id"] == ref(1))
    assert n["txPowerDbm"] == 8.5                                  # nothing reported yet = default
    rid = ref(1)
    with db_session() as c:
        c.execute("UPDATE nodes SET tx_dbm_x10=150 WHERE id=?", (rid,))
    n = next(x for x in admin("get", "/api/nodes").get_json()["nodes"] if x["id"] == ref(1))
    assert n["txPowerDbm"] == 15.0


def test_remove_is_signed(admin, online, sent):
    admin("delete", f"/api/nodes/{ref(2)}")
    p = line(sent)
    assert p["op"] == "R" and p["number"] == 2 and p["mac"] is None
    assert admin("delete", "/api/nodes/9999").status_code == 404


def test_timestamps_never_go_backwards(admin, online, sent):
    for _ in range(3):
        admin("patch", f"/api/nodes/{ref(1)}", json={"name": "A"})
    ts = [line(sent, i)["ts"] for i in range(3)]
    assert ts == sorted(ts)
    assert len({line(sent, i)["nonce"] for i in range(3)}) == 3


# --------------------------------------------------------------------------- add / replace need TLS
def test_add_and_replace_are_refused_while_mqtt_has_no_tls(cfg, admin, online, sent):
    assert not cfg.mqtt_tls
    r = admin("post", "/api/nodes", json={"type": "flow", "mac": MAC_NEW, "code": CODE})
    j = r.get_json()
    assert r.status_code == 409 and j["code"] == "tls_required" and "no verified TLS" in j["error"] and "gateway's own page" in j["error"]
    r = admin("post", f"/api/nodes/{ref(1)}/replace", json={"mac": MAC_NEW, "code": CODE})
    assert r.status_code == 409 and r.get_json()["code"] == "tls_required"
    assert sent == []                                                             # nothing left the backend
    with database.db_session() as c:
        assert c.execute("SELECT COUNT(*) n FROM gateway_commands").fetchone()["n"] == 0
    assert admin("get", "/api/gateways").get_json()["mqtt"]["remoteAddAllowed"] is False


def test_add_refused_when_the_gateway_itself_refuses_remote_add(cfg, admin, online, sent):
    cfg.mqtt_tls = True
    r = admin("post", "/api/nodes", json={"type": "flow", "mac": MAC_NEW, "code": CODE})
    assert r.status_code == 409 and r.get_json()["code"] == "gateway_refuses_remote_add" and sent == []


def test_add_and_replace_succeed_with_tls_and_never_leak_the_code(cfg, admin, online, sent):
    cfg.mqtt_tls = True
    set_gw(remote_add=1, tls=1, tls_verified=1)
    r = admin("post", "/api/nodes", json={"type": "flow", "mac": "A1:B2:C3:D4:E5:F6", "code": CODE, "name": "Shed"})
    assert r.status_code == 202
    p = line(sent)
    assert p["op"] == "A" and p["mac"] == MAC_NEW and p["name"] == "Shed" and p["code"] == "RPZ16DGPZA5Q"
    assert "RPZ1" not in sent[0][1] and "6DGP" not in sent[0][1].upper()          # encrypted on the wire
    admin("post", f"/api/nodes/{ref(1)}/replace", json={"mac": "0a0b0c0d0e0f", "code": CODE})
    p = line(sent)
    assert p["op"] == "P" and p["number"] == 1 and p["mac"] == "0a0b0c0d0e0f"
    with database.db_session() as c:                                                # the code is nowhere in the database
        dump = " ".join(str(v) for t in ("gateway_commands", "audit_log") for row in c.execute(f"SELECT * FROM {t}").fetchall() for v in tuple(row))
    assert "RPZ1" not in dump.upper() and "RPZ16DGPZA5Q" not in dump.upper() and "6DGP" not in dump.upper()
    assert "node_cmd_A" in dump and "node_cmd_P" in dump


def test_duplicate_mac_is_refused_before_sending(cfg, admin, online, sent):
    cfg.mqtt_tls = True
    set_gw(remote_add=1)
    r = admin("post", "/api/nodes", json={"type": "flow", "mac": MAC_A, "code": CODE})
    assert r.status_code == 409 and r.get_json()["code"] == "duplicate_mac" and sent == []
    r = admin("post", f"/api/nodes/{ref(2)}/replace", json={"mac": MAC_A, "code": CODE})
    assert r.status_code == 409 and r.get_json()["code"] == "duplicate_mac"


def test_bad_input_is_a_400(cfg, admin, online, sent):
    cfg.mqtt_tls = True
    set_gw(remote_add=1)
    assert admin("post", "/api/nodes", json={"type": "flow", "mac": "nonsense", "code": CODE}).status_code == 400
    assert admin("post", "/api/nodes", json={"type": "flow", "mac": MAC_NEW, "code": "TOO-SHORT"}).status_code == 400
    assert admin("post", "/api/nodes", json={"type": "pump", "mac": MAC_NEW, "code": CODE}).status_code == 400
    assert sent == []


# --------------------------------------------------------------------------- gateway state
def test_offline_gateway_refuses_with_a_clear_message(admin, online, sent):
    set_gw(last_heartbeat_at="2020-01-01 00:00:00")
    r = admin("patch", f"/api/nodes/{ref(1)}", json={"name": "X"})
    assert r.status_code == 409 and r.get_json()["code"] == "gateway_offline" and "not sent" in r.get_json()["error"] and sent == []
    g = admin("get", "/api/gateways").get_json()["gateways"][0]
    assert g["state"] == "offline"
    nodes = admin("get", "/api/nodes").get_json()["nodes"]
    assert {n["state"] for n in nodes} == {"unknown"}                              # a gateway outage does not make nodes look dead


def test_retained_offline_status_wins_over_a_fresh_heartbeat(admin, online, sent):
    set_gw(status_raw="offline")
    assert admin("delete", f"/api/nodes/{ref(1)}").get_json()["code"] == "gateway_offline"


def test_time_not_synced_is_reported_and_refused(admin, online, sent):
    heartbeat(now=real_now(), epoch=0)
    r = admin("patch", f"/api/nodes/{ref(1)}", json={"name": "X"})
    assert r.status_code == 409 and r.get_json()["code"] == "gateway_time_not_synced" and sent == []
    g = admin("get", "/api/gateways").get_json()["gateways"][0]
    assert g["state"] == "online" and g["timeSynced"] is False and g["timeStatus"] == "not_synced"


def test_no_gateway_at_all(app, cfg, admin, sent):
    r = admin("post", "/api/nodes", json={"type": "flow", "mac": MAC_NEW, "code": CODE})
    assert r.status_code == 409 and r.get_json()["code"] == "no_gateway"
    assert admin("get", "/api/nodes").get_json()["nodes"] == []


def test_publish_failure_is_reported_and_recorded(admin, online):
    old = mqtt_service._text_publisher
    mqtt_service.set_text_publisher(lambda t, x: False)
    try:
        r = admin("delete", f"/api/nodes/{ref(1)}")
    finally:
        mqtt_service.set_text_publisher(old)
    assert r.status_code == 503 and r.get_json()["code"] == "mqtt_down"
    with database.db_session() as c:
        row = c.execute("SELECT status, result FROM gateway_commands").fetchone()
    assert (row["status"], row["result"]) == ("not_sent", "mqtt_down")


# --------------------------------------------------------------------------- results
def result(d, now=None):
    with database.db_session() as c:
        return gateway_v2.handle_cmd_result(c, "gw-test", d, now or real_now())


def test_command_result_is_matched_and_reported(admin, online, sent):
    cid = admin("patch", f"/api/nodes/{ref(1)}", json={"name": "X"}).get_json()["commandId"]
    result({"schema": 3, "result": "ok", "rc": 0, "number": 1})
    j = admin("get", f"/api/commands/{cid}").get_json()
    assert j["status"] == "ok" and j["result"] == "ok"


def test_failure_result_is_a_409_with_the_gateway_reason(admin, online, sent):
    cid = admin("delete", f"/api/nodes/{ref(1)}").get_json()["commandId"]
    result({"schema": 3, "result": "replay", "rc": 3})
    j = admin("get", f"/api/commands/{cid}").get_json()
    assert j["status"] == "error" and j["result"] == "replay"


def test_wait_returns_the_result_when_it_arrives(admin, online, sent):
    import threading
    threading.Timer(0.4, lambda: result({"schema": 3, "result": "ok", "rc": 0})).start()
    t0 = time.time()
    r = admin("patch", f"/api/nodes/{ref(1)}?wait=5", json={"name": "Y"})
    assert r.status_code == 200 and r.get_json()["status"] == "ok" and time.time() - t0 < 4


def test_unanswered_commands_time_out(cfg, admin, online, sent):
    cfg.command_timeout_s = 0
    cid = admin("patch", f"/api/nodes/{ref(1)}", json={"name": "X"}).get_json()["commandId"]
    time.sleep(1.1)
    assert admin("get", f"/api/commands/{cid}").get_json()["status"] == "timeout"
    assert admin("get", "/api/commands/9999").status_code == 404


def test_too_many_pending_commands(admin, online, sent):
    for _ in range(8):
        admin("patch", f"/api/nodes/{ref(1)}", json={"name": "A"})
    r = admin("patch", f"/api/nodes/{ref(1)}", json={"name": "A"})
    assert r.status_code == 429 and r.get_json()["code"] == "too_many_pending"


# --------------------------------------------------------------------------- listing / permissions
def test_listing_shows_the_mirror_and_hardware_history(admin, online):
    registry([("flow", 1, "99aabbccddee", "Kitchen", "paired"), ("flow", 2, MAC_B, "Garden", "paired")], rev=2, now=real_now())   # node 1 got a new board
    j = admin("get", f"/api/nodes/{ref(1)}").get_json()
    assert j["label"] == "Node 1" and j["mac"] == "99:AA:BB:CC:DD:EE" and [h["mac"] for h in j["hardware"]] == ["44:7B:30:85:4B:88", "99:AA:BB:CC:DD:EE"]


def test_nearby_lists_unregistered_devices_only(app, cfg, admin, online):
    cfg.cmd_key_hex = KEY_HEX
    from protocol_v2 import cmd_tag
    key = parse_cmd_key(KEY_HEX)

    def ev(mac, kind="D"):
        body = f"AQE1 {kind} 0 {mac} 1 20 9 -50 0000abcd 0 - - {int(time.time())}"
        return f"{body} {cmd_tag(key, body)}"
    with database.db_session() as c:
        assert gateway_v2.handle_pair_event(c, cfg, ev(MAC_NEW), real_now())["kind"] == "nearby"
        gateway_v2.handle_pair_event(c, cfg, ev(MAC_A), real_now())              # already registered -> hidden
        assert gateway_v2.handle_pair_event(c, cfg, ev("010203040506").replace("0000abcd", "0000abcd")[:-16] + "0" * 16, real_now())["result"] == "rejected"   # bad tag
    near = admin("get", "/api/nodes/nearby").get_json()["nearby"]
    assert [n["mac"] for n in near] == ["A1:B2:C3:D4:E5:F6"]


def test_only_admins_may_send_commands_and_csrf_is_required(app, cfg, admin, online, sent):
    with database.db_session() as c:
        c.execute("INSERT INTO users (first_name,last_name,email,password_hash,role,must_change_password) VALUES ('U','S',?,?, 'user', 0)", (USER_EMAIL, generate_password_hash(USER_PW)))
    u = app.test_client()
    h = {"X-CSRF-Token": login(u, USER_EMAIL, USER_PW)}
    assert u.get("/api/nodes", headers=h).status_code == 200
    assert u.patch(f"/api/nodes/{ref(1)}", headers=h, json={"name": "X"}).status_code == 403
    assert u.delete(f"/api/nodes/{ref(1)}", headers=h).status_code == 403
    assert u.post("/api/nodes", headers=h, json={}).status_code == 403
    a = app.test_client(); login(a)
    assert a.delete(f"/api/nodes/{ref(1)}").status_code == 403                    # admin session without the CSRF header
    assert app.test_client().get("/api/nodes").status_code == 401
    assert sent == []
