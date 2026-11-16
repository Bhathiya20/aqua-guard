"""Bucketing, mass balance and the leak rules (brief 7.2): pure functions, driven by synthetic flow."""
import pytest

from analysis.buckets import Sample, node_buckets
from analysis.rules import CONFIRMED, SUSPECTED, ZoneParams, evaluate_zone, zone_series
from analysis.zones import partition
from pipeline.model import parse
from tests import netdocs as nd

T0 = 1_767_268_800                 # 2026-01-01 12:00:00 UTC (outside the 02:00-04:00 night window)
NIGHT0 = 1_767_225_600 + 2 * 3600  # 2026-01-01 02:00:00 UTC


def run(rate, t0, t1, step=2, src="gw_epoch", gaps=()):
    """Samples every `step` s with a constant flow (L/min); `rate` may be a function of time.  `gaps` = [(from, to)] with no samples at all."""
    out, t = [], t0
    while t < t1:
        if not any(a <= t < b for a, b in gaps):
            r = rate(t) if callable(rate) else rate
            out.append(Sample(t, r * step / 60.0, 0, src))
        t += step
    return out


def zones(doc):
    return {z.id: z for z in partition(parse(doc.doc())).zones}


def evaluate(zone, node_samples, now, params=None, prev=0, t0=None):
    p = params or ZoneParams(tz_offset_min=0)
    t0 = t0 if t0 is not None else now - int(p.lookback_h * 3600)
    series = {k: node_buckets(s, t0, now, p.bucket_s, p.max_gap_s) for k, s in node_samples.items()}
    return evaluate_zone(zone, zone_series(zone, series, p, t0, now), now, prev, p), zone_series(zone, series, p, t0, now)


N1, N2 = ("flow", 1), ("flow", 2)


# --------------------------------------------------------------------------- buckets
def test_bucket_volume_and_completeness():
    b = node_buckets(run(3.0, T0, T0 + 180), T0, T0 + 180)
    assert [round(x.liters, 3) for x in b.values()] == [3.0, 3.0, 3.0] and all(x.complete for x in b.values())


def test_missing_samples_make_a_bucket_incomplete_never_zero_flow():
    b = node_buckets(run(3.0, T0, T0 + 180, gaps=[(T0 + 70, T0 + 110)]), T0, T0 + 180)
    ts = sorted(b)
    assert b[ts[0]].complete and not b[ts[1]].complete and b[ts[2]].complete
    b2 = node_buckets([], T0, T0 + 60)
    assert not b2[T0].complete and b2[T0].samples == 0


def test_edges_must_be_covered():
    b = node_buckets(run(1.0, T0 + 30, T0 + 60), T0, T0 + 60)             # first half of the minute is missing
    assert not b[T0].complete


def test_unrecovered_loss_marks_the_bucket_incomplete():
    s = run(2.0, T0, T0 + 60)
    s[10] = Sample(s[10].t, s[10].liters * 3, 2, "gw_epoch")             # 2 sequence numbers missing right before this sample
    assert not node_buckets(s, T0, T0 + 60)[T0].complete
    assert node_buckets(s, T0, T0 + 60, strict_gaps=False)[T0].complete


def test_backend_clock_samples_are_low_confidence():
    b = node_buckets(run(1.0, T0, T0 + 60, src="recv"), T0, T0 + 60)
    assert b[T0].complete and b[T0].confidence == "low"


# --------------------------------------------------------------------------- strong zone: balance rule
def strong():
    return zones(nd.straight())["zone:flow1"]            # Node 1 in, Node 2 out, no outlets


def test_balanced_flow_is_normal():
    ev, _ = evaluate(strong(), {N1: run(3.0, T0, T0 + 3600), N2: run(3.0, T0, T0 + 3600)}, T0 + 3600)
    assert ev.level == 0 and ev.status == "normal"


def test_meter_tolerance_is_not_a_leak():
    ev, _ = evaluate(strong(), {N1: run(3.0, T0, T0 + 3600), N2: run(2.8, T0, T0 + 3600)}, T0 + 3600)   # 0.2 L/min < max(0.3, 10 % of 5.8)
    assert ev.status == "normal"


def test_leak_in_a_strong_zone_goes_suspected_then_confirmed():
    leak = lambda t: 3.0                                                    # upstream 3 L/min, downstream 2 L/min -> 1 L/min vanishes
    n1, n2 = run(leak, T0, T0 + 3600), run(2.0, T0, T0 + 3600)
    ev0, _ = evaluate(strong(), {N1: n1, N2: n2}, T0 + 8 * 60 + 90)
    assert ev0.level == 0                                                   # 8 min < suspect_min
    ev1, _ = evaluate(strong(), {N1: n1, N2: n2}, T0 + 12 * 60 + 90)
    assert ev1.level == SUSPECTED and ev1.status == "suspected" and "unaccounted" in ev1.reason
    ev2, _ = evaluate(strong(), {N1: n1, N2: n2}, T0 + 33 * 60 + 90, prev=ev1.level)
    assert ev2.level == CONFIRMED and ev2.status == "confirmed"


