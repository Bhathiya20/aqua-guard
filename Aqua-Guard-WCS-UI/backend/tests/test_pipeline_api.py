"""Pipeline REST API: versions, drafts vs active, validation endpoint, export/import, permissions."""
import json

import pytest
from werkzeug.security import generate_password_hash

import database
from tests import netdocs as nd
from tests.conftest import USER_EMAIL, USER_PW, login
from tests.v2helpers import registry

MAC_A, MAC_B = "447b30854b88", "aabbcc000002"


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def admin(client):
    csrf = login(client)
    h = {"X-CSRF-Token": csrf}

    def call(method, url, **kw):
        return getattr(client, method)(url, headers=h, **kw)
    return call


@pytest.fixture()
def paired(app):
    registry([("flow", 1, MAC_A, "Kitchen", "paired"), ("flow", 2, MAC_B, "Garden", "paired"), ("flow", 3, "112233445566", "Spare", "paired")])


def create(admin, d=None, **kw):
    r = admin("post", "/api/pipelines", json={"name": "House", "doc": (d or nd.straight()).doc(), **kw})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def test_create_valid_network_becomes_active_version_1(admin, paired):
    j = create(admin)
    assert j["valid"] and j["version"] == 1 and j["activeVersion"] == 1
    g = admin("get", f"/api/pipelines/{j['id']}").get_json()
    assert g["valid"] and g["activeVersion"] == 1 and [z["class"] for z in g["zones"]] == ["not_monitored", "strong", "weak"]
    assert g["coverage"]["text"].startswith("2 of 3 zones leak-monitored")
    assert [n["number"] for n in g["availableNodes"]] == [3]                         # only paired nodes that are not placed yet
    assert g["zones"][1]["equation"].startswith("Q_unaccounted(After Node 1)")


def test_saving_creates_a_new_version_and_invalid_drafts_do_not_replace_the_active_one(admin, paired):
    pid = create(admin)["id"]
    ok = admin("put", f"/api/pipelines/{pid}", json={"doc": nd.tee_tree().doc(), "note": "tee"}).get_json()
    assert ok["version"] == 2 and ok["valid"] and ok["activated"]
    broken = nd.tee_tree()
    broken.links = broken.links[:-1]                                                # o2 loses its pipe
    r = admin("put", f"/api/pipelines/{pid}", json={"doc": broken.doc(), "note": "draft"})
    j = r.get_json()
    assert r.status_code == 201 and j["version"] == 3 and not j["valid"] and not j["activated"] and any(m["partId"] == "o2" or m["partId"] == "p3" for m in j["messages"])
    v = admin("get", f"/api/pipelines/{pid}/versions").get_json()
    assert v["activeVersion"] == 2 and [x["version"] for x in v["versions"]] == [3, 2, 1]
    assert admin("get", f"/api/pipelines/{pid}").get_json()["version"] == 2           # the editor loads the ACTIVE version by default
    assert admin("get", f"/api/pipelines/{pid}?version=3").get_json()["valid"] is False
    assert admin("post", f"/api/pipelines/{pid}/activate", json={"version": 3}).status_code == 409
    assert admin("post", f"/api/pipelines/{pid}/activate", json={"version": 1}).get_json()["activeVersion"] == 1


def test_validate_endpoint_returns_clickable_messages_without_saving(admin, paired):
    bad = nd.tee_tree()
    bad.links = [lk for lk in bad.links if lk["from"]["port"] != "out2"]
    r = admin("post", "/api/pipelines/validate", json={"doc": bad.doc()}).get_json()
    assert not r["valid"] and any(m["partId"] == "t1" and "T junction 1" in m["message"] for m in r["messages"])
    assert admin("get", "/api/pipelines").get_json()["pipelines"] == []


def test_unpaired_or_double_placed_nodes_are_rejected_by_validation(admin, paired):
    d = nd.straight()
    d.node("n9", 9, "p2", 0.1)                                                       # node 9 is not paired
    r = admin("post", "/api/pipelines/validate", json={"doc": d.doc()}).get_json()
    assert any(m["code"] == "node_unknown" and m["partId"] == "n9" for m in r["messages"])


