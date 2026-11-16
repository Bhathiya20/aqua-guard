"""Authentication, sessions, CSRF, roles, profile access, audit, and API smoke tests."""
import time

import pytest

import database
import ingest
from database import db_session
from tests.conftest import (ADMIN_EMAIL, ADMIN_PW, T0, USER_EMAIL, USER_PW, flow_msg, gw_heartbeat, login, rain_msg)


@pytest.fixture()
def client(app):
    return app.test_client()


def make_user(client, csrf_admin):
    r = client.post("/api/admin/users", json={"email": USER_EMAIL, "password": USER_PW, "role": "user", "firstName": "Una", "lastName": "User"},
                    headers={"X-CSRF-Token": csrf_admin})
    assert r.status_code == 201, r.get_json()
    # clear the forced change flag so we can use the account (admin-created accounts must change password)
    c = database.get_db(); c.execute("UPDATE users SET must_change_password=0 WHERE lower(email)=lower(?)", (USER_EMAIL,)); c.close()


@pytest.fixture()
def two_clients(app):
    a, u = app.test_client(), app.test_client()
    csrf_a = login(a)
    make_user(a, csrf_a)
    csrf_u = login(u, USER_EMAIL, USER_PW)
    return a, csrf_a, u, csrf_u


def test_health_is_public_everything_else_requires_login(client):
    assert client.get("/api/health").status_code == 200
    for path in ("/api/overview", "/api/water/live", "/api/leaks/live", "/api/rainwater/live", "/api/reports",
                 "/api/profile", "/api/me", "/api/alerts", "/api/system/status", "/api/admin/users", "/api/devices"):
        assert client.get(path).status_code == 401, path