def test_threshold_and_duration_are_configurable_per_zone():
    n1, n2 = run(3.0, T0, T0 + 3600), run(2.0, T0, T0 + 3600)
    p = ZoneParams(tz_offset_min=0, overrides={"zone:flow1": {"suspect_min": 3, "confirm_min": 6}})
    assert evaluate(strong(), {N1: n1, N2: n2}, T0 + 4 * 60 + 90, p)[0].level == SUSPECTED
    assert evaluate(strong(), {N1: n1, N2: n2}, T0 + 7 * 60 + 90, p, prev=1)[0].level == CONFIRMED
    p2 = ZoneParams(tz_offset_min=0, overrides={"zone:flow1": {"abs_min_lpm": 1.5, "tolerance": 0.5}})        # raised threshold: the same leak is tolerated
    assert evaluate(strong(), {N1: n1, N2: n2}, T0 + 40 * 60, p2)[0].level == 0


def test_a_leak_clears_only_after_resolve_minutes_of_normal_flow():
    leak_until = T0 + 20 * 60
    n1 = run(3.0, T0, T0 + 3600)
    n2 = run(lambda t: 2.0 if t < leak_until else 3.0, T0, T0 + 3600)
    assert evaluate(strong(), {N1: n1, N2: n2}, leak_until + 90 + 3 * 60, prev=1)[0].level == 1       # 3 normal minutes: still held
    assert evaluate(strong(), {N1: n1, N2: n2}, leak_until + 90 + 7 * 60, prev=1)[0].level == 0       # 7 normal minutes: cleared


def test_incomplete_buckets_are_excluded_not_counted_as_leak_or_as_normal():
    n1 = run(3.0, T0, T0 + 3600)
    n2 = run(3.0, T0, T0 + 3600, gaps=[(T0 + 600, T0 + 1200)])             # downstream node silent for 10 min: out volume would look like 0
    ev, series = evaluate(strong(), {N1: n1, N2: n2}, T0 + 1500)
    assert ev.level == 0 and ev.status == "normal"                          # NOT a 3 L/min leak
    gap = [b for b in series if T0 + 600 <= b.ts < T0 + 1200]
    assert gap and all(not b.complete and not b.exceed and b.unacc_l is None for b in gap)
    # a leak is running, then the downstream node goes silent: the hole must neither clear nor escalate the suspicion
    n2_leak = run(2.0, T0, T0 + 3600, gaps=[(T0 + 720, T0 + 3600)])
    ev2, _ = evaluate(strong(), {N1: n1, N2: n2_leak}, T0 + 1500, prev=1)
    assert ev2.level == 1 and ev2.status == "suspected"


def test_long_hole_does_not_join_two_episodes():
    leak = lambda a, b: run(3.0, a, b)
    n1 = run(3.0, T0, T0 + 7200)
    n2 = run(lambda t: 2.0 if (t < T0 + 300 or t >= T0 + 3600) else 3.0, T0, T0 + 7200, gaps=[(T0 + 300, T0 + 3600)])
    ev, _ = evaluate(strong(), {N1: n1, N2: n2}, T0 + 3600 + 8 * 60 + 90)   # 5 min before the hole + 8 min after it, one hour apart
    assert ev.level == 0


def test_downstream_larger_than_upstream_raises_a_meter_fault_hint_not_a_leak():
    ev, _ = evaluate(strong(), {N1: run(2.0, T0, T0 + 3600), N2: run(3.5, T0, T0 + 3600)}, T0 + 3600)
    assert ev.level == 0 and ev.meter_fault_hint


def test_gateway_time_loss_lowers_confidence_but_still_evaluates():
    ev, series = evaluate(strong(), {N1: run(3.0, T0, T0 + 3600, src="recv"), N2: run(3.0, T0, T0 + 3600)}, T0 + 3600)
    assert ev.level == 0 and all(b.confidence == "low" for b in series if b.complete)


def test_a_node_missing_from_the_database_means_no_data_not_normal():
    ev, _ = evaluate(strong(), {N1: run(3.0, T0, T0 + 3600)}, T0 + 3600)    # node 2 never reported
    assert ev.level == 0 and (ev.last_bucket is None or not ev.last_bucket.complete)


