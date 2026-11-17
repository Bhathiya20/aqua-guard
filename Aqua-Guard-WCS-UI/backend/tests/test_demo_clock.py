"""Demo clock (Auto / Force DAY / Force NIGHT) and the unified time model (quiet hours shared by node profiles and zones, per-zone overrides)."""
import json

import pytest

import analysis_service
import clock_service
import database
import usage_service
from analysis import clock as C
from analysis.rules import ZoneParams
from tests import netdocs as nd
from tests.conftest import ADMIN_EMAIL, USER_EMAIL, USER_PW, login
from tests.v2helpers import NodeSim, T0, dt, feed, registry

MAC_1, MAC_2 = "447b30854b88", "aabbcc000002"
MIN = 60
NIGHT22 = T0 + 10 * 3600          # 2026-01-01 22:00:00 UTC (the test site offset is 0): the first minute of the default quiet hours 22:00-05:00
ADMIN_USER = {"id": None, "email": ADMIN_EMAIL}


def P(**kw):
    return ZoneParams(**{"tz_offset_min": 0, "suspect_min": 5, "confirm_min": 10, "resolve_min": 5, **kw})


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def admin(client):
    h = {"X-CSRF-Token": login(client)}
    return lambda m, url, **kw: getattr(client, m)(url, headers=h, **kw)


@pytest.fixture()
def two_nodes(app):
    registry([("flow", 1, MAC_1, "Main", "paired"), ("flow", 2, MAC_2, "Garden", "paired")])


def make_pipeline(admin, d=None):
    r = admin("post", "/api/pipelines", json={"name": "House", "doc": (d or nd.straight()).doc()})
    assert r.status_code == 201 and r.get_json()["valid"], r.get_json()
    return r.get_json()["id"]


def run(cfg, now_s, base=None):
    with database.db_session() as c:
        return analysis_service.run_analysis(c, cfg, dt(now_s), base=base or P())


def zone(res, zid="zone:flow2"):
    return next(z for z in res[0]["zones"] if z["id"] == zid)


def leak_alerts():
    with database.db_session() as c:
        return [dict(r) for r in c.execute("SELECT * FROM alerts WHERE kind='LEAK' ORDER BY id").fetchall()]


def set_clock(mode, now_s):
    with database.db_session() as c:
        return clock_service.set_clock(c, mode, ADMIN_USER, now_s)


def night_rule(z):
    return next(r for r in z["rules"] if r["rule"] == "night_flow")


def samples_snapshot():
    with database.db_session() as c:
        return [tuple(r) for r in c.execute("SELECT id, node_ref, measured_at, received_at, delta_pulses, cum_pulses FROM flow_samples ORDER BY id").fetchall()]


# --------------------------------------------------------------------------- the switch itself
def test_clock_api_admin_only_audited_and_validated(app, client, admin):
    assert client.get("/api/demo-clock").get_json()["mode"] == "auto"
    assert admin("put", "/api/demo-clock", json={"mode": "dusk"}).status_code == 400
    r = admin("put", "/api/demo-clock", json={"mode": "night"})
    j = r.get_json()
    assert r.status_code == 200 and j["mode"] == "night" and j["forced"] is True and j["by"] == ADMIN_EMAIL
    assert 3590 <= j["until"] - j["since"] <= 3600
    assert client.get("/api/demo-clock").get_json()["mode"] == "night"
    assert admin("put", "/api/demo-clock", json={"mode": "auto"}).get_json()["mode"] == "auto"
    with database.db_session() as c:
        rows = c.execute("SELECT email, detail FROM audit_log WHERE action='demo_clock' ORDER BY id").fetchall()
    assert [json.loads(x["detail"])["mode"] for x in rows] == ["night", "auto"] and rows[0]["email"] == ADMIN_EMAIL

    c2 = app.test_client()                                                      # a normal user may read the clock, never switch it
    admin("post", "/api/admin/users", json={"email": USER_EMAIL, "password": USER_PW, "role": "user", "firstName": "Una", "lastName": "User"})
    with database.db_session() as c:
        c.execute("UPDATE users SET must_change_password=0 WHERE lower(email)=lower(?)", (USER_EMAIL,))
    csrf = login(c2, USER_EMAIL, USER_PW)
    assert c2.get("/api/demo-clock").status_code == 200
    assert c2.put("/api/demo-clock", json={"mode": "night"}, headers={"X-CSRF-Token": csrf}).status_code == 403