def test_login_sets_httponly_samesite_cookie_and_returns_csrf(client):
    r = client.post("/api/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PW})
    assert r.status_code == 200
    cookie = r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "aquaguard_session=" in cookie
    body = r.get_json()
    assert body["user"]["role"] == "admin" and body["csrfToken"] and "password" not in str(body).lower().replace("mustchangepassword", "")


def test_bad_credentials_generic_error_and_lockout(client):
    for _ in range(4):
        r = client.post("/api/login", json={"email": ADMIN_EMAIL, "password": "wrong-password-1"})
        assert r.status_code == 401 and r.get_json()["error"] == "Invalid email or password"
    assert client.post("/api/login", json={"email": "nobody@x.y", "password": "whatever-1A"}).status_code == 401
    assert client.post("/api/login", json={"email": ADMIN_EMAIL, "password": "wrong-password-1"}).status_code == 401   # 5th failure locks
    r = client.post("/api/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PW})                                   # correct pw, still locked
    assert r.status_code == 429


def test_logout_revokes_session_server_side(client):
    csrf = login(client)
    cookie = client.get_cookie("aquaguard_session").value
    assert client.get("/api/me").status_code == 200
    assert client.post("/api/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    # replay the old cookie: must be rejected even though the browser would still hold it
    client.set_cookie("aquaguard_session", cookie)
    assert client.get("/api/me").status_code == 401


def test_idle_session_expires(app, cfg, client):
    login(client)
    c = database.get_db(); c.execute("UPDATE sessions SET last_seen = last_seen - ?", (cfg.session_idle_minutes * 60 + 5,)); c.close()
    assert client.get("/api/me").status_code == 401


def test_absolute_session_expiry(app, client):
    login(client)
    c = database.get_db(); c.execute("UPDATE sessions SET expires_at = ?", (time.time() - 1,)); c.close()
    assert client.get("/api/me").status_code == 401


def test_csrf_required_for_state_changing_requests(two_clients):
    a, csrf_a, u, csrf_u = two_clients
    body = {"building": "A", "location": "Roof", "description": "drip"}
    assert u.post("/api/reports", json=body).status_code == 403                                   # no token
    assert u.post("/api/reports", json=body, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert u.post("/api/reports", json=body, headers={"X-CSRF-Token": csrf_a}).status_code == 403  # another session's token
    assert u.post("/api/reports", json=body, headers={"X-CSRF-Token": csrf_u}).status_code == 201


def test_roles_enforced_on_server(two_clients):
    a, csrf_a, u, csrf_u = two_clients
    rep = u.post("/api/reports", json={"building": "B", "location": "Lab", "description": "leak"}, headers={"X-CSRF-Token": csrf_u}).get_json()
    # normal user cannot change report status or reach admin APIs - even with a valid CSRF token
    assert u.put(f"/api/reports/{rep['dbId']}", json={"status": "resolved"}, headers={"X-CSRF-Token": csrf_u}).status_code == 403
    assert u.get("/api/admin/users").status_code == 403
    assert u.get("/api/admin/audit").status_code == 403
    assert u.post("/api/admin/users", json={"email": "x@y.z", "password": USER_PW}, headers={"X-CSRF-Token": csrf_u}).status_code == 403
    # admin can
    r = a.put(f"/api/reports/{rep['dbId']}", json={"status": "resolved"}, headers={"X-CSRF-Token": csrf_a})
    assert r.status_code == 200 and r.get_json()["status"] == "resolved"
    assert a.put(f"/api/reports/{rep['dbId']}", json={"status": "bogus"}, headers={"X-CSRF-Token": csrf_a}).status_code == 400
    assert a.get("/api/admin/users").status_code == 200


def test_reporter_identity_comes_from_session_not_client(two_clients):
    a, csrf_a, u, csrf_u = two_clients
    r = u.post("/api/reports", json={"building": "B", "location": "L", "description": "d", "reportedBy": "Someone Else"},
               headers={"X-CSRF-Token": csrf_u}).get_json()
    assert r["reportedBy"] == "Una User"


def test_profile_is_limited_to_self_unless_admin(two_clients):
    a, csrf_a, u, csrf_u = two_clients
    assert u.get(f"/api/profile?email={USER_EMAIL}").status_code == 200
    assert u.get(f"/api/profile?email={ADMIN_EMAIL}").status_code == 403
    r = u.put("/api/profile", json={"currentEmail": ADMIN_EMAIL, "firstName": "Hacked"}, headers={"X-CSRF-Token": csrf_u})
    assert r.status_code == 403
    assert a.get(f"/api/profile?email={USER_EMAIL}").status_code == 200       # admin may view
    # duplicate email is a clean 409, not a 500
    r = u.put("/api/profile", json={"currentEmail": USER_EMAIL, "email": ADMIN_EMAIL}, headers={"X-CSRF-Token": csrf_u})
    assert r.status_code == 409
    # role cannot be changed through the profile endpoint
    u.put("/api/profile", json={"currentEmail": USER_EMAIL, "role": "admin", "firstName": "Una2"}, headers={"X-CSRF-Token": csrf_u})
    assert u.get("/api/me").get_json()["user"]["role"] == "user"


def test_password_policy_and_change_revokes_other_sessions(app):
    s1, s2 = app.test_client(), app.test_client()
    csrf1 = login(s1); login(s2)
    h = {"X-CSRF-Token": csrf1}
    assert s1.post("/api/change-password", json={"currentPassword": ADMIN_PW, "newPassword": "short"}, headers=h).status_code == 400
    assert s1.post("/api/change-password", json={"currentPassword": "nope", "newPassword": "BrandNewPass-123"}, headers=h).status_code == 400
    assert s1.post("/api/change-password", json={"currentPassword": ADMIN_PW, "newPassword": "BrandNewPass-123"}, headers=h).status_code == 200
    assert s2.get("/api/me").status_code == 401           # other session revoked
    assert s1.get("/api/me").status_code == 200
    assert app.test_client().post("/api/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PW}).status_code == 401


def test_default_admin_password_from_old_release_forces_change(app, cfg):
    from werkzeug.security import generate_password_hash
    c = database.get_db()
    c.execute("UPDATE users SET password_hash=? WHERE role='admin'", (generate_password_hash("Admin.AquaGuard"),))
    c.close()
    database.init_db(cfg.default_admin_email, cfg.default_admin_password)       # simulates the next start-up
    cl = app.test_client()
    r = cl.post("/api/login", json={"email": ADMIN_EMAIL, "password": "Admin.AquaGuard"})
    assert r.status_code == 200 and r.get_json()["mustChangePassword"] is True
    assert cl.get("/api/overview").status_code == 403                             # blocked until changed
    assert cl.get("/api/me").status_code == 200


def test_audit_log_records_security_events(two_clients):
    a, csrf_a, u, csrf_u = two_clients
    u.get("/api/admin/users")                                                      # forbidden
    app_client = a.application.test_client()
    app_client.post("/api/login", json={"email": ADMIN_EMAIL, "password": "badbadbad1A"})
    log = a.get("/api/admin/audit?limit=50").get_json()
    actions = {e["action"] for e in log}
    assert {"login", "forbidden", "login_failed", "user_created"} <= actions


def test_cannot_demote_or_disable_self_and_disabling_kills_sessions(two_clients):
    a, csrf_a, u, csrf_u = two_clients
    me = a.get("/api/me").get_json()["user"]["id"]
    assert a.put(f"/api/admin/users/{me}", json={"role": "user"}, headers={"X-CSRF-Token": csrf_a}).status_code == 400
    uid = u.get("/api/me").get_json()["user"]["id"]
    assert a.put(f"/api/admin/users/{uid}", json={"active": False}, headers={"X-CSRF-Token": csrf_a}).status_code == 200
    assert u.get("/api/me").status_code == 401


def test_cors_allows_only_configured_origin_with_credentials(client):
    r = client.options("/api/login", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
                                             "Access-Control-Request-Headers": "content-type,x-csrf-token"})
    assert r.headers.get("Access-Control-Allow-Origin") == "http://localhost:5173"
    assert r.headers.get("Access-Control-Allow-Credentials") == "true"
    r = client.options("/api/login", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
    assert "Access-Control-Allow-Origin" not in r.headers


def test_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY" and r.headers["Cache-Control"] == "no-store"


def test_sql_injection_attempt_is_inert(client):
    r = client.post("/api/login", json={"email": "' OR 1=1 --", "password": "x"})
    assert r.status_code == 401


# ---------------------------------------------------------------- data endpoints
def seed(app):
    with db_session() as c:
        ingest.ingest_gateway_heartbeat(c, gw_heartbeat(), ingest.utc_now())
        ingest.ingest_flow(c, flow_msg(node=1, seq=1, cum=450, interval=450), ingest.utc_now())
        ingest.ingest_flow(c, flow_msg(node=2, seq=1, cum=420, interval=420), ingest.utc_now())
        ingest.ingest_rain(c, rain_msg(seq=1, level=42.5), ingest.utc_now())
        ingest.ingest_rain(c, rain_msg(seq=2, level_valid=False), ingest.utc_now())


def test_data_endpoints_shape_and_values(app, client):
    seed(app); login(client)
    ov = client.get("/api/overview").get_json()
    assert ov["consumption"]["allTime"]["liters"] == pytest.approx(1.0) and ov["node1"]["flowRate"] > 0
    assert ov["gateway"]["online"] is True and ov["rainwater"]["levelValid"] is False and ov["rainwater"]["waterLevel"] is None
    ws = client.get("/api/water/summary").get_json()
    assert ws["authoritativeVolume"] == pytest.approx(1.0) and ws["node2TotalLiters"] == pytest.approx(420 / 450, abs=1e-3)
    assert client.get("/api/water/live").get_json()["node1"]["cumPulses"] == 450
    assert len(client.get("/api/water/history?node=1&limit=10").get_json()) == 1
    assert client.get("/api/water/consumption?period=day&count=3").status_code == 200
    assert client.get("/api/water/consumption?period=year").status_code == 400
    rs = client.get("/api/rainwater/summary").get_json()
    assert rs["ultrasonicValid"] is False and rs["tankFillPercentage"] is None        # timeout never shown as 0 %
    lv = client.get("/api/leaks/live").get_json()
    assert lv["state"] == "NOT_CONFIGURED" and lv["status"] == "unavailable" and "pipeline" in lv["reason"]      # no pipeline yet: never a false all-clear
    st = client.get("/api/system/status").get_json()
    assert st["gatewayActive"] is True and len(st["devices"]) == 3
    assert client.get("/api/devices").get_json()["devices"][0]["device"] == "node1"
    assert client.get("/api/leaks/events").status_code == 200 and client.get("/api/leaks/summary").status_code == 200
    assert client.get("/api/rainwater/events").status_code == 200 and client.get("/api/alerts?active=1").status_code == 200


def test_alert_acknowledge(app, client):
    seed(app)
    csrf = login(client)
    with db_session() as c:
        c.execute("INSERT INTO alerts (alert_key,kind,severity,message,active,created_at) VALUES ('X','x','info','m',1,'2026-01-01 00:00:00')")
    aid = client.get("/api/alerts").get_json()[0]["id"]
    assert client.post(f"/api/alerts/{aid}/ack").status_code == 403                          # CSRF
    assert client.post(f"/api/alerts/{aid}/ack", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/api/alerts").get_json()[0]["acknowledgedBy"] == ADMIN_EMAIL
