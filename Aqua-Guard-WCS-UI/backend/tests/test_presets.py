"""Pipeline presets: the catalogue, creating inactive pipelines from a preset, and the zones each preset is meant to produce."""
import pytest
from werkzeug.security import generate_password_hash

import database
from tests.conftest import USER_EMAIL, USER_PW, login
from tests.v2helpers import registry

MACS = ["447b30854b88", "aabbcc000002", "aabbcc000003", "aabbcc000004"]


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def admin(client):
    h = {"X-CSRF-Token": login(client)}
    return lambda m, url, **kw: getattr(client, m)(url, headers=h, **kw)


@pytest.fixture()
def four(app):
    registry([("flow", i + 1, MACS[i], f"N{i + 1}", "paired") for i in range(4)])
    with database.db_session() as c:
        return [r["id"] for r in c.execute("SELECT id FROM nodes WHERE type='flow' ORDER BY number")]


def classes(admin, pid):
    z = admin("get", f"/api/pipelines/{pid}").get_json()["zones"]
    return {x["id"]: x["class"] for x in z}


def test_catalogue_describes_every_preset(admin):
    r = admin("get", "/api/pipelines/presets").get_json()["presets"]
    assert [p["id"] for p in r] == ["calibration_rig", "leak_bench", "branch_balance", "single_meter_house", "my_house"]
    for p in r:
        assert p["description"] and len(p["physicalSetup"]) >= 3 and len(p["slots"]) == p["maxNodes"] and p["minNodes"] >= 1


@pytest.mark.parametrize("preset,count,expected", [
    ("calibration_rig", 3, {"zone:flow1": "strong", "zone:flow2": "strong", "zone:flow3": "weak"}),
    ("leak_bench", 2, {"zone:flow1": "strong", "zone:flow2": "weak"}),
    ("branch_balance", 3, {"zone:flow1": "strong", "zone:flow2": "weak", "zone:flow3": "weak"}),
    ("single_meter_house", 1, {"zone:flow1": "weak"}),
    ("my_house", 4, {"zone:flow1": "strong", "zone:flow2": "weak", "zone:flow3": "weak", "zone:flow4": "weak"}),
])
def test_each_preset_is_valid_inactive_and_has_the_intended_zones(admin, four, preset, count, expected):
    r = admin("post", "/api/pipelines/from-preset", json={"preset": preset, "nodeIds": four[:count]})
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["valid"] is True and body["activeVersion"] is None and body["missingNodes"] == 0
    got = classes(admin, body["id"])
    assert {k: v for k, v in got.items() if k != "zone:source"} == expected
    assert next(p for p in admin("get", "/api/pipelines").get_json()["pipelines"] if p["id"] == body["id"])["activeVersion"] is None


def test_a_preset_never_changes_the_active_pipeline(admin, four):
    first = admin("post", "/api/pipelines/from-preset", json={"preset": "my_house", "nodeIds": four}).get_json()["id"]
    assert admin("post", f"/api/pipelines/{first}/activate", json={"version": 1}).status_code == 200
    other = admin("post", "/api/pipelines/from-preset", json={"preset": "leak_bench", "nodeIds": four[:2], "name": "Bench"}).get_json()
    assert other["activeVersion"] is None
    lst = {p["id"]: p for p in admin("get", "/api/pipelines").get_json()["pipelines"]}
    assert lst[first]["activeVersion"] == 1 and lst[other["id"]]["name"] == "Bench"


def test_the_leak_bench_marks_the_leak_tap(admin, four):
    pid = admin("post", "/api/pipelines/from-preset", json={"preset": "leak_bench", "nodeIds": four[:2]}).get_json()["id"]
    parts = admin("get", f"/api/pipelines/{pid}").get_json()["doc"]["parts"]
    assert any(p["kind"] == "cap" and "Leak tap" in p["label"] for p in parts)
    assert [p["number"] for p in parts if p["kind"] == "node"] == [1, 2]


def test_missing_nodes_leave_the_meters_out_instead_of_failing(admin, four):
    r = admin("post", "/api/pipelines/from-preset", json={"preset": "my_house", "nodeIds": four[:1]})
    assert r.status_code == 201 and r.get_json()["missingNodes"] == 3
    r = admin("post", "/api/pipelines/from-preset", json={"preset": "leak_bench"})
    assert r.status_code == 201 and r.get_json()["missingNodes"] == 2


def test_bad_requests(admin, four):
    assert admin("post", "/api/pipelines/from-preset", json={"preset": "nope"}).status_code == 400
    assert admin("post", "/api/pipelines/from-preset", json={"preset": "leak_bench", "nodeIds": four[:3]}).status_code == 400          # too many
    assert admin("post", "/api/pipelines/from-preset", json={"preset": "leak_bench", "nodeIds": [four[0], four[0]]}).status_code == 400
    assert admin("post", "/api/pipelines/from-preset", json={"preset": "leak_bench", "nodeIds": [9999]}).status_code == 400


def test_users_can_list_presets_but_not_create(app, admin, four):
    with database.db_session() as c:
        c.execute("INSERT INTO users (first_name,last_name,email,password_hash,role,must_change_password) VALUES ('U','S',?,?, 'user', 0)", (USER_EMAIL, generate_password_hash(USER_PW)))
    u = app.test_client()
    h = {"X-CSRF-Token": login(u, USER_EMAIL, USER_PW)}
    assert u.get("/api/pipelines/presets", headers=h).status_code == 200
    assert u.post("/api/pipelines/from-preset", headers=h, json={"preset": "leak_bench"}).status_code == 403