def test_clock_switches_itself_off_after_one_hour_and_says_so(app):
    set_clock("night", T0)
    with database.db_session() as c:
        assert clock_service.load_clock(c, T0 + 3599).night
        assert not clock_service.load_clock(c, T0 + 3601).forced                # expired = the real clock, even before anything cleans up
        assert clock_service.expire_clock(c, T0 + 3601) is True
        assert clock_service.load_clock(c, T0 + 3602) is C.REAL
        assert clock_service.expire_clock(c, T0 + 3602) is False
        last = c.execute("SELECT email, detail FROM audit_log WHERE action='demo_clock' ORDER BY id DESC LIMIT 1").fetchone()
    assert last["email"] == "system" and "automatically" in last["detail"]


# --------------------------------------------------------------------------- weak zone: night-flow rule under the clock
_SIMS: dict = {}


def feed_both(t0, secs, rate=1.0):
    """Both nodes deliver `secs` seconds of `rate` L/min from t0.  The simulators persist within a test, so sequence numbers keep rising."""
    if not _SIMS:
        _SIMS.update(a=NodeSim(1, MAC_1, step=5, t_boot=t0 - 10), b=NodeSim(2, MAC_2, step=5, t_boot=t0 - 10))
    feed(_SIMS["a"], t0, t0 + secs, rate); feed(_SIMS["b"], t0, t0 + secs, rate)


@pytest.fixture(autouse=True)
def _fresh_sims():
    _SIMS.clear()
    yield
    _SIMS.clear()


def test_forced_night_by_day_raises_a_demo_night_flow_alert_after_the_confirm_time(cfg, admin, two_nodes):
    make_pipeline(admin)
    feed_both(T0, 8 * MIN)                                                      # 12:00 site time: the real clock says DAY
    z = zone(run(cfg, T0 + 8 * MIN))
    assert z["class"] == "weak" and z["status"] == "normal" and leak_alerts() == []          # without the clock a steady 1 L/min by day is nothing
    set_clock("night", T0 + 8 * MIN)
    feed_both(T0 + 8 * MIN, 2 * MIN)
    z = zone(run(cfg, T0 + 10 * MIN + 30))                                      # about 1 complete minute of simulated night: suspected
    assert z["status"] == "suspected" and night_rule(z)["level"] == 1
    a = leak_alerts()
    assert len(a) == 1 and a[0]["demo"] == 1 and a[0]["message"].startswith("[demo]") and a[0]["state"] == "suspected"
    feed_both(T0 + 10 * MIN, 4 * MIN)
    z = zone(run(cfg, T0 + 14 * MIN + 30))                                      # 3+ complete minutes above the threshold: confirmed (the confirm time)
    assert z["status"] == "confirmed" and leak_alerts()[0]["state"] == "confirmed" and len(leak_alerts()) == 1


def test_forced_day_by_night_raises_no_night_alert_but_the_real_night_does(cfg, admin, two_nodes):
    make_pipeline(admin)
    feed_both(NIGHT22, 30 * MIN)                                                # a 1 L/min floor from 22:00 on
    z = zone(run(cfg, NIGHT22 + 30 * MIN))
    assert night_rule(z)["level"] == 1 and z["status"] == "suspected"           # control: the real quiet hours (22:00-05:00, inherited from the usage defaults) catch it
    with database.db_session() as c:
        c.execute("DELETE FROM alerts"); c.execute("DELETE FROM zone_state")
    set_clock("day", NIGHT22 + 30 * MIN)
    z = zone(run(cfg, NIGHT22 + 30 * MIN + 5))
    r = night_rule(z)
    assert r["level"] == 0 and "DAY" in r["progress"]["text"] and z["status"] != "suspected" and leak_alerts() == []