def test_export_import_roundtrip(admin, paired):
    pid = create(admin, nd.tee_tree())["id"]
    exp = admin("get", f"/api/pipelines/{pid}/export")
    assert exp.headers["Content-Disposition"].startswith("attachment") and exp.mimetype == "application/json"
    doc = json.loads(exp.data)
    r = admin("post", "/api/pipelines", json={"name": "Imported", "doc": doc})
    assert r.status_code == 201 and r.get_json()["id"] != pid
    assert admin("get", f"/api/pipelines/{r.get_json()['id']}").get_json()["doc"]["parts"] == doc["parts"]
    assert admin("post", "/api/pipelines", json={"doc": {"schema": 99}}).status_code == 400


def test_zone_settings_are_part_of_the_versioned_document(admin, paired):
    d = nd.straight().doc(zoneSettings={"zone:flow1": {"suspect_min": 3, "abs_min_lpm": 0.5}})
    pid = create(admin, nd.straight())["id"]
    admin("put", f"/api/pipelines/{pid}", json={"doc": d})
    assert admin("get", f"/api/pipelines/{pid}").get_json()["doc"]["zoneSettings"]["zone:flow1"]["suspect_min"] == 3


def test_delete_hides_the_pipeline(admin, paired):
    pid = create(admin)["id"]
    assert admin("delete", f"/api/pipelines/{pid}").status_code == 200
    assert admin("get", f"/api/pipelines/{pid}").status_code == 404 and admin("get", "/api/pipelines").get_json()["pipelines"] == []


def test_palette_lists_ports_and_outlet_types(client, admin):
    j = admin("get", "/api/pipelines/palette").get_json()
    assert {k["kind"] for k in j["kinds"]} == {"source", "pipe", "elbow", "valve", "tee", "cross", "outlet", "cap"} and "garden" in j["outletTypes"]
    assert next(k for k in j["kinds"] if k["kind"] == "tee")["ports"] == ["in", "out1", "out2"]


def test_only_admins_can_change_pipelines(app, client, admin, paired):
    pid = create(admin)["id"]
    with database.db_session() as c:
        c.execute("INSERT INTO users (first_name,last_name,email,password_hash,role,must_change_password) VALUES ('U','S',?,?, 'user', 0)", (USER_EMAIL, generate_password_hash(USER_PW)))
    u = app.test_client()
    h = {"X-CSRF-Token": login(u, USER_EMAIL, USER_PW)}
    assert u.get(f"/api/pipelines/{pid}", headers=h).status_code == 200                                       # reading is fine
    assert u.put(f"/api/pipelines/{pid}", headers=h, json={"doc": nd.straight().doc()}).status_code == 403
    assert u.post("/api/pipelines", headers=h, json={"doc": nd.straight().doc()}).status_code == 403
    assert u.delete(f"/api/pipelines/{pid}", headers=h).status_code == 403
    assert app.test_client().get("/api/pipelines").status_code == 401
    c3 = app.test_client(); login(c3)
    assert c3.post("/api/pipelines", json={"doc": nd.straight().doc()}).status_code == 403                  # admin without CSRF header


def test_rename_changes_only_the_name_and_a_duplicate_is_not_activated(admin, paired):
    j = create(admin)
    r = admin("patch", f"/api/pipelines/{j['id']}", json={"name": "Ground floor"})
    assert r.status_code == 200 and r.get_json()["name"] == "Ground floor"
    lst = admin("get", "/api/pipelines").get_json()["pipelines"]
    assert lst[0]["name"] == "Ground floor" and lst[0]["versions"] == 1 and lst[0]["activeVersion"] == 1          # no new version, still active
    assert admin("patch", f"/api/pipelines/{j['id']}", json={"name": "  "}).status_code == 400
    assert admin("patch", "/api/pipelines/999", json={"name": "x"}).status_code == 404
    full = admin("get", f"/api/pipelines/{j['id']}").get_json()
    c = admin("post", "/api/pipelines", json={"name": "Ground floor (copy)", "doc": full["doc"], "activate": False}).get_json()
    assert c["valid"] and c["activeVersion"] is None                                                               # copy stays inactive: only one pipeline is live
    assert [p["activeVersion"] for p in admin("get", "/api/pipelines").get_json()["pipelines"]] == [1, None]
