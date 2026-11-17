"""Demo "Reset usage": non-destructive baseline for the usage counters (today / 7 days / running time), undo, auto-expiry, audit, admin only."""
import time

import pytest

import database
import usage_reset
import usage_service
from tests.conftest import ADMIN_EMAIL, USER_EMAIL, USER_PW, login
from tests.v2helpers import NodeSim, dt, feed, registry

MAC = "447b30854b88"
ADMIN_USER = {"id": None, "email": ADMIN_EMAIL}
T = int(time.time())
RESET_AT = T - 300                       # the reset happened 5 minutes ago (so "now" for the HTTP API, the real clock, is after it)


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def admin(client):
    h = {"X-CSRF-Token": login(client)}
    return lambda m, url, **kw: getattr(client, m)(url, headers=h, **kw)


@pytest.fixture()
def node(app):
    registry([("flow", 1, MAC, "Main", "paired")], now=dt(T - 1000))
    return NodeSim(1, MAC, t_boot=T - 1000)


def live(client):
    login(client)
    n = client.get("/api/nodes").get_json()["nodes"][0]["live"]
    return n["todayLiters"], n["weekLiters"]


def counts():
    with database.db_session() as c:
        return (c.execute("SELECT COUNT(*) FROM flow_samples").fetchone()[0], c.execute("SELECT COUNT(*) FROM alerts").fetchone()[0],
                c.execute("SELECT COALESCE(SUM(delta_pulses),0) FROM flow_samples").fetchone()[0])


def audit_actions():
    with database.db_session() as c:
        return [r["action"] for r in c.execute("SELECT action FROM audit_log ORDER BY id").fetchall()]


def do_reset(clear=False, at=RESET_AT):
    with database.db_session() as c:
        return usage_reset.reset(c, ADMIN_USER, at, clear)


def test_reset_starts_at_zero_counts_up_and_undo_restores(client, node):
    feed(node, T - 900, T - 300, 6.0)                                  # 600 s x 6 L/min = ~60 L before the reset
    today0, week0 = live(client)
    assert week0 > 50
    before = counts()
    do_reset()
    assert live(client) == (0, 0)                                      # nothing since the reset yet
    assert counts() == before                                          # non-destructive: no sample, alert or pulse changed
    feed(node, T - 300, T - 100, 6.0)                                  # 200 s later: ~20 L
    today1, week1 = live(client)
    assert 15 < week1 < 25 and 0 < today1 <= week1                    # counts up from the reset only
    assert counts()[0] > before[0]
    with database.db_session() as c:
        usage_reset.undo(c, ADMIN_USER, T)
    today2, week2 = live(client)
    assert week2 > week0 + 15                                          # undo: the real totals (old + new samples) are back
    assert counts()[2] >= before[2]


def test_other_usage_endpoints_follow_the_baseline(client, node):
    login(client)
    feed(node, T - 900, T - 300, 6.0)
    do_reset()
    s = client.get("/api/summary").get_json()["total"]
    assert s["todayLiters"] == 0 and s["weekLiters"] == 0


def test_running_time_counts_from_the_baseline(app, node):
    with database.db_session() as c:
        c.execute("INSERT INTO usage_state (node_ref,flowing,flow_since,last_flow_at,processed_until,peak_lpm,last_lpm,status,updated_at) VALUES (1,1,?,?,?,5,5,'PROLONGED_USAGE','x')",
                  (T - 1000, T, T))
        before = usage_service.live_all(c, dt(T))[0]["tFlowS"]
        usage_reset.reset(c, ADMIN_USER, T - 300)
        after = usage_service.live_all(c, dt(T))[0]["tFlowS"]
        assert 990 <= before <= 1010 and 290 <= after <= 310
        usage_reset.undo(c, ADMIN_USER, T)
        assert usage_service.live_all(c, dt(T))[0]["tFlowS"] >= 990