def test_clock_never_touches_stored_timestamps_or_readings(cfg, admin, two_nodes):
    make_pipeline(admin)
    feed_both(T0, 6 * MIN)
    before = samples_snapshot()
    assert before
    set_clock("night", T0 + 6 * MIN)
    run(cfg, T0 + 6 * MIN + 30)
    set_clock("day", T0 + 7 * MIN)
    run(cfg, T0 + 7 * MIN + 30)
    set_clock("auto", T0 + 8 * MIN)
    run(cfg, T0 + 8 * MIN + 30)
    assert samples_snapshot() == before


def test_demo_alerts_are_closed_when_the_clock_expires(cfg, admin, two_nodes):
    make_pipeline(admin)
    set_clock("night", T0)
    feed_both(T0, 8 * MIN)
    run(cfg, T0 + 8 * MIN)
    a = leak_alerts()
    assert len(a) == 1 and a[0]["demo"] == 1 and a[0]["active"] == 1
    run(cfg, T0 + 3601)                                                         # one hour later: the clock is off, its alert is closed with the reason
    a = leak_alerts()
    assert a[0]["active"] == 0 and "demo mode ended" in a[0]["message"]
    with database.db_session() as c:
        assert not clock_service.load_clock(c, T0 + 3602).forced


# --------------------------------------------------------------------------- node usage profiles under the clock
MAC = "447b30854b88"


@pytest.fixture()
def one_node(app):
    registry([("flow", 1, MAC, "Washroom tap", "paired")])


def usage_alerts():
    with database.db_session() as c:
        return [dict(r) for r in c.execute("SELECT * FROM alerts WHERE alert_key LIKE 'USAGE:%' ORDER BY id").fetchall()]


def flow_ticks(sim, t0, secs, rate=2.0, every=10):
    t = t0
    while t < t0 + secs:
        n = min(every, t0 + secs - t)
        feed(sim, t, t + n, rate)
        t += n
        with database.db_session() as c:
            usage_service.run_usage(c, dt(t))
    return t


def test_node_night_flow_by_day_when_the_clock_says_night(app, one_node):
    day = T0                                                                    # 12:00 UTC; site time 17:30: day
    set_clock("night", day)
    sim = NodeSim(1, MAC, step=2, t_boot=day - 10)
    t = flow_ticks(sim, day, 40)
    assert usage_alerts() == []                                                 # 40 s < the 60 s night confirm time
    flow_ticks(sim, t, 40)
    a = usage_alerts()
    assert len(a) == 1 and a[0]["kind"] == "night_flow" and a[0]["demo"] == 1 and a[0]["message"].startswith("[demo]")


def test_node_no_night_alert_when_the_clock_says_day_at_night(app, one_node):
    night = T0 + int(5.5 * 3600)                                                # 17:30 UTC = 23:00 site time (+05:30): inside the quiet hours
    set_clock("day", night)
    sim = NodeSim(1, MAC, step=2, t_boot=night - 10)
    flow_ticks(sim, night, 90)
    assert usage_alerts() == []
    set_clock("auto", night + 90)                                               # back to the real clock: the same flow is a night flow now
    flow_ticks(sim, night + 90, 90)
    assert [x["kind"] for x in usage_alerts()] == ["night_flow"] and usage_alerts()[0]["demo"] == 0


def test_schedule_follows_the_forced_state(app, one_node):
    from analysis import usage as U
    p = dict(U.FACTORY, schedule=[{"days": [0, 1, 2, 3, 4], "start": "08:00", "end": "17:00"}])
    t_sat = T0 + 2 * 86400                                                      # 2026-01-03 is a Saturday 12:00 UTC: outside the weekday schedule
    assert U.schedule_state(t_sat, p, 0)[0] is False
    day, night = C.Clock("day", t_sat - 5, t_sat + 3600), C.Clock("night", t_sat - 5, t_sat + 3600)
    assert U.schedule_state(t_sat, p, 0, day)[0] is True                        # forced DAY: inside the allowed lab hours
    inside, last_end = U.schedule_state(t_sat, p, 0, night)
    assert inside is False and last_end == t_sat - 5                            # forced NIGHT: outside, and the allowed window "ended" when the clock was switched
    assert U.quiet_window(t_sat, p, 0, night) == (True, t_sat - 5) and U.quiet_window(t_sat, p, 0, day) == (False, None)


