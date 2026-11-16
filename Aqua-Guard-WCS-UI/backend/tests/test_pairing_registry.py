"""Milestone 3: device registry + secure pairing (backend side)."""
import struct
import time
from datetime import datetime, timezone

import pytest
from werkzeug.security import generate_password_hash

import database
import ingest
import pairing
from tests.conftest import ADMIN_EMAIL, ADMIN_PW, USER_EMAIL, USER_PW, flow_msg, login

# Same key as tests/firmware/sim_harness.h (GMK) - vectors below were printed by the C++ library test.
GMK_HEX = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
GMK = bytes.fromhex(GMK_HEX)


@pytest.fixture()
def cfg(cfg):
    cfg.gmk_hex = GMK_HEX
    return cfg


@pytest.fixture()
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(pairing, "publisher", lambda text: (out.append(text) or True))
    return out


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def admin(client):
    csrf = login(client)
    return lambda method, url, **kw: getattr(client, method)(url, headers={"X-CSRF-Token": csrf}, **kw)


def now_epoch():
    return int(datetime.now(timezone.utc).timestamp())


def provision(name="Kitchen", uid=None):
    with database.db_session() as conn:
        info, code, secret = pairing.provision_device(conn, GMK, name, "flow", uid)
    return info["uid"], code, secret


def ev(kind, uid, mac="5C0000000031", dtype=1, fw=256, ch=6, rssi=-50, boot=0xDEADBEEF, slot=0, chal=None, resp=None, epoch=None, key=None):
    body = "AQE1 %s %d %s %d %d %d %d %s %d %s %s %d" % (
        kind, uid, mac.lower(), dtype, fw, ch, rssi, struct.pack("<I", boot).hex(), slot,
        chal.hex() if chal else "-", resp.hex() if resp else "-", epoch if epoch is not None else now_epoch())
    return body + " " + pairing.text_tag(key or pairing.cmd_key(GMK), body)


def feed(app, cfg, text):
    with database.db_session() as conn:
        return pairing.handle_pair_event(conn, cfg, text)


def do_pair(app, cfg, admin, sent, uid, code, boot=0xDEADBEEF):
    """Run the complete owner + gateway + device handshake; returns the registry slot."""
    feed(app, cfg, ev("D", uid))
    r = admin("post", f"/api/registry/devices/{uid}/pair", json={"code": code, "name": "Kitchen sensor"})
    assert r.status_code == 202, r.get_json()
    slot = r.get_json()["slot"]
    cmd = sent[-1].split()
    assert cmd[1] == "auth" and int(cmd[2]) == uid and int(cmd[3]) == slot
    chal = bytes.fromhex(cmd[5])
    resp = pairing.pair_mac(pairing.device_secret(GMK, uid), "R", uid, boot, chal, 0)
    res = feed(app, cfg, ev("O", uid, slot=slot, boot=boot, chal=chal, resp=resp))
    assert res["result"] == "paired", res
    return slot


# ---------------------------------------------------------------- crypto cross-check with the C++ library
def test_python_matches_cpp_vectors():
    s = pairing.kdf(GMK, "S", 4, 0)
    assert s.hex() == "afb4ed97d12df46eb0fab91af7379e2e"
    assert pairing.kdf(s, "K", 4, 0xA1B2C3D5).hex() == "7a077bdf14cd383e7456a00b546f9618"
    assert pairing.cmd_key(GMK).hex() == "98666ef59fce97e437e16f4da0de8fa4"
    assert pairing.pair_mac(s, "R", 4, 0xA1B2C3D5, bytes([1, 2, 3, 4, 5, 6, 7, 8]), 0).hex() == "aee6a9d7d22d7746"
    body = "AQE1 O 7 5c0000000031 1 256 6 -50 efbeadde 4 0102030405060708 abababababababab 1700000000"
    assert pairing.text_tag(pairing.cmd_key(GMK), body) == "f24ae4df8a0e1913"


# ---------------------------------------------------------------- migration / legacy compatibility
def test_legacy_devices_seeded_and_old_data_untouched(app, conn):
    rows = {r["uid"]: r for r in conn.execute("SELECT * FROM device_registry")}
    assert set(rows) == {1, 2, 3} and all(r["legacy"] and r["pairing_status"] == "paired" for r in rows.values())
    assert rows[1]["slot"] == 1 and rows[3]["device_type"] == "rain"


