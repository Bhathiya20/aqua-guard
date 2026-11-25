"""The calibrated pulses-per-litre is the one source of truth.

The NODE converts with its own factor (it reports flowRate and pulsesPerLiter; the calibrate command pushes the factor to it, signed, through the gateway).
The backend stores the raw pulse counts plus the factor each reading was taken with, and derives every volume as delta_pulses / that factor - it never
multiplies by a second factor, so there is no double conversion.  These tests pin that down on every page that shows a volume.
"""
import time

import pytest

import analysis_service
import calibration_api
import database
from tests.conftest import ADMIN_EMAIL, USER_EMAIL, USER_PW, login
from tests.v2helpers import T0, deliver, dt, fmt_mac, registry

MAC = "447b30854b88"
T = int(time.time()) - 120


def msg(seq, cum, interval, ppl, t, flow=None):
    """One telemetry message as the node sends it: `ppl` is the factor the node itself converts with."""
    return t, {"schema": 3, "node": 1, "type": "flow", "mac": fmt_mac(MAC), "boot": 7, "seq": seq, "recovered": False, "backlog": False,
               "flowRate": flow if flow is not None else round(interval / ppl * 30.0, 3), "totalLiters": round(cum / ppl, 3), "intervalLiters": round(interval / ppl, 3),
               "intervalPulses": interval, "cumPulses": cum, "pulsesPerLiter": ppl, "intervalMs": 2000, "batteryMv": 7400, "uptime": seq * 2, "status": 0, "channel": 9, "rssi": -55}


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def admin(client):
    h = {"X-CSRF-Token": login(client)}
    return lambda m, url, **kw: getattr(client, m)(url, headers=h, **kw)


@pytest.fixture()
def node(app):
    registry([("flow", 1, MAC, "Main", "paired")], now=dt(T - 100))


def send(*rows):
    for t, m in rows:
        deliver(m, t)


def node_json(admin):
    return admin("get", "/api/nodes").get_json()["nodes"][0]


def test_480_pulses_is_one_litre_everywhere_after_calibration(admin, node):
    send(msg(1, 0, 0, 480.0, T), msg(2, 480, 480, 480.0, T + 2))              # a baseline reading, then exactly 480 pulses at 480 p/L
    n = node_json(admin)
    assert n["live"]["weekLiters"] == 1.0 and n["live"]["todayLiters"] in (0.0, 1.0)      # Water Monitoring / Nodes (today = 0 only if the test runs across UTC midnight)
    assert n["live"]["samplePpl"] == 480.0
    pts = admin("get", f"/api/nodes/{n['id']}/usage?bucket=hour&days=1").get_json()["usage"]
    assert round(sum(p["liters"] for p in pts), 2) == 1.0                              # Analytics (and its CSV, which is built from these rows)
    with database.db_session() as c:
        s = analysis_service.load_samples(c, n["id"], T - 10, T + 10)
        assert round(sum(x.liters for x in s), 2) == 1.0                               # the leak balance (Q_in / Q_out / D) reads the same samples
    tot = admin("get", "/api/summary").get_json()["total"]
    assert round(tot["weekLiters"], 2) == 1.0                                         # Overview


def test_default_450_when_not_calibrated(admin, node):
    send(msg(1, 0, 0, 450.0, T), msg(2, 450, 450, 450.0, T + 2))
    n = node_json(admin)
    assert n["live"]["weekLiters"] == 1.0 and n["live"]["samplePpl"] == 450.0
    assert n["calibration"]["calibrated"] is False


def test_no_double_conversion_the_node_flow_rate_is_stored_as_sent(admin, node):
    send(msg(1, 0, 0, 480.0, T), msg(2, 480, 480, 480.0, T + 2, flow=30.0))
    with database.db_session() as c:
        r = c.execute("SELECT flow_lpm, delta_pulses, pulses_per_liter_x10 FROM flow_samples ORDER BY id DESC LIMIT 1").fetchone()
    assert r["flow_lpm"] == 30.0 and r["delta_pulses"] == 480 and r["pulses_per_liter_x10"] == 4800
    assert node_json(admin)["live"]["lastFlowLpm"] == 30.0                              # shown as the node computed it


def test_calibration_state_is_confirmed_by_the_nodes_own_readings(admin, node):
    n = node_json(admin)
    with database.db_session() as c:
        calibration_api.record_history(c, n["id"], ADMIN_EMAIL, 4500, 4800, "manual", None, now=dt(T - 30))
    send(msg(1, 0, 0, 450.0, T), msg(2, 450, 450, 450.0, T + 2))                   # the node still reports 450: not applied yet
    cal = node_json(admin)["calibration"]
    assert cal["calibrated"] and cal["ppl"] == 480.0 and cal["applied"] is False and cal["earlierUsesOld"] is True
    send(msg(3, 930, 480, 480.0, T + 4))                                               # the node now reports 480: confirmed
    assert node_json(admin)["calibration"]["applied"] is True


def test_recalculate_history_is_admin_only_backed_up_and_keeps_raw_pulses(app, admin, node, cfg):
    send(msg(1, 0, 0, 450.0, T), msg(2, 450, 450, 450.0, T + 2, flow=30.0))
    n = node_json(admin)
    with database.db_session() as c:
        calibration_api.record_history(c, n["id"], ADMIN_EMAIL, 4500, 4800, "manual", None, now=dt(T - 30))
    assert n["live"]["weekLiters"] == 1.0                                              # the history still uses 450 p/L
    admin("post", "/api/admin/users", json={"email": USER_EMAIL, "password": USER_PW, "role": "user", "firstName": "Una", "lastName": "User"})
    with database.db_session() as c:
        c.execute("UPDATE users SET must_change_password=0 WHERE lower(email)=lower(?)", (USER_EMAIL,))
    c2 = app.test_client()
    h2 = {"X-CSRF-Token": login(c2, USER_EMAIL, USER_PW)}
    assert c2.post(f"/api/nodes/{n['id']}/recalculate-history", headers=h2, json={}).status_code == 403
    with database.db_session() as c:
        pulses = c.execute("SELECT SUM(delta_pulses), SUM(cum_pulses) FROM flow_samples").fetchone()
    r = admin("post", f"/api/nodes/{n['id']}/recalculate-history", json={})
    assert r.status_code == 200 and r.get_json()["ppl"] == 480.0 and r.get_json()["samples"] == 2
    assert list((cfg.db_path.parent / "backups").glob("aquaguard-before-recalc-*.db"))     # backup first
    n2 = node_json(admin)
    assert n2["live"]["weekLiters"] == round(450 / 480, 2)                    # 450 pulses at 480 p/L
    assert n2["calibration"]["earlierUsesOld"] is False
    with database.db_session() as c:
        assert tuple(c.execute("SELECT SUM(delta_pulses), SUM(cum_pulses) FROM flow_samples").fetchone()) == tuple(pulses)       # raw pulses untouched
        assert c.execute("SELECT flow_lpm FROM flow_samples ORDER BY id DESC LIMIT 1").fetchone()[0] == pytest.approx(30.0 * 450 / 480)
        assert c.execute("SELECT COUNT(*) FROM audit_log WHERE action='calibration_recalculate'").fetchone()[0] == 1
    assert admin("post", f"/api/nodes/{n['id']}/recalculate-history", json={"ppl": 5}).status_code == 400