# --------------------------------------------------------------------------- weak zone: night flow + continuous flow
def weak():
    return zones(nd.main_only())["zone:flow1"]             # Node 1 on the main supply, two outlets, no outflow meters


def household(t):                                         # a normal day: bursts of use, silence in the night
    return 6.0 if (t // 600) % 3 == 0 else 0.0


def test_normal_household_use_is_not_a_leak():
    ev, _ = evaluate(weak(), {N1: run(household, NIGHT0 - 3600, NIGHT0 + 3 * 3600 + 600)}, NIGHT0 + 3 * 3600 + 600, t0=NIGHT0 - 3600)
    assert ev.level == 0 and ev.status == "normal"


def test_minimum_night_flow_above_threshold_is_suspected_then_confirmed():
    drip = lambda t: 0.6 + (household(t) if not (NIGHT0 <= t < NIGHT0 + 7200) else 0.0)     # a 0.6 L/min leak: the night floor never reaches zero
    s = run(drip, NIGHT0 - 3600, NIGHT0 + 4 * 3600)
    ev_mid, _ = evaluate(weak(), {N1: s}, NIGHT0 + 40 * 60 + 90, t0=NIGHT0 - 3600)
    assert ev_mid.level == SUSPECTED and any(r.rule == "night_flow" and r.level == SUSPECTED for r in ev_mid.results)
    ev_end, _ = evaluate(weak(), {N1: s}, NIGHT0 + 2 * 3600 + 150, prev=1, t0=NIGHT0 - 3600)
    assert ev_end.level == CONFIRMED and "minimum night flow" in ev_end.reason


def test_a_night_with_a_zero_floor_clears_the_night_rule():
    s = run(lambda t: 0.0 if NIGHT0 <= t < NIGHT0 + 7200 else household(t), NIGHT0 - 3600, NIGHT0 + 4 * 3600)
    ev, _ = evaluate(weak(), {N1: s}, NIGHT0 + 2 * 3600 + 150, prev=2, t0=NIGHT0 - 3600)
    night = [r for r in ev.results if r.rule == "night_flow"][0]
    assert night.level == 0 and night.clear


def test_night_with_too_little_data_decides_nothing():
    s = run(0.6, NIGHT0, NIGHT0 + 7200, gaps=[(NIGHT0 + 600, NIGHT0 + 6000)])
    ev, _ = evaluate(weak(), {N1: s}, NIGHT0 + 2 * 3600 + 150, t0=NIGHT0 - 3600)
    night = [r for r in ev.results if r.rule == "night_flow"][0]
    assert night.level is None and ev.level == 0


def test_continuous_flow_for_hours_is_flagged():
    now = T0 + 13 * 3600
    s = run(1.5, T0, now)
    assert evaluate(weak(), {N1: s}, T0 + 3 * 3600)[0].level == 0
    ev6, _ = evaluate(weak(), {N1: s}, T0 + 6 * 3600 + 200)
    assert ev6.level == SUSPECTED and "has not dropped to zero" in ev6.reason
    assert evaluate(weak(), {N1: s}, T0 + 12 * 3600 + 200, prev=1)[0].level == CONFIRMED


def test_one_minute_of_zero_flow_breaks_continuous_flow():
    s = run(lambda t: 0.0 if T0 + 4 * 3600 <= t < T0 + 4 * 3600 + 120 else 1.5, T0, T0 + 7 * 3600)
    ev, _ = evaluate(weak(), {N1: s}, T0 + 7 * 3600)
    assert ev.level == 0                                                    # only ~3 h since the pause


def test_continuous_flow_survives_small_data_gaps_but_not_big_ones():
    now = T0 + 7 * 3600
    small = run(1.5, T0, now, gaps=[(T0 + 3 * 3600, T0 + 3 * 3600 + 300)])           # 5 min hole in 7 h: coverage stays above 90 %
    assert evaluate(weak(), {N1: small}, now)[0].level == SUSPECTED
    big = run(1.5, T0, now, gaps=[(T0 + 1 * 3600, T0 + 3 * 3600)])                    # 2 h hole: the run is cut there
    assert evaluate(weak(), {N1: big}, now)[0].level == 0


def test_not_monitored_zone_is_never_evaluated():
    z = zones(nd.unmetered())["zone:source"]
    ev = evaluate_zone(z, [], T0, 0, ZoneParams())
    assert ev.status == "not_monitored" and ev.level == 0


def test_status_is_incomplete_when_recent_data_is_missing():
    s = run(1.0, T0, T0 + 1800)
    ev, _ = evaluate(weak(), {N1: s}, T0 + 1800 + 40 * 60)                  # 40 minutes of silence
    assert ev.status == "incomplete" and ev.level == 0