def test_legacy_telemetry_still_ingests_and_totals_match(app, conn):
    with database.db_session() as c:
        r = ingest.ingest_flow(c, flow_msg(node=1, boot=77, seq=1, cum=900, interval=900), expected_node=1)
        ingest.ingest_flow(c, flow_msg(node=2, boot=88, seq=1, cum=450, interval=450), expected_node=2)
    assert r["result"] == "stored" and r["delta_pulses"] == 900
    assert conn.execute("SELECT COUNT(*) c FROM flow_samples WHERE node_id IN (1,2)").fetchone()["c"] == 2


def test_legacy_cannot_be_unpaired_but_can_be_renamed_and_calibrated(app, cfg, admin, sent):
    assert admin("post", "/api/registry/devices/1/unpair").status_code == 409
    r = admin("put", "/api/registry/devices/1", json={"displayName": "Main inlet", "calibrationPulsesPerLiter": 460})
    assert r.status_code == 200
    with database.db_session() as c:
        res = ingest.ingest_flow(c, flow_msg(node=1, boot=5, seq=1, cum=460, interval=460, ppl=450.0, lpm=13.8), expected_node=1)
        row = c.execute("SELECT pulses_per_liter_x10, flow_lpm FROM flow_samples WHERE node_id=1").fetchone()
    assert row["pulses_per_liter_x10"] == 4600 and abs(row["flow_lpm"] - 13.8 * 450 / 460) < 1e-6


# ---------------------------------------------------------------- provisioning / duplicates
def test_duplicate_and_reserved_uid_rejected(app, cfg):
    uid, _, _ = provision(uid=150)
    with pytest.raises(pairing.PairingError) as e:
        provision(uid=150)
    assert e.value.code == "DUPLICATE"
    with pytest.raises(pairing.PairingError):
        provision(uid=7)                                   # < 100 reserved for built-in nodes
    with database.db_session() as c:
        with pytest.raises(Exception):                     # DB-level uniqueness as the last line of defence
            c.execute("INSERT INTO device_registry (uid,device_id,device_type,display_name,pairing_status) VALUES (150,'X','flow','x','provisioned')")


def test_pairing_code_stored_hashed_only(app, conn):
    uid, code, _ = provision()
    row = conn.execute("SELECT code_hash FROM device_registry WHERE uid=?", (uid,)).fetchone()
    assert code.replace("-", "") not in row["code_hash"] and row["code_hash"].startswith("pbkdf2:")


# ---------------------------------------------------------------- full happy path
def test_full_pairing_flow_and_real_telemetry(app, cfg, admin, sent, conn):
    uid, code, _ = provision("Garden")
    slot = do_pair(app, cfg, admin, sent, uid, code)
    assert slot == 4
    d = admin("get", f"/api/registry/devices/{uid}").get_json()
    assert d["pairingStatus"] == "paired" and d["displayName"] == "Kitchen sensor" and d["mac"] == "5C:00:00:00:00:31"
    assert d["firmwareVersion"] == "1.0" and d["deviceId"] == f"AG-FLOW-{uid:03d}"
    # telemetry in the assigned slot is accepted, stored under the uid, and visible through the registry API
    msg = flow_msg(node=slot, boot=4242, seq=1, cum=300, interval=300, uid=uid)
    with database.db_session() as c:
        res = ingest.ingest_flow(c, msg, expected_node=slot)
    assert res["result"] == "stored" and res["delta_pulses"] == 300
    d = admin("get", f"/api/registry/devices/{uid}").get_json()
    assert d["state"] == "online" and d["latest"]["cumPulses"] == 300 and abs(d["latest"]["totalLiters"] - 300 / 450) < 1e-3
    assert conn.execute("SELECT COUNT(*) c FROM flow_samples WHERE node_id=?", (uid,)).fetchone()["c"] == 1
    # discovery list no longer shows it
    assert admin("get", "/api/registry/discovered").get_json()["devices"] == []


# ---------------------------------------------------------------- unauthorised attempts
def test_wrong_code_counts_and_locks(app, cfg, admin, sent):
    uid, code, _ = provision()
    feed(app, cfg, ev("D", uid))
    for i in range(cfg.pairing_code_max_failures):
        r = admin("post", f"/api/registry/devices/{uid}/pair", json={"code": "AAAA-BBBB"})
        assert r.status_code == 403 and r.get_json()["code"] == "BAD_CODE"
    r = admin("post", f"/api/registry/devices/{uid}/pair", json={"code": code})          # even the right code is refused while locked
    assert r.status_code == 429 and r.get_json()["code"] == "LOCKED"
    assert sent == []                                                                    # nothing was ever authorised
    with database.db_session() as c:
        kinds = [r["kind"] for r in c.execute("SELECT kind FROM pairing_attempts WHERE uid=?", (uid,))]
    assert kinds.count("code_bad") == cfg.pairing_code_max_failures and "code_locked" in kinds


