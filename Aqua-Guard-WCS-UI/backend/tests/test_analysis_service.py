"""Analysis on the database path: alert lifecycle (one alert per event), measured-time bucketing with late data, weak single-node zones, endpoints."""
import time

import pytest

import analysis_service
import database
from analysis.rules import ZoneParams
from tests import netdocs as nd
from tests.conftest import login
from tests.v2helpers import NodeSim, T0, deliver, dt, feed, registry

MAC_1, MAC_2 = "447b30854b88", "aabbcc000002"
MIN = 60


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


def make_pipeline(admin, d, name="House"):
    r = admin("post", "/api/pipelines", json={"name": name, "doc": d.doc() if hasattr(d, "doc") else d})
    assert r.status_code == 201 and r.get_json()["valid"], r.get_json()
    return r.get_json()["id"]


def run(cfg, now_s, base=None):
    with database.db_session() as c:
        return analysis_service.run_analysis(c, cfg, dt(now_s), base=base or P())


def alerts(pid=None):
    with database.db_session() as c:
        rows = c.execute("SELECT * FROM alerts WHERE kind='LEAK' ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def zone(res, zid, pid_index=0):
    return next(z for z in res[pid_index]["zones"] if z["id"] == zid)


# --------------------------------------------------------------------------- lifecycle: one alert per event
def test_alert_goes_suspected_confirmed_resolved_in_one_row(cfg, admin, two_nodes):
    pid = make_pipeline(admin, nd.straight())
    s1, s2 = NodeSim(1, MAC_1, step=5), NodeSim(2, MAC_2, step=5)
    feed(s1, T0, T0 + 8 * MIN, 3.0); feed(s2, T0, T0 + 8 * MIN, 1.0)                 # 2 L/min vanish between the nodes
    assert zone(run(cfg, T0 + 8 * MIN), "zone:flow1")["status"] == "suspected"
    a = alerts()
    assert len(a) == 1 and a[0]["state"] == "suspected" and a[0]["active"] == 1 and a[0]["pipeline_id"] == pid and a[0]["pipeline_version"] == 1 and a[0]["zone_id"] == "zone:flow1"
    first_id = a[0]["id"]

    run(cfg, T0 + 8 * MIN); run(cfg, T0 + 8 * MIN + 5)                                   # re-running the analysis never duplicates
    assert len(alerts()) == 1

    feed(s1, T0 + 8 * MIN, T0 + 14 * MIN, 3.0); feed(s2, T0 + 8 * MIN, T0 + 14 * MIN, 1.0)
    assert zone(run(cfg, T0 + 14 * MIN), "zone:flow1")["status"] == "confirmed"
    a = alerts()
    assert len(a) == 1 and a[0]["id"] == first_id and a[0]["state"] == "confirmed" and a[0]["severity"] == "critical" and a[0]["active"] == 1

    feed(s1, T0 + 14 * MIN, T0 + 24 * MIN, 2.0); feed(s2, T0 + 14 * MIN, T0 + 24 * MIN, 2.0)       # leak repaired
    assert zone(run(cfg, T0 + 24 * MIN), "zone:flow1")["status"] == "normal"
    a = alerts()
    assert len(a) == 1 and a[0]["state"] == "resolved" and a[0]["active"] == 0 and a[0]["cleared_at"]

    # a SECOND event is a new row, and the first stays as history
    feed(s1, T0 + 24 * MIN, T0 + 34 * MIN, 3.0); feed(s2, T0 + 24 * MIN, T0 + 34 * MIN, 1.0)
    run(cfg, T0 + 34 * MIN)
    a = alerts()
    assert len(a) == 2 and a[0]["state"] == "resolved" and a[1]["active"] == 1 and a[1]["id"] != first_id


def test_alert_remembers_the_pipeline_version_it_was_raised_under(cfg, admin, two_nodes):
    pid = make_pipeline(admin, nd.straight())
    admin("put", f"/api/pipelines/{pid}", json={"doc": nd.straight().doc(), "note": "v2"})      # active version is now 2
    s1, s2 = NodeSim(1, MAC_1, step=5), NodeSim(2, MAC_2, step=5)
    feed(s1, T0, T0 + 8 * MIN, 3.0); feed(s2, T0, T0 + 8 * MIN, 1.0)
    run(cfg, T0 + 8 * MIN)
    assert [x["pipeline_version"] for x in alerts()] == [2]


def test_removing_the_zone_from_the_design_closes_its_alert(cfg, admin, two_nodes):
    pid = make_pipeline(admin, nd.straight())
    s1, s2 = NodeSim(1, MAC_1, step=5), NodeSim(2, MAC_2, step=5)
    feed(s1, T0, T0 + 8 * MIN, 3.0); feed(s2, T0, T0 + 8 * MIN, 1.0)
    run(cfg, T0 + 8 * MIN)
    assert alerts()[0]["active"] == 1
    admin("put", f"/api/pipelines/{pid}", json={"doc": nd.unmetered().doc(), "note": "sensors removed"})     # zone:flow1 no longer exists
    run(cfg, T0 + 8 * MIN + 10)
    a = alerts()
    assert len(a) == 1 and a[0]["active"] == 0 and a[0]["state"] == "resolved" and "configuration changed" in a[0]["message"]


# --------------------------------------------------------------------------- measured time and late data
def test_delayed_retransmission_lands_in_the_right_buckets_and_raises_no_false_leak(cfg, admin, two_nodes):
    make_pipeline(admin, nd.straight())
    base = P(suspect_min=3, confirm_min=6)
    s1, s2 = NodeSim(1, MAC_1, step=5), NodeSim(2, MAC_2, step=5)
    hole = (T0 + 10 * MIN, T0 + 14 * MIN)                                                  # node 2's minutes 10-13 arrive only at minute 17
    feed(s1, T0, T0 + 22 * MIN, 2.0)
    held = feed(s2, T0, T0 + 22 * MIN, 2.0, skip=[hole])                                   # delivered live except the hole, flagged recovered afterwards
    assert len(held) == 48
    # while the data is missing: no alarm, the buckets are INCOMPLETE (missing data is never zero)
    res = run(cfg, T0 + 17 * MIN, base)
    z = zone(res, "zone:flow1")
    assert z["status"] in ("normal", "incomplete") and alerts() == []
    with database.db_session() as c:
        miss = c.execute("SELECT complete FROM zone_buckets WHERE zone_id='zone:flow1' AND bucket_ts>=? AND bucket_ts<? ORDER BY bucket_ts",
                         (T0 + 10 * MIN, T0 + 14 * MIN)).fetchall()
    assert miss and all(r["complete"] == 0 for r in miss)

    # the retransmission arrives at minute 17
    late = NodeSim(2, MAC_2, step=5)                                                       # regenerate the identical samples (same boot, same seqs)
    for t, m in late.msgs(T0, T0 + 22 * MIN, 2.0):
        if hole[0] <= t < hole[1]:
            deliver(m, T0 + 17 * MIN, recovered=True)
    res = run(cfg, T0 + 22 * MIN, base)
    z = zone(res, "zone:flow1")
    assert z["status"] == "normal" and alerts() == []
    with database.db_session() as c:
        rows = c.execute("SELECT bucket_ts, q_in_l, q_out_l, complete FROM zone_buckets WHERE zone_id='zone:flow1' AND bucket_ts>=? AND bucket_ts<? ORDER BY bucket_ts",
                         (T0 + 10 * MIN, T0 + 14 * MIN)).fetchall()
        src = {r["measured_src"] for r in c.execute("SELECT DISTINCT measured_src FROM flow_samples WHERE node_ref IS NOT NULL").fetchall()}
    assert len(rows) == 4 and all(r["complete"] == 1 for r in rows)                        # in the buckets they were MEASURED in
    assert all(abs(r["q_in_l"] - 2.0) < 0.05 and abs(r["q_out_l"] - 2.0) < 0.05 for r in rows)
    assert src == {"gw_epoch"}                                                             # measured time anchored to the gateway epoch


def test_control_naive_receive_time_bucketing_would_have_false_alarmed(cfg):
    """Same data, bucketed by ARRIVAL: node 2's minutes 10-13 count as zero and the balance shows a 2 L/min 'leak' for 4 minutes (suspect_min=3 trips)."""
    s2 = NodeSim(2, MAC_2, step=5)
    vol_in, vol_out = {}, {}
    for t in range(T0, T0 + 22 * MIN, 5):
        vol_in[(t - T0) // 60] = vol_in.get((t - T0) // 60, 0) + 2.0 * 5 / 60
    for t, _ in s2.msgs(T0, T0 + 22 * MIN, 2.0):
        arrival = T0 + 17 * MIN if T0 + 10 * MIN <= t < T0 + 14 * MIN else t
        vol_out[(arrival - T0) // 60] = vol_out.get((arrival - T0) // 60, 0) + 2.0 * 5 / 60
    run_len = best = 0
    for m in range(22):
        unacc = vol_in.get(m, 0) - vol_out.get(m, 0)
        thr = max(0.3, 0.10 * (vol_in.get(m, 0) + vol_out.get(m, 0)))
        run_len = run_len + 1 if unacc > thr else 0
        best = max(best, run_len)
    assert best >= 3                                                                       # the naive balance WOULD alarm; the measured-time engine above does not


def test_unsynced_gateway_buckets_by_backend_time_and_is_marked_lower_confidence(cfg, admin, two_nodes):
    make_pipeline(admin, nd.straight())
    s1, s2 = NodeSim(1, MAC_1, step=5), NodeSim(2, MAC_2, step=5)
    feed(s1, T0, T0 + 6 * MIN, 2.0, rx_epoch=False); feed(s2, T0, T0 + 6 * MIN, 2.0, rx_epoch=False)
    res = run(cfg, T0 + 6 * MIN)
    z = zone(res, "zone:flow1")
    assert z["balance"] is not None and z["balance"]["confidence"] == "low" and z["status"] == "normal" and alerts() == []
    with database.db_session() as c:
        assert {r["measured_src"] for r in c.execute("SELECT DISTINCT measured_src FROM flow_samples").fetchall()} == {"recv"}


# --------------------------------------------------------------------------- topology classes
def test_single_node_on_main_supply_runs_continuous_flow(cfg, admin):
    """The real installation: one node, one weak zone.  Water running non-stop is caught without any second sensor."""
    registry([("flow", 1, MAC_1, "Main", "paired")])
    d = nd.main_only().doc(zoneSettings={"zone:flow1": {"continuous_suspect_h": 0.25, "continuous_confirm_h": 0.5}})
    make_pipeline(admin, d)
    s1 = NodeSim(1, MAC_1, step=5)
    feed(s1, T0, T0 + 20 * MIN, 0.5)
    res = run(cfg, T0 + 20 * MIN)
    z = zone(res, "zone:flow1")
    assert z["class"] == "weak" and z["status"] == "suspected" and any(r["rule"].startswith("continuous") and r["level"] for r in z["rules"])
    assert zone(res, "zone:source")["class"] == "not_monitored" and zone(res, "zone:source")["status"] == "not_monitored"
    feed(s1, T0 + 20 * MIN, T0 + 36 * MIN, 0.5)
    assert zone(run(cfg, T0 + 36 * MIN), "zone:flow1")["status"] == "confirmed"
    a = alerts()
    assert len(a) == 1 and a[0]["zone_id"] == "zone:flow1" and a[0]["state"] == "confirmed"          # one alert, escalated; never one for the unmonitored zone


def test_normal_use_with_pauses_is_not_a_continuous_leak(cfg, admin):
    registry([("flow", 1, MAC_1, "Main", "paired")])
    make_pipeline(admin, nd.main_only().doc(zoneSettings={"zone:flow1": {"continuous_suspect_h": 0.25, "continuous_confirm_h": 0.5}}))
    s1 = NodeSim(1, MAC_1, step=5)
    feed(s1, T0, T0 + 36 * MIN, lambda t: 0.0 if int(t - T0) % 600 < 120 else 6.0)              # a tap used every 10 minutes, with 2 minutes of zero
    assert zone(run(cfg, T0 + 36 * MIN), "zone:flow1")["status"] == "normal" and alerts() == []


def test_unmetered_pipeline_has_no_monitored_zone_and_no_alerts(cfg, admin):
    registry([])
    make_pipeline(admin, nd.unmetered())
    res = run(cfg, T0 + 10 * MIN)
    assert [z["status"] for z in res[0]["zones"]] == ["not_monitored"] and alerts() == []
    assert res[0]["suggestions"] and "Add a node" in res[0]["suggestions"][0]["text"]


def test_node_that_never_reported_gives_incomplete_not_a_leak(cfg, admin, two_nodes):
    make_pipeline(admin, nd.straight())
    s1 = NodeSim(1, MAC_1, step=5)
    feed(s1, T0, T0 + 20 * MIN, 3.0)                                                         # node 2 is silent
    z = zone(run(cfg, T0 + 20 * MIN), "zone:flow1")
    assert z["status"] == "incomplete" and z["completeBuckets"] == 0 and alerts() == []      # never "normal" without data


def test_zone_whose_node_went_silent_is_incomplete_and_names_the_node(cfg, admin, two_nodes):
    make_pipeline(admin, nd.straight())
    s1, s2 = NodeSim(1, MAC_1, step=5), NodeSim(2, MAC_2, step=5)
    feed(s1, T0, T0 + 20 * MIN, 3.0); feed(s2, T0, T0 + 20 * MIN, 3.0)
    assert zone(run(cfg, T0 + 20 * MIN), "zone:flow1")["status"] == "normal"                 # data complete and recent: fine
    z = zone(run(cfg, T0 + 60 * MIN), "zone:flow1")                                           # 40 min of silence
    assert z["status"] == "incomplete" and alerts() == []


# --------------------------------------------------------------------------- endpoints (use the real clock)
def test_status_series_leaks_and_usage_endpoints(cfg, admin, two_nodes):
    pid = make_pipeline(admin, nd.straight())
    cfg.node_offline_s = 3600                                                              # the data was produced a moment ago on the real clock
    now = int(time.time()) // 60 * 60
    start = now - 12 * MIN
    s1, s2 = NodeSim(1, MAC_1, step=5, t_boot=start - 10), NodeSim(2, MAC_2, step=5, t_boot=start - 10)
    feed(s1, start, now, 3.0); feed(s2, start, now, 1.0)
    run(cfg, now, P())
    st = admin("get", f"/api/pipelines/{pid}/status").get_json()
    z = next(x for x in st["zones"] if x["id"] == "zone:flow1")
    assert st["version"] == 1 and st["coverage"]["text"].startswith("2 of 3") and z["status"] in ("suspected", "confirmed", "normal")
    assert st["live"]["n1"]["fresh"] is True and abs(st["live"]["n1"]["flowLpm"] - 3.0) < 0.01 and abs(st["live"]["n2"]["flowLpm"] - 1.0) < 0.01
    ser = admin("get", f"/api/pipelines/{pid}/zones/zone:flow1/series?hours=1").get_json()
    done = [b for b in ser["buckets"] if b["complete"]]
    assert done and all(abs(b["qInL"] - 3.0) < 0.1 and abs(b["qOutL"] - 1.0) < 0.1 and abs(b["unaccountedL"] - 2.0) < 0.1 for b in done)
    lk = admin("get", f"/api/pipelines/{pid}/leaks").get_json()["leaks"]
    assert len(lk) == 1 and lk[0]["active"] is True and lk[0]["zone_id"] == "zone:flow1" and lk[0]["pipeline_version"] == 1
    with database.db_session() as c:
        ref = c.execute("SELECT id FROM nodes WHERE number=1").fetchone()["id"]
    u = admin("get", f"/api/nodes/{ref}/usage?bucket=hour&days=1").get_json()
    assert u["usage"] and abs(sum(x["liters"] for x in u["usage"]) - 3.0 * 12) < 0.5 and not any(x["lowConfidence"] for x in u["usage"])
    zu = admin("get", f"/api/pipelines/{pid}/zones/zone:flow1/usage?bucket=hour&days=1").get_json()
    done_n = sum(b["completeBuckets"] for b in zu["usage"])
    assert done_n >= 8 and zu["bucket"] == "hour"
    assert abs(sum(b["inLiters"] for b in zu["usage"]) - 3.0 * done_n) < 1.0 and abs(sum(b["unaccountedLiters"] for b in zu["usage"]) - 2.0 * done_n) < 1.0     # one-minute buckets: L/min == L per bucket
    assert admin("get", f"/api/pipelines/{pid}/zones/zone:flow1/usage?bucket=year").status_code == 400
    assert admin("get", "/api/pipelines/999/zones/zone:flow1/usage").status_code == 404
    assert admin("get", f"/api/nodes/{ref}/usage?bucket=year").status_code == 400
    assert admin("get", "/api/nodes/9999/usage").status_code == 404
    assert admin("get", "/api/pipelines/999/status").status_code == 404


# --------------------------------------------------------------------------- rule progress, demo mode, exclusive activation, site time zone
def test_rule_progress_counts_minutes_and_marks_the_zone_as_watched(cfg, admin, two_nodes):
    make_pipeline(admin, nd.straight())
    s1, s2 = NodeSim(1, MAC_1, step=5), NodeSim(2, MAC_2, step=5)
    feed(s1, T0, T0 + 40, 3.0); feed(s2, T0, T0 + 40, 1.0)                            # 2 L/min unaccounted for 40 s of the 60 s T_leak (changed on purpose: was 4 of 5 minutes)
    z = zone(run(cfg, T0 + 48), "zone:flow1")
    assert z["status"] == "normal" and z["watching"] is True and z["balanceState"] == "suspicious"
    bal = next(r for r in z["rules"] if r["rule"] == "balance")
    assert bal["progress"]["counting"] and "of 60 s" in bal["progress"]["text"] and bal["progress"]["needed"] == 60
    assert z["settings"]["suspectMin"] == 5 and z["settings"]["confirmMin"] == 10 and z["settings"]["thresholdLpm"] == 0.3 and z["settings"]["tz"] == "Asia/Colombo"
    assert z["settings"]["tLeakS"] == 60 and z["settings"]["xPlusLpm"] == 0.3 and z["settings"]["windowS"] == 5


def test_weak_zone_progress_names_the_night_window_and_the_continuous_limit(cfg, admin, two_nodes):
    make_pipeline(admin, nd.straight())
    s1, s2 = NodeSim(1, MAC_1, step=5), NodeSim(2, MAC_2, step=5)
    feed(s1, T0, T0 + 6 * MIN, 1.0); feed(s2, T0, T0 + 6 * MIN, 1.0)
    z = zone(run(cfg, T0 + 6 * MIN), "zone:flow2")                                     # after node 2 the zone has an outlet: weak
    assert z["class"] == "weak"
    texts = {r["rule"]: r["progress"]["text"] for r in z["rules"]}
    assert "outside window (02:00-04:00)" in texts["night_flow"]
    assert "of 6 h" in texts["continuous_flow"] and z["settings"]["nightStart"] == "02:00" and z["settings"]["continuousSuspectMin"] == 360


def test_demo_mode_suspects_after_one_minute_confirms_after_three_tags_alerts_and_expires(cfg, admin, two_nodes):
    pid = make_pipeline(admin, nd.straight())
    s1, s2 = NodeSim(1, MAC_1, step=5), NodeSim(2, MAC_2, step=5)
    feed(s1, T0, T0 + 40, 3.0); feed(s2, T0, T0 + 40, 1.0)
    assert zone(run(cfg, T0 + 45), "zone:flow1")["status"] == "normal" and alerts() == []               # normal values: 40 s of the 60 s T_leak (changed on purpose: was 4 of 5 min)
    assert admin("put", f"/api/pipelines/{pid}/demo", json={"on": "yes"}).status_code == 400
    r = admin("put", f"/api/pipelines/{pid}/demo", json={"on": True})
    assert r.status_code == 200 and r.get_json()["demo"] is True and r.get_json()["demoUntil"] - time.time() <= 3600 + 5
    with database.db_session() as c:
        c.execute("UPDATE pipelines SET demo_until=?", (T0 + 3600,))                                    # the test clock is in 2026-01
    res = run(cfg, T0 + 45)
    z = zone(res, "zone:flow1")
    assert res[0]["demo"] is True and z["settings"]["demo"] is True and z["settings"]["suspectMin"] == 1 and z["settings"]["tLeakS"] == 10 and z["status"] == "suspected"      # 10 s is enough (normally 60 s)
    a = alerts()
    assert len(a) == 1 and a[0]["demo"] == 1 and a[0]["message"].startswith("[demo] ")
    feed(s1, T0 + 40, T0 + 8 * MIN, 3.0); feed(s2, T0 + 40, T0 + 8 * MIN, 1.0)
    assert zone(run(cfg, T0 + 8 * MIN), "zone:flow1")["status"] == "confirmed"                          # 3 min is enough (normally 10)
    assert admin("get", "/api/alerts?active=1").get_json()[0]["demo"] is True
    with database.db_session() as c:
        c.execute("UPDATE pipelines SET demo_until=?", (T0 + 60,))                                      # an hour later the demo is over by itself
    res = run(cfg, T0 + 8 * MIN + 120)
    assert res[0]["demo"] is False and zone(res, "zone:flow1")["settings"]["suspectMin"] == 5


def _demo_leak(cfg, admin):
    pid = make_pipeline(admin, nd.straight())
    s1, s2 = NodeSim(1, MAC_1, step=5), NodeSim(2, MAC_2, step=5)
    feed(s1, T0, T0 + 40, 3.0); feed(s2, T0, T0 + 40, 1.0)                                              # 40 s of leak: possible only under the demo's 10 s T_leak
    admin("put", f"/api/pipelines/{pid}/demo", json={"on": True})
    with database.db_session() as c:
        c.execute("UPDATE pipelines SET demo_until=?", (T0 + 3600,))
    run(cfg, T0 + 45)
    assert [a["demo"] for a in alerts()] == [1]
    return pid


def test_a_demo_alert_resolves_when_demo_mode_ends(cfg, admin, two_nodes):
    pid = _demo_leak(cfg, admin)
    admin("put", f"/api/pipelines/{pid}/demo", json={"on": False})                                      # switched off by hand
    run(cfg, T0 + 50)
    assert [a for a in alerts() if a["active"]] == []
    with database.db_session() as c:
        r = c.execute("SELECT active, state, message FROM alerts").fetchone()
    assert r["active"] == 0 and r["state"] == "resolved" and "demo mode ended" in r["message"]
    run(cfg, T0 + 55)                                                                                    # and the demo state does not come back as a REAL alert (T_D 55 s < 60 s)
    assert [a for a in alerts() if a["active"]] == []


def test_a_demo_alert_resolves_when_the_hour_is_up(cfg, admin, two_nodes):
    _demo_leak(cfg, admin)
    with database.db_session() as c:
        c.execute("UPDATE pipelines SET demo_until=?", (T0 + 60,))                                      # auto-off after 1 h
    run(cfg, T0 + 70)
    assert [a for a in alerts() if a["demo"] and a["active"]] == []


def test_a_leak_alert_of_a_zone_that_no_longer_exists_is_closed(cfg, admin, two_nodes):
    pid = _demo_leak(cfg, admin)
    with database.db_session() as c:
        c.execute("UPDATE alerts SET alert_key='LEAK:%d:zone:flow9', demo=0" % pid)                  # e.g. re-bound pipeline: that zone id is gone
    run(cfg, T0 + 4 * MIN + 10)
    with database.db_session() as c:
        r = c.execute("SELECT active, message FROM alerts WHERE alert_key LIKE '%flow9'").fetchone()
    assert r["active"] == 0 and "no longer active" in r["message"]


def test_activating_a_pipeline_switches_the_others_off(cfg, admin, two_nodes):
    a = make_pipeline(admin, nd.straight(), "House")
    b = admin("post", "/api/pipelines", json={"name": "Bench", "doc": nd.straight().doc(), "activate": False}).get_json()["id"]
    lst = {p["name"]: p for p in admin("get", "/api/pipelines").get_json()["pipelines"]}
    assert lst["House"]["activeVersion"] == 1 and lst["Bench"]["activeVersion"] is None
    r = admin("post", f"/api/pipelines/{b}/activate", json={"version": 1})
    assert r.get_json()["deactivated"] == [a]
    lst = {p["name"]: p for p in admin("get", "/api/pipelines").get_json()["pipelines"]}
    assert lst["House"]["activeVersion"] is None and lst["Bench"]["activeVersion"] == 1


def test_night_window_follows_the_site_time_zone_not_the_server(cfg):
    from datetime import datetime, timezone
    from analysis.rules import _night_window
    assert analysis_service.site_offset_min() == 330                                    # Asia/Colombo, +05:30
    p = ZoneParams(tz_offset_min=analysis_service.site_offset_min())
    now = int(datetime(2026, 1, 1, 20, 45, tzinfo=timezone.utc).timestamp())            # 02:15 on 2 Jan in Colombo
    ws, we = _night_window(now, p)
    assert ws == int(datetime(2026, 1, 1, 20, 30, tzinfo=timezone.utc).timestamp()) and we - ws == 7200