def test_clear_option_resolves_usage_alerts_with_reason_test_and_restarts_timers(app, node):
    with database.db_session() as c:
        c.execute("INSERT INTO alerts (alert_key,kind,severity,message,active,created_at,node_ref,state) VALUES ('USAGE:1','usage_prolonged','warning','x',1,'2026-01-01 00:00:00',1,'suspected')")
        c.execute("INSERT INTO alerts (alert_key,kind,severity,message,active,created_at,state) VALUES ('LEAK:z','LEAK','critical','leak',1,'2026-01-01 00:00:00','confirmed')")
        c.execute("INSERT INTO usage_state (node_ref,flowing,flow_since,last_flow_at,processed_until,peak_lpm,last_lpm,status,updated_at) VALUES (1,1,?,?,?,5,5,'PROLONGED_USAGE','x')", (T - 1000, T, T))
        usage_reset.reset(c, ADMIN_USER, T - 300, clear=False)
        assert c.execute("SELECT COUNT(*) FROM alerts WHERE active=1").fetchone()[0] == 2        # without the checkbox nothing is cleared
        usage_reset.reset(c, ADMIN_USER, T - 300, clear=True)
        u = c.execute("SELECT * FROM alerts WHERE alert_key='USAGE:1'").fetchone()
        assert u["active"] == 0 and u["resolve_reason"] == "test" and u["resolved_by"] == ADMIN_EMAIL
        assert c.execute("SELECT active FROM alerts WHERE alert_key='LEAK:z'").fetchone()[0] == 1   # leak alerts are never touched
        assert c.execute("SELECT flow_since FROM usage_state WHERE node_ref=1").fetchone()[0] == T - 300
        assert c.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 2                         # nothing deleted


def test_expires_after_24h_unless_extended(app):
    with database.db_session() as c:
        usage_reset.reset(c, ADMIN_USER, 1000)
        assert usage_reset.baseline(c, 1000 + 3600) == 1000
        assert usage_reset.baseline(c, 1000 + usage_reset.MAX_S) is None            # expired
        usage_reset.extend(c, ADMIN_USER, 1000 + 20 * 3600)
        assert usage_reset.baseline(c, 1000 + 40 * 3600) == 1000                   # extended by 24 h from the click
        assert usage_reset.expire(c, 1000 + 40 * 3600) is False
        assert usage_reset.expire(c, 1000 + 50 * 3600) is True
        assert usage_reset.state_json(c, 1000 + 50 * 3600)["active"] is False
        assert usage_reset.extend(c, ADMIN_USER, 1000 + 50 * 3600) is None
    acts = audit_actions()
    assert acts.count("usage_reset") == 1 and "usage_reset_extend" in acts and "usage_reset_undo" in acts


def test_audit_entries_for_reset_and_undo_via_api(admin, node):
    assert admin("post", "/api/usage-reset", json={"clear": False}).get_json()["active"] is True
    assert admin("get", "/api/usage-reset").get_json()["by"] == ADMIN_EMAIL
    assert admin("post", "/api/usage-reset/extend").status_code == 200
    assert admin("delete", "/api/usage-reset").get_json()["active"] is False
    assert admin("post", "/api/usage-reset/extend").status_code == 409
    acts = audit_actions()
    assert [a for a in acts if a.startswith("usage_reset")] == ["usage_reset", "usage_reset_extend", "usage_reset_undo"]


def test_non_admin_cannot_reset_or_undo(app, admin):
    admin("post", "/api/admin/users", json={"email": USER_EMAIL, "password": USER_PW, "role": "user", "firstName": "Una", "lastName": "User"})
    with database.db_session() as c:
        c.execute("UPDATE users SET must_change_password=0 WHERE lower(email)=lower(?)", (USER_EMAIL,))
    c2 = app.test_client()
    h = {"X-CSRF-Token": login(c2, USER_EMAIL, USER_PW)}
    assert c2.post("/api/usage-reset", headers=h, json={}).status_code == 403
    assert c2.delete("/api/usage-reset", headers=h).status_code == 403
    assert c2.post("/api/usage-reset/extend", headers=h).status_code == 403
    assert c2.get("/api/usage-reset").get_json()["active"] is False                # reading the state (for the note) is allowed