def test_unknown_unprovisioned_device_cannot_be_paired(app, cfg, admin, sent):
    feed(app, cfg, ev("D", 9999))                                   # shows up in discovery...
    items = admin("get", "/api/registry/discovered").get_json()["devices"]
    assert items and items[0]["uid"] == 9999 and items[0]["provisioned"] is False and items[0]["canPair"] is False
    r = admin("post", "/api/registry/devices/9999/pair", json={"code": "ABCD-EFGH"})
    assert r.status_code == 404 and sent == []


def test_telemetry_refused_before_pairing_and_for_wrong_slot_or_uid(app, cfg, admin, sent):
    uid, code, _ = provision()
    msg = flow_msg(node=4, boot=1, seq=1, uid=uid)
    with database.db_session() as c:
        with pytest.raises(ingest.IngestError):
            ingest.ingest_flow(c, msg, expected_node=4)             # not paired yet
    slot = do_pair(app, cfg, admin, sent, uid, code)
    with database.db_session() as c:
        with pytest.raises(ingest.IngestError):
            ingest.ingest_flow(c, flow_msg(node=5, boot=1, seq=1, uid=uid), expected_node=5)      # right device, other slot
        with pytest.raises(ingest.IngestError):
            ingest.ingest_flow(c, flow_msg(node=slot, boot=1, seq=1, uid=uid + 1), expected_node=slot)   # other uid in this slot
        with pytest.raises(ingest.IngestError):
            ingest.ingest_flow(c, flow_msg(node=slot, boot=1, seq=1), expected_node=slot)           # no uid at all


def test_forged_stale_and_bad_proof_events_do_not_pair(app, cfg, admin, sent, conn):
    uid, code, _ = provision()
    feed(app, cfg, ev("D", uid))
    r = admin("post", f"/api/registry/devices/{uid}/pair", json={"code": code})
    slot, chal = r.get_json()["slot"], bytes.fromhex(sent[-1].split()[5])
    good = pairing.pair_mac(pairing.device_secret(GMK, uid), "R", uid, 0x1234, chal, 0)
    wrong_key = bytes(range(16))
    assert feed(app, cfg, ev("O", uid, slot=slot, boot=0x1234, chal=chal, resp=good, key=wrong_key))["reason"] == "tag"           # not our gateway
    assert feed(app, cfg, ev("O", uid, slot=slot, boot=0x1234, chal=chal, resp=good, epoch=now_epoch() - 4000))["reason"] == "stale"   # replay
    bad = feed(app, cfg, ev("O", uid, slot=slot, boot=0x1234, chal=chal, resp=bytes(8)))                                             # wrong proof
    assert bad["result"] == "challenge_failed"
    other = feed(app, cfg, ev("O", uid, slot=slot, boot=0x1234, chal=bytes(range(8)), resp=good))                                     # chal we never issued
    assert other["reason"] == "challenge"
    assert conn.execute("SELECT pairing_status FROM device_registry WHERE uid=?", (uid,)).fetchone()["pairing_status"] == "pairing"
    # an attacker who forges the response for a different uid also fails
    assert feed(app, cfg, ev("O", uid + 1, slot=slot, boot=1, chal=chal, resp=good))["reason"] == "unexpected"
    assert feed(app, cfg, "AQE1 garbage")["reason"] == "format"


def test_gateway_reported_failure_is_recorded(app, cfg, admin, sent):
    uid, code, _ = provision()
    feed(app, cfg, ev("D", uid))
    admin("post", f"/api/registry/devices/{uid}/pair", json={"code": code})
    assert feed(app, cfg, ev("F", uid, slot=4))["result"] == "challenge_failed"
    d = admin("get", f"/api/registry/devices/{uid}").get_json()
    assert d["pairingStatus"] == "pairing" and "secure challenge" in d["pairingError"]


