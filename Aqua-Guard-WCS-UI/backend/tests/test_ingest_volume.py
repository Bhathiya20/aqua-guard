"""Volume accounting from cumulative pulse counters: duplicates, gaps, reorder, reboot, rollover, restart."""
import pytest
from datetime import timedelta

import database
import ingest
import services
from database import db_session
from tests.conftest import T0, flow_msg


def put(msg, now=T0):
    with db_session() as c:
        return ingest.ingest_flow(c, msg, now)


def liters(node=1):
    c = database.get_db()
    try:
        return services.consumption_total(c, node)["liters"]
    finally:
        c.close()


def test_calibration_pulses_to_liters(app):
    put(flow_msg(seq=1, cum=450, interval=450))         # 450 pulses = 1 L
    assert liters() == pytest.approx(1.0)
    put(flow_msg(seq=2, cum=1350, interval=900))        # +900 pulses = +2 L
    assert liters() == pytest.approx(3.0)
    put(flow_msg(seq=3, cum=1575, interval=225))        # +225 pulses = +0.5 L
    assert liters() == pytest.approx(3.5)


def test_duplicate_does_not_double_count(app):
    assert put(flow_msg(seq=1, cum=450))["result"] == "stored"
    assert put(flow_msg(seq=1, cum=450))["result"] == "duplicate"
    assert put(flow_msg(seq=1, cum=450, recovered=True))["result"] == "duplicate"   # retransmitted copy
    assert liters() == pytest.approx(1.0)
    c = database.get_db()
    assert c.execute("SELECT COUNT(*) FROM flow_samples").fetchone()[0] == 1
    c.close()


def test_missing_packets_keep_volume(app):
    put(flow_msg(seq=1, cum=450, interval=450))
    put(flow_msg(seq=5, cum=2250, interval=450))        # seq 2,3,4 never arrive: 1800 pulses must still count
    assert liters() == pytest.approx(5.0)
    c = database.get_db()
    row = c.execute("SELECT gap_before, delta_pulses FROM flow_samples WHERE seq=5").fetchone()
    c.close()
    assert row["gap_before"] == 3 and row["delta_pulses"] == 1800


def test_out_of_order_and_late_recovery_no_double_count(app):
    put(flow_msg(seq=1, cum=450, interval=450))
    put(flow_msg(seq=4, cum=1800, interval=450))        # gap 2,3
    assert liters() == pytest.approx(4.0)
    put(flow_msg(seq=3, cum=1350, interval=450, recovered=True))    # late arrival splits the delta
    assert liters() == pytest.approx(4.0)
    put(flow_msg(seq=2, cum=900, interval=450, recovered=True))
    assert liters() == pytest.approx(4.0)
    c = database.get_db()
    deltas = [r[0] for r in c.execute("SELECT delta_pulses FROM flow_samples ORDER BY seq")]
    c.close()
    assert deltas == [450, 450, 450, 450]


def test_arrival_in_reverse_order(app):
    for seq, cum in [(4, 1800), (3, 1350), (2, 900), (1, 450)]:
        put(flow_msg(seq=seq, cum=cum, interval=450))
    assert liters() == pytest.approx(4.0)


def test_node_reboot_starts_new_epoch_without_overwriting_history(app):
    put(flow_msg(boot=111, seq=1, cum=900, interval=900))
    put(flow_msg(boot=111, seq=2, cum=1800, interval=900))
    assert liters() == pytest.approx(4.0)
    put(flow_msg(boot=222, seq=1, cum=450, interval=450))           # counter restarted at 0 after reboot
    put(flow_msg(boot=222, seq=2, cum=900, interval=450))
    assert liters() == pytest.approx(6.0)                            # old 4 L kept, +2 L new epoch
    c = database.get_db()
    assert c.execute("SELECT sessions FROM devices WHERE device_key='node1'").fetchone()[0] == 2
    c.close()


def test_counter_rollover_u32(app):
    base = (1 << 32) - 450
    put(flow_msg(boot=7, seq=50, cum=base, interval=450))          # joined mid-session: only its own interval counted
    put(flow_msg(boot=7, seq=51, cum=(base + 900) % (1 << 32), interval=900))   # wraps past 2^32
    assert liters() == pytest.approx(1.0 + 2.0)


def test_join_mid_session_does_not_credit_history(app):
    put(flow_msg(boot=9, seq=5000, cum=4_000_000, interval=450))   # backend DB was empty; node ran for days
    assert liters() == pytest.approx(1.0)                           # NOT 8888 L


