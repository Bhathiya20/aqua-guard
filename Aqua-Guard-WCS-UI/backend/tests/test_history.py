"""Resolved history: filters, CSV, real node / zone names, the detail chart and the manual resolve reason."""
import csv
import io

import pytest

import database
from tests import netdocs as nd
from tests.conftest import login
from tests.v2helpers import NodeSim, T0, dt, feed, registry

MAC_1, MAC_2 = "447b30854b88", "aabbcc000002"


@pytest.fixture()
def admin(app):
    c = app.test_client()
    h = {"X-CSRF-Token": login(c)}
    return lambda m, url, **kw: getattr(c, m)(url, headers=h, **kw)


@pytest.fixture()
def world(app, admin):
    registry([("flow", 1, MAC_1, "Main", "paired"), ("flow", 2, MAC_2, "Garden", "paired")])
    r = admin("post", "/api/pipelines", json={"name": "House", "doc": nd.straight().doc()})
    pid = r.get_json()["id"]
    with database.db_session() as c:
        refs = {x["number"]: x["id"] for x in c.execute("SELECT id, number FROM nodes").fetchall()}
    return pid, refs


def add(c, key, kind, created, cleared, **kw):
    cols = dict(alert_key=key, kind=kind, severity="critical", message=f"{kind} test", active=0, created_at=created, cleared_at=cleared, state="resolved")
    cols.update(kw)
    c.execute(f"INSERT INTO alerts ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", tuple(cols.values()))
    return c.execute("SELECT MAX(id) m FROM alerts").fetchone()["m"]


def test_history_lists_real_names_times_and_filters(admin, world):
    pid, refs = world
    with database.db_session() as c:
        a = add(c, "LEAK:1:zone:flow1", "LEAK", "2026-01-01 10:00:00", "2026-01-01 10:20:00", pipeline_id=pid, pipeline_version=1, zone_id="zone:flow1", suspected_at="2026-01-01 10:00:00",
                confirmed_at="2026-01-01 10:10:00", peak_unaccounted_lpm=2.4, lost_l=18.5, resolved_by="admin@test.local", resolve_reason="fixed_leak", resolve_note="pipe clamp", demo=0)
        b = add(c, f"USAGE:{refs[2]}", "usage_continuous", "2026-01-02 08:00:00", "2026-01-02 08:06:00", node_ref=refs[2], peak_flow_lpm=6.1, lost_l=30.0, demo=1)
        add(c, "x", "node_offline", "2026-01-05 08:00:00", "2026-01-05 08:30:00", node_ref=refs[1], severity="warning")
    h = admin("get", "/api/alerts/history").get_json()
    assert len(h["history"]) == 3
    by = {e["id"]: e for e in h["history"]}
    leak = by[a]
    assert leak["type"] == "leak" and leak["zone"]["name"] == "After Node 1" and leak["durationS"] == 1200 and leak["peakLpm"] == 2.4 and leak["lostL"] == 18.5
    assert leak["suspectedAt"] == "2026-01-01 10:00:00" and leak["confirmedAt"] == "2026-01-01 10:10:00" and leak["resolvedBy"] == "admin@test.local" and leak["reasonLabel"] == "Fixed leak"
    use = by[b]
    assert use["node"]["label"] == "Node 2 (Garden)" and use["resolvedBy"] == "auto" and use["demo"] is True and use["peakKind"] == "flow"
    q = lambda s: [e["id"] for e in admin("get", "/api/alerts/history?" + s).get_json()["history"]]
    assert q(f"node={refs[2]}") == [b]
    assert q("type=leak") == [a] and q("type=usage_continuous") == [b]
    assert q("reason=fixed_leak") == [a] and set(q("reason=auto")) == set(by) - {a}
    assert set(q("from=2026-01-02&to=2026-01-03")) == {b}
    assert q("zone=zone:flow1") == [a]


def test_csv_export_has_the_same_rows(admin, world):
    pid, refs = world
    with database.db_session() as c:
        add(c, "USAGE:1", "night_flow", "2026-01-02 23:00:00", "2026-01-02 23:03:00", node_ref=refs[1], peak_flow_lpm=2.0, lost_l=5.5, resolved_by="op@test.local", resolve_reason="tap_left_open", resolve_note="a, \"quoted\" note")
    r = admin("get", "/api/alerts/history.csv")
    assert r.status_code == 200 and "text/csv" in r.headers["Content-Type"] and "attachment" in r.headers["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(r.get_data(as_text=True).lstrip("\ufeff"))))
    assert rows[0][:5] == ["id", "type", "severity", "node", "zone"] and len(rows) == 2
    assert rows[1][1] == "Night flow" and rows[1][3] == "Node 1 (Main)" and rows[1][13] == "op@test.local" and rows[1][14] == "Tap left open" and rows[1][15] == 'a, "quoted" note'


def test_detail_has_a_flow_chart_for_usage_and_a_balance_chart_for_leaks(admin, world):
    pid, refs = world
    sim = NodeSim(2, MAC_2, step=2, t_boot=T0 - 10)
    feed(sim, T0, T0 + 120, 2.0)
    from ingest import fmt_ts
    t0, t1 = fmt_ts(dt(T0 + 10)), fmt_ts(dt(T0 + 100))
    with database.db_session() as c:
        u = add(c, "USAGE:2", "usage_prolonged", t0, t1, node_ref=refs[2], severity="warning")
        lk = add(c, "LEAK:1:zone:flow1", "LEAK", t0, t1, pipeline_id=pid, pipeline_version=1, zone_id="zone:flow1")
        for i in range(3):
            c.execute("INSERT INTO zone_buckets (pipeline_id,zone_id,bucket_ts,q_in_l,q_out_l,unaccounted_l,complete,confidence,state,version) VALUES (?,?,?,?,?,?,1,'high','suspected',1)", (pid, "zone:flow1", T0 + 60 * i, 3.0, 1.0, 2.0))
    d = admin("get", f"/api/alerts/{u}/detail").get_json()
    assert d["series"]["kind"] == "flow" and len(d["series"]["points"]) > 10 and d["series"]["points"][0]["flow"] == pytest.approx(2.0, abs=0.2)
    d = admin("get", f"/api/alerts/{lk}/detail").get_json()
    assert d["series"]["kind"] == "balance" and d["series"]["points"][0]["d"] == 2.0 and d["event"]["zone"]["name"] == "After Node 1"
    assert admin("get", "/api/alerts/99999/detail").status_code == 404