def test_only_admin_with_csrf_can_pair(app, cfg, client, sent):
    uid, code, _ = provision()
    with database.db_session() as c:
        c.execute("INSERT INTO users (first_name,last_name,email,password_hash,role,must_change_password) VALUES ('U','S',?,?, 'user', 0)",
                  (USER_EMAIL, generate_password_hash(USER_PW)))
    csrf = login(client, USER_EMAIL, USER_PW)
    assert client.post(f"/api/registry/devices/{uid}/pair", headers={"X-CSRF-Token": csrf}, json={"code": code}).status_code == 403
    assert client.get("/api/registry/devices", headers={"X-CSRF-Token": csrf}).status_code == 200           # read-only access is fine
    c2 = client.application.test_client()
    assert c2.get("/api/registry/devices").status_code == 401                                                # anonymous
    login(c2)
    assert c2.post(f"/api/registry/devices/{uid}/pair", json={"code": code}).status_code == 403             # admin but no CSRF token
    assert sent == []


def test_pairing_not_configured_without_gmk(app, cfg, admin, sent):
    uid, code, _ = provision()
    cfg.gmk_hex = ""
    r = admin("post", f"/api/registry/devices/{uid}/pair", json={"code": code})
    assert r.status_code == 503 and r.get_json()["code"] == "NOT_CONFIGURED"
    assert feed(app, cfg, ev("D", uid))["result"] == "not_configured"


# ---------------------------------------------------------------- restart / reboot behaviour
def test_gateway_restart_backend_reauthorises_paired_device_only(app, cfg, admin, sent):
    uid, code, _ = provision()
    slot = do_pair(app, cfg, admin, sent, uid, code)
    n = len(sent)
    res = feed(app, cfg, ev("D", uid))                              # gateway rebooted, device announces again
    assert res["result"] == "reauthorised" and len(res["commands"]) == 1
    parts = res["commands"][0].split()
    assert parts[1] == "auth" and int(parts[3]) == slot and parts[5] == "-"     # gateway picks a fresh challenge itself
    stranger, _, _ = provision("Stranger")
    assert feed(app, cfg, ev("D", stranger))["commands"] == []      # an unpaired device is never auto-authorised


def test_node_reboot_reverified_with_new_boot(app, cfg, admin, sent, conn):
    uid, code, _ = provision()
    slot = do_pair(app, cfg, admin, sent, uid, code, boot=0x1111)
    chal = bytes(range(8))
    resp = pairing.pair_mac(pairing.device_secret(GMK, uid), "R", uid, 0x2222, chal, 0)
    assert feed(app, cfg, ev("O", uid, slot=slot, boot=0x2222, chal=chal, resp=resp))["result"] == "paired"
    assert conn.execute("SELECT last_boot_id FROM device_registry WHERE uid=?", (uid,)).fetchone()["last_boot_id"] == 0x2222
    # a replayed/forged proof for a paired device is still checked
    assert feed(app, cfg, ev("O", uid, slot=slot, boot=0x3333, chal=chal, resp=resp))["result"] == "challenge_failed"


def test_pairing_timeout_returns_device_to_provisioned(app, cfg, admin, sent, conn):
    uid, code, _ = provision()
    feed(app, cfg, ev("D", uid))
    admin("post", f"/api/registry/devices/{uid}/pair", json={"code": code})
    conn.execute("UPDATE device_registry SET pairing_started=? WHERE uid=?", (time.time() - 1000, uid))
    with database.db_session() as c:
        pairing.expire_stale_pairings(c, cfg)
    row = conn.execute("SELECT pairing_status, slot FROM device_registry WHERE uid=?", (uid,)).fetchone()
    assert row["pairing_status"] == "provisioned" and row["slot"] is None


# ---------------------------------------------------------------- unpair / slots / rename / calibration
def test_unpair_revokes_and_invalidates_code(app, cfg, admin, sent):
    uid, code, _ = provision()
    slot = do_pair(app, cfg, admin, sent, uid, code)
    r = admin("post", f"/api/registry/devices/{uid}/unpair")
    assert r.status_code == 200 and sent[-1].split()[1] == "revoke"
    with database.db_session() as c:
        with pytest.raises(ingest.IngestError):
            ingest.ingest_flow(c, flow_msg(node=slot, boot=9, seq=1, uid=uid), expected_node=slot)
    assert admin("post", f"/api/registry/devices/{uid}/pair", json={"code": code}).status_code == 403     # old code is dead
    code2 = admin("post", f"/api/registry/devices/{uid}/new-code").get_json()["pairingCode"]
    feed(app, cfg, ev("D", uid))
    assert admin("post", f"/api/registry/devices/{uid}/pair", json={"code": code2}).status_code == 202