# --------------------------------------------------------------------------- unified time model
def test_zone_inherits_the_global_quiet_hours_and_can_override_them(cfg, app, client, admin, two_nodes):
    pid = make_pipeline(admin)
    url = f"/api/pipelines/{pid}/zones/zone:flow2/time-settings"
    g = client.get(url).get_json()
    assert g["quietStart"] == "22:00" and g["quietEnd"] == "05:00" and g["overridden"] is False
    assert set(g["source"].values()) == {"default"} and g["continuousSuspectH"] == 6 and g["continuousConfirmH"] == 12
    z = zone(run(cfg, T0))
    assert z["settings"]["nightStart"] == "22:00" and z["settings"]["nightEnd"] == "05:00"                  # the same hours as the node usage profiles
    assert z["timeRules"]["source"]["quietStart"] == "default"

    assert admin("put", url, json={"quietStart": "23:30"}).status_code == 400                                 # camelCase is not the API: unknown keys change nothing
    r = admin("put", url, json={"quiet_start": "23:30", "continuous_suspect_h": 2, "continuous_confirm_h": 4})
    j = r.get_json()
    assert r.status_code == 200 and j["quietStart"] == "23:30" and j["source"]["quietStart"] == "zone" and j["source"]["quietEnd"] == "default" and j["overridden"] is True
    z = zone(run(cfg, T0))
    assert z["settings"]["nightStart"] == "23:30" and z["settings"]["nightEnd"] == "05:00" and z["settings"]["continuousSuspectMin"] == 120 and z["settings"]["continuousConfirmMin"] == 240
    assert zone(run(cfg, T0), "zone:flow1")["timeRules"]["overridden"] is False                              # other zones keep inheriting

    assert admin("put", url, json={"quiet_start": "7:99"}).status_code == 400
    assert admin("put", url, json={"continuous_suspect_h": 5, "continuous_confirm_h": 3}).status_code == 400   # suspected must stay shorter than confirmed
    assert admin("put", url, json={"quiet_start": "05:00", "quiet_end": "05:00"}).status_code == 400
    assert admin("put", f"/api/pipelines/{pid}/zones/zone:nope/time-settings", json={"quiet_start": "23:00"}).status_code == 404

    j = admin("delete", url).get_json()                                                                       # Reset to defaults
    assert j["overridden"] is False and j["quietStart"] == "22:00"
    assert zone(run(cfg, T0))["settings"]["nightStart"] == "22:00"
    with database.db_session() as c:
        acts = [r["action"] for r in c.execute("SELECT action FROM audit_log WHERE action LIKE 'zone_time%' ORDER BY id").fetchall()]
    assert acts == ["zone_time_set", "zone_time_reset"]


def test_changing_the_global_quiet_hours_moves_every_inheriting_zone(cfg, admin, two_nodes):
    pid = make_pipeline(admin)
    assert admin("put", "/api/usage/defaults", json={"quietStart": "21:00", "quietEnd": "06:00"}).status_code == 200
    z = zone(run(cfg, T0))
    assert z["settings"]["nightStart"] == "21:00" and z["settings"]["nightEnd"] == "06:00" and z["timeRules"]["source"]["quietStart"] == "default"
    assert admin("put", f"/api/pipelines/{pid}/zones/zone:flow2/time-settings", json={"quiet_end": "04:00"}).status_code == 200
    z = zone(run(cfg, T0))
    assert z["settings"]["nightStart"] == "21:00" and z["settings"]["nightEnd"] == "04:00"                    # the override wins only for the value it names


def test_night_window_crosses_midnight():
    from analysis.rules import _night_window
    p = ZoneParams(tz_offset_min=0, night_start_min=22 * 60, night_end_min=5 * 60)
    day = 1_767_225_600                                                        # 2026-01-01 00:00 UTC
    assert _night_window(day + 23 * 3600, p) == (day + 22 * 3600, day + 29 * 3600)       # 23:00: tonight's window
    assert _night_window(day + 3 * 3600, p) == (day - 2 * 3600, day + 5 * 3600)          # 03:00: still last evening's window
    assert _night_window(day + 12 * 3600, p) == (day - 2 * 3600, day + 5 * 3600)         # noon: the most recent one (finished)