def test_first_packets_lost_right_after_node_boot_still_counted(app):
    put(flow_msg(boot=5, seq=4, cum=1800, interval=450, uptime=8))   # seq 1-3 lost, node up for 8 s: counter began at 0
    assert liters() == pytest.approx(4.0)


def test_counter_going_backwards_is_flagged_not_counted(app):
    put(flow_msg(seq=1, cum=450))
    put(flow_msg(seq=2, cum=900))
    r = put(flow_msg(seq=3, cum=100))                               # impossible within one epoch
    assert r["anomaly"] is True
    assert liters() == pytest.approx(2.0)


def test_nodes_are_independent(app):
    put(flow_msg(node=1, seq=1, cum=450))
    put(flow_msg(node=2, seq=1, cum=225, interval=225))
    assert liters(1) == pytest.approx(1.0) and liters(2) == pytest.approx(0.5)


def test_database_restart_preserves_totals_and_dedup(app, cfg):
    put(flow_msg(seq=1, cum=450))
    put(flow_msg(seq=2, cum=900))
    # simulate process restart: re-run init on the same file and re-open
    database.set_db_path(cfg.db_path)
    database.init_db(cfg.default_admin_email, cfg.default_admin_password)
    assert liters() == pytest.approx(2.0)
    assert put(flow_msg(seq=2, cum=900))["result"] == "duplicate"
    put(flow_msg(seq=3, cum=1350))
    assert liters() == pytest.approx(3.0)


@pytest.mark.parametrize("bad", [
    {"schema": 1}, {"schema": None}, {"node": 9}, {"node": 0}, {"boot": 0}, {"seq": 0}, {"seq": -4},
    {"cumPulses": -1}, {"cumPulses": "x"}, {"cumPulses": float("nan")}, {"intervalPulses": 10 ** 9},
    {"pulsesPerLiter": 0}, {"flowRate": -1}, {"flowRate": 9999}, {"intervalMs": 0}, {"boot": True},
])
def test_invalid_input_rejected_and_nothing_stored(app, bad):
    msg = flow_msg()
    msg.update(bad)
    with pytest.raises(ingest.IngestError):
        put(msg)
    c = database.get_db()
    assert c.execute("SELECT COUNT(*) FROM flow_samples").fetchone()[0] == 0
    c.close()


def test_missing_field_and_non_object_rejected(app):
    m = flow_msg(); del m["cumPulses"]
    with pytest.raises(ingest.IngestError):
        put(m)
    with pytest.raises(ingest.IngestError):
        put([1, 2, 3])


def test_topic_payload_node_mismatch_rejected(app):
    with db_session() as c:
        with pytest.raises(ingest.IngestError):
            ingest.ingest_flow(c, flow_msg(node=2), T0, expected_node=1)


def test_gateway_epoch_used_for_timestamp_but_bogus_epoch_ignored(app):
    good = int(T0.timestamp()) - 120                                # buffered 2 minutes at the gateway
    put(flow_msg(seq=1, epoch=good))
    put(flow_msg(seq=2, cum=900, epoch=1_700_000_001))               # 2023 epoch vs 2026 "now": ignored
    c = database.get_db()
    rows = {r["seq"]: r["measured_at"] for r in c.execute("SELECT seq, measured_at FROM flow_samples")}
    c.close()
    assert rows[1] == "2026-01-15 11:58:00" and rows[2] == "2026-01-15 12:00:00"


def test_daily_weekly_monthly_series(app, monkeypatch):
    monkeypatch.setattr(ingest, "MAX_ACCEPTED_SKEW_S", 400 * 86400)       # synthetic history older than the 7-day sanity limit
    cum = 0
    for day in range(0, 40):
        cum += 450 * 10                                              # 10 L per day
        t = T0 - timedelta(days=39 - day)
        put(flow_msg(boot=3, seq=day + 1, cum=cum, interval=4500, interval_ms=120000, epoch=t.timestamp()), now=T0)
    c = database.get_db()
    daily = services.consumption_series(c, 1, "day", 5, T0)
    weekly = services.consumption_series(c, 1, "week", 3, T0)
    monthly = services.consumption_series(c, 1, "month", 2, T0)
    total = services.consumption_total(c, 1)["liters"]
    c.close()
    assert all(d["liters"] == pytest.approx(10.0) for d in daily) and len(daily) >= 5
    assert sum(w["liters"] for w in weekly) == pytest.approx(10.0 * 7 * 3, abs=10.0 + 0.01)
    assert sum(m["liters"] for m in monthly) > 100.0
    assert total == pytest.approx(400.0)