def test_slot_allocation_exhaustion_and_reuse(app, cfg, admin, sent):
    uids = [provision(f"d{i}")[:2] for i in range(10)]
    slots = []
    for uid, code in uids[:9]:
        slots.append(do_pair(app, cfg, admin, sent, uid, code))
    assert slots == list(range(4, 13))
    uid, code = uids[9]
    feed(app, cfg, ev("D", uid))
    assert admin("post", f"/api/registry/devices/{uid}/pair", json={"code": code}).get_json()["code"] == "NO_SLOT"
    admin("post", f"/api/registry/devices/{uids[2][0]}/unpair")
    assert admin("post", f"/api/registry/devices/{uid}/pair", json={"code": code}).get_json()["slot"] == slots[2]


def test_rename_and_calibration_validation_and_effect(app, cfg, admin, sent):
    uid, code, _ = provision()
    slot = do_pair(app, cfg, admin, sent, uid, code)
    assert admin("put", f"/api/registry/devices/{uid}", json={"displayName": ""}).status_code == 400
    assert admin("put", f"/api/registry/devices/{uid}", json={"calibrationPulsesPerLiter": 5}).status_code == 400
    assert admin("put", f"/api/registry/devices/{uid}", json={"calibrationPulsesPerLiter": "x"}).status_code == 400
    assert admin("put", f"/api/registry/devices/{uid}", json={"displayName": "Bathroom", "calibrationPulsesPerLiter": 500}).status_code == 200
    with database.db_session() as c:
        ingest.ingest_flow(c, flow_msg(node=slot, boot=3, seq=1, cum=500, interval=500, uid=uid, lpm=15.0), expected_node=slot)
        row = c.execute("SELECT pulses_per_liter_x10, flow_lpm, delta_pulses FROM flow_samples WHERE node_id=?", (uid,)).fetchone()
    assert row["pulses_per_liter_x10"] == 5000 and abs(row["flow_lpm"] - 13.5) < 1e-6 and row["delta_pulses"] == 500
    d = admin("get", f"/api/registry/devices/{uid}").get_json()
    assert d["displayName"] == "Bathroom" and abs(d["latest"]["totalLiters"] - 1.0) < 1e-3


def test_mqtt_topic_routing_and_signature(app, cfg, admin, sent):
    import json as _j
    import mqtt_service
    uid, code, _ = provision()
    assert mqtt_service.handle_message(cfg, f"{cfg.mqtt_topic_prefix}/pairing/event", ev("D", uid).encode()) == "stored"
    assert mqtt_service.handle_message(cfg, f"{cfg.mqtt_topic_prefix}/pairing/event", ev("D", uid, key=bytes(16)).encode()) == "rejected"
    slot = do_pair(app, cfg, admin, sent, uid, code)
    payload = _j.dumps(flow_msg(node=slot, boot=77, seq=1, cum=100, interval=100, uid=uid)).encode()
    assert mqtt_service.handle_message(cfg, f"{cfg.mqtt_topic_prefix}/node{slot}", payload) == "stored"
    assert mqtt_service.handle_message(cfg, f"{cfg.mqtt_topic_prefix}/node{slot}", payload) == "duplicate"
    assert mqtt_service.handle_message(cfg, f"{cfg.mqtt_topic_prefix}/node{slot + 1}", payload) == "rejected"      # topic/payload mismatch


def test_gateway_heartbeat_marks_dynamic_device_alive(app, cfg, admin, sent, conn):
    from tests.conftest import gw_heartbeat
    uid, code, _ = provision()
    slot = do_pair(app, cfg, admin, sent, uid, code)
    hb = gw_heartbeat(nodes=[{"node": slot, "uid": uid, "keyed": True, "ageS": 2, "received": 10}, {"node": 9, "uid": 5555, "ageS": 1},
                             {"node": 1, "ageS": 3}])
    with database.db_session() as c:
        ingest.ingest_gateway_heartbeat(c, hb)
    row = conn.execute("SELECT gw_last_seen FROM devices WHERE device_key=?", (f"dev{uid}",)).fetchone()
    assert row and row["gw_last_seen"]
    assert conn.execute("SELECT COUNT(*) c FROM devices WHERE device_key LIKE 'dev%'").fetchone()["c"] == 1     # slot 9/uid 5555 ignored
    assert admin("get", f"/api/registry/devices/{uid}").get_json()["state"] == "online"
