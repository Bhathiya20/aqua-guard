"""Zone balance and leak rules (brief section 7.2).  Pure functions of (zone, bucket series, params): no clock, no DB, no Flask.

The rules are STATELESS over a rolling window of FINAL buckets (a bucket is final once it is older than `grace_s`, so a retransmission
that is still on its way does not make it look empty).  Because they are recomputed from the buckets every tick, late data that
patches a bucket is picked up automatically.  Only the alert level has memory (hysteresis) - see `next_level`.

  strong zone : balance rule     - Q_unaccounted above the threshold for >= suspect_min minutes -> suspected, >= confirm_min -> confirmed
  weak zone   : minimum night flow (unaccounted flow inside the night window) and continuous flow (Q_in never zero for X hours)
  incomplete buckets are skipped (never zero, never "normal"); a long hole stops a run instead of bridging two episodes.
"""
from dataclasses import dataclass, field

SUSPECTED, CONFIRMED = 1, 2
LEVEL_NAME = {0: "normal", 1: "suspected", 2: "confirmed"}


@dataclass
class ZoneParams:
    bucket_s: int = 60
    max_gap_s: float = 10.0
    abs_min_lpm: float = 0.3                 # never alarm below this unaccounted flow
    tolerance: float = 0.10                  # YF-S201: about +-10 % of reading, per meter
    tolerance_by_node: dict = field(default_factory=dict)      # (type, number) -> fraction
    suspect_min: float = 10                  # strong zone: sustained minutes -> suspected
    confirm_min: float = 30                  # ... -> confirmed
    resolve_min: float = 5                   # complete normal minutes needed to clear
    bridge_min: float = 15                   # longest hole of incomplete buckets a run may span
    night_start_min: int = 120               # 02:00
    night_end_min: int = 240                 # 04:00
    night_min_coverage: float = 0.6
    night_min_buckets: int = 20
    night_smooth: int = 5                    # buckets averaged inside the window before taking the minimum
    tz_offset_min: int = 0                   # minutes east of UTC of the site
    continuous_suspect_h: float = 6.0
    continuous_confirm_h: float = 12.0
    continuous_min_lpm: float = 0.05
    continuous_min_coverage: float = 0.9
    grace_s: int = 90
    lookback_h: float = 26.0
    incomplete_after_min: float = 15.0
    overrides: dict = field(default_factory=dict)              # zone id -> {field: value}   (threshold and duration per zone)
    # responsive balance (WLDS, see analysis/balance.py)
    window_s: int = 5                        # aligned window of the fast balance
    x_plus_lpm: float = 0.3                  # D above this = suspicious (floor; the meters' proportional tolerance can raise it)
    x_minus_lpm: float = 0.3                 # D below minus this = measurement inconsistency
    t_leak_s: float = 60                     # D above x+ this long = possible leak (demo mode: 10 s)
    fast_grace_s: int = 8                    # a window is final this long after it ended (late frames of a slower node)
    bridge_fast_s: int = 30                  # longest hole of incomplete windows a run may span
    pause_windows: int = 6                   # no complete window among the newest N = leak check paused
    fast_max_gap_s: float = 6.0

    def for_zone(self, zone_id: str) -> "ZoneParams":
        o = self.overrides.get(zone_id)
        if not o:
            return self
        p = ZoneParams(**{k: getattr(self, k) for k in self.__dataclass_fields__})
        for k, v in o.items():
            if k in p.__dataclass_fields__ and k != "overrides":
                setattr(p, k, v)
        return p


@dataclass(frozen=True)
class ZoneBucket:
    ts: int
    q_in_l: float | None
    q_out_l: float | None
    unacc_l: float | None
    thr_l: float | None
    complete: bool
    confidence: str
    exceed: bool          # complete and unaccounted above threshold
    fault_hint: bool      # complete and downstream clearly larger than upstream (meter fault?)


def zone_series(zone, node_series: dict, params: ZoneParams, t0: int, t1: int) -> list:
    """Balance per aligned bucket.  node_series: node key -> {ts: Bucket}.  A bucket is complete only if EVERY involved meter is complete."""
    p = params.for_zone(zone.id)
    minutes = p.bucket_s / 60.0
    out = []
    ts = t0 - (t0 % p.bucket_s)
    while ts < t1:
        involved = zone.meters()
        bs = [node_series.get(m.key, {}).get(ts) for m in involved]
        complete = bool(involved) and all(b is not None and b.complete for b in bs)
        low = any(b is not None and b.confidence == "low" for b in bs)
        if not zone.in_meter or not complete:
            out.append(ZoneBucket(ts, None, None, None, None, False, "low" if low else "high", False, False))
        else:
            qin = bs[0].liters
            qout = sum(b.liters for b in bs[1:])
            tol = sum(p.tolerance_by_node.get(m.key, p.tolerance) * b.liters for m, b in zip(involved, bs))
            thr = max(p.abs_min_lpm * minutes, tol)
            unacc = qin - qout
            out.append(ZoneBucket(ts, qin, qout, unacc, thr, True, "low" if low else "high", unacc > thr, unacc < -thr))
        ts += p.bucket_s
    return out


def final_only(series: list, now: int, params: ZoneParams) -> list:
    lim = now - params.grace_s
    return [b for b in series if b.ts + params.bucket_s <= lim]


@dataclass
class RuleResult:
    rule: str
    level: int | None          # None = the rule has no opinion yet (not enough data)
    clear: bool                # the rule positively shows that the problem is gone / absent
    reason: str = ""
    progress: dict | None = None   # {text, value, needed, unit, counting}: how far the rule is from raising (for the UI)
    extra: dict | None = None      # live numbers of the responsive balance (state, T_D, D, x+, x-)


def _dur(minutes: float) -> str:
    m = float(minutes)
    if m < 120:
        return f"{m:.0f} min"
    h = m / 60.0
    return f"{h:.0f} h" if abs(h - round(h)) < 0.05 else f"{h:.1f} h"


def hhmm(minutes: int) -> str:
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


def _prog(text, value=None, needed=None, unit="min", counting=False):
    return {"text": text, "value": value, "needed": needed, "unit": unit, "counting": counting}


# ---------------------------------------------------------------- strong zone: balance
def balance_rule(zone, final: list, params: ZoneParams) -> RuleResult:
    p = params.for_zone(zone.id)
    run, last_ts, bridge = 0, None, p.bridge_min * 60
    for b in reversed(final):
        if not b.complete:
            continue
        if last_ts is not None and last_ts - b.ts > bridge + p.bucket_s:
            break                                        # a long hole: do not join two separate episodes
        if not b.exceed:
            break
        run += 1
        last_ts = b.ts
    minutes = run * p.bucket_s / 60.0
    comp = [b for b in final if b.complete]
    need = max(1, int(round(p.resolve_min * 60 / p.bucket_s)))
    clear = len(comp) >= need and not any(b.exceed for b in comp[-need:])
    if minutes >= p.confirm_min:
        return RuleResult("balance", CONFIRMED, False, f"{minutes:.0f} min of unaccounted flow above the meter tolerance in a zone without outlets",
                          _prog(f"Above threshold for {_dur(minutes)}: confirmed (limit {_dur(p.confirm_min)})", minutes, p.confirm_min))
    if minutes >= p.suspect_min:
        return RuleResult("balance", SUSPECTED, False, f"{minutes:.0f} min of unaccounted flow above the meter tolerance in a zone without outlets",
                          _prog(f"Suspected: above threshold {_dur(minutes)} of {_dur(p.confirm_min)} \u2192 confirmed", minutes, p.confirm_min, counting=True))
    if not comp:
        return RuleResult("balance", None, False, "", _prog("Waiting for complete data (no complete minute yet)"))
    if minutes > 0:
        return RuleResult("balance", 0, clear, "", _prog(f"Above threshold {_dur(minutes)} of {_dur(p.suspect_min)} \u2192 suspected (confirmed at {_dur(p.confirm_min)})", minutes, p.suspect_min, counting=True))
    return RuleResult("balance", 0, clear, "", _prog(f"Balance within threshold (suspected after {_dur(p.suspect_min)} above it, confirmed after {_dur(p.confirm_min)})", 0, p.suspect_min))


# ---------------------------------------------------------------- weak zone: minimum night flow
def _night_window(now: int, p: ZoneParams):
    """(start, end) of the most recent night window that has started (local time = UTC + tz_offset)."""
    off = p.tz_offset_min * 60
    local = now + off
    day = local - (local % 86400)
    start, end = day + p.night_start_min * 60, day + p.night_end_min * 60
    if local < start:                                     # tonight has not started yet: use last night
        start, end = start - 86400, end - 86400
    return start - off, end - off


def night_rule(zone, final: list, now: int, params: ZoneParams) -> RuleResult:
    p = params.for_zone(zone.id)
    ws, we = _night_window(now, p)
    inw = [b for b in final if ws <= b.ts < we]
    expected = max(1, int((min(we, now) - ws) // p.bucket_s))
    comp = [b for b in inw if b.complete]
    over = now < we
    rng = f"{hhmm(p.night_start_min)}-{hhmm(p.night_end_min)}"
    if len(comp) < p.night_min_buckets or len(comp) / expected < p.night_min_coverage:
        txt = (f"Night flow: window {rng} running, {len(comp)} of {p.night_min_buckets} complete minutes collected" if over
               else f"Night flow: outside window ({rng}); last night had too little complete data to judge")
        return RuleResult("night_flow", None, False, "", _prog(txt, len(comp), p.night_min_buckets, counting=False))      # not enough night data to decide anything (never "normal")
    # smoothing: mean of `night_smooth` CONSECUTIVE complete buckets; the minimum over the window is the minimum night flow
    best = None
    for i in range(len(inw) - p.night_smooth + 1):
        win = inw[i:i + p.night_smooth]
        if all(b.complete for b in win):
            m = sum(b.unacc_l for b in win) / (p.night_smooth * p.bucket_s / 60.0)
            thr = sum(b.thr_l for b in win) / (p.night_smooth * p.bucket_s / 60.0)
            if best is None or m < best[0]:
                best = (m, thr)
    if best is None:
        return RuleResult("night_flow", None, False, "", _prog(f"Night flow: window {rng}, waiting for {p.night_smooth} complete minutes in a row"))
    mnf, thr = best
    if mnf > thr:
        what = f"minimum night flow {mnf:.2f} L/min (threshold {thr:.2f}) between {p.night_start_min // 60:02d}:{p.night_start_min % 60:02d} and {p.night_end_min // 60:02d}:{p.night_end_min % 60:02d}"
        return RuleResult("night_flow", SUSPECTED if over else CONFIRMED, False, what,
                          _prog(f"Night flow {mnf:.2f} L/min is above the threshold {thr:.2f} ({rng})", mnf, thr, "L/min", counting=True))
    txt = (f"Night flow: window {rng} running, minimum so far {mnf:.2f} L/min is within the threshold {thr:.2f}" if over
           else f"Night flow: outside window ({rng}); last night's minimum {mnf:.2f} L/min was within the threshold")
    return RuleResult("night_flow", 0, not over, "", _prog(txt, mnf, thr, "L/min"))           # a finished night with no flow floor clears; a night in progress only holds


# ---------------------------------------------------------------- weak zone: continuous flow
def continuous_rule(zone, final: list, params: ZoneParams) -> RuleResult:
    p = params.for_zone(zone.id)
    if not final:
        return RuleResult("continuous_flow", None, False, "", _prog("Continuous flow: no data yet"))
    minutes = p.bucket_s / 60.0
    span = best_span = 0
    complete = flowing_total = 0
    broke = False
    for b in reversed(final):
        if b.complete:
            if (b.q_in_l or 0.0) / minutes <= p.continuous_min_lpm:
                broke = True                              # the flow dropped to (about) zero here: the run ends
                break
            complete += 1
        span += 1
        if complete / span >= p.continuous_min_coverage:
            best_span = span
    hours = best_span * p.bucket_s / 3600.0
    run_min, need_min, conf_min = hours * 60.0, p.continuous_suspect_h * 60.0, p.continuous_confirm_h * 60.0
    newest = next((b for b in reversed(final) if b.complete), None)
    zero_now = newest is not None and (newest.q_in_l or 0.0) / minutes <= p.continuous_min_lpm
    if hours >= p.continuous_confirm_h:
        return RuleResult("continuous_flow", CONFIRMED, False, f"flow has not dropped to zero for {hours:.1f} h", _prog(f"Continuous flow {_dur(run_min)}: confirmed (limit {_dur(conf_min)})", run_min, conf_min))
    if hours >= p.continuous_suspect_h:
        return RuleResult("continuous_flow", SUSPECTED, False, f"flow has not dropped to zero for {hours:.1f} h", _prog(f"Continuous flow {_dur(run_min)} of {_dur(conf_min)} \u2192 confirmed", run_min, conf_min, counting=True))
    if newest is None:
        return RuleResult("continuous_flow", None, False, "", _prog("Continuous flow: waiting for complete data"))
    txt = (f"Continuous flow {_dur(run_min)} of {_dur(need_min)} \u2192 suspected (confirmed at {_dur(conf_min)})" if run_min > 0
           else f"Continuous flow: none right now (suspected after {_dur(need_min)} without a stop, confirmed after {_dur(conf_min)})")
    return RuleResult("continuous_flow", 0, zero_now or broke, "", _prog(txt, run_min, need_min, counting=run_min >= 0.5 * need_min))


# ---------------------------------------------------------------- aggregation
@dataclass
class Evaluation:
    level: int
    status: str            # normal | suspected | confirmed | not_monitored | incomplete
    reason: str
    results: list
    last_bucket: ZoneBucket | None
    meter_fault_hint: bool = False


def next_level(prev: int, results: list):
    """Hysteresis: go up as soon as any rule says so; go down only when every rule shows the problem is gone."""
    lv = max([r.level or 0 for r in results] + [0])
    if lv > prev:
        return lv
    if results and all(r.clear for r in results):
        return 0
    return prev if prev >= lv else lv


def evaluate_zone(zone, series: list, now: int, prev_level: int, params: ZoneParams, fast: list | None = None) -> Evaluation:
    """`fast` = the zone's FINAL short windows (balance.final_fast).  Given, a strong zone is judged by the responsive WLDS balance rule;
    without it (old callers) by the minute-based balance rule."""
    from .zones import NONE, STRONG
    from .balance import PAUSED, balance_wlds
    p = params.for_zone(zone.id)
    if zone.cls == NONE or zone.in_meter is None:
        return Evaluation(0, "not_monitored", zone.reason, [], None)
    final = final_only(series, now, p)
    if zone.cls == STRONG:
        rules = [balance_wlds(zone, fast, now, p)] if fast is not None else [balance_rule(zone, final, p)]
    else:
        rules = [night_rule(zone, final, now, p), continuous_rule(zone, final, p)]
    level = next_level(prev_level, rules)
    top = max(rules, key=lambda r: (r.level or 0))
    reason = top.reason if (top.level or 0) >= level and level > 0 else (next((r.reason for r in rules if r.level == level and r.reason), "") if level else "")
    status = LEVEL_NAME[level]
    if level == 0:
        tail = [b for b in final if b.ts >= now - p.incomplete_after_min * 60]
        if fast is not None and zone.cls == STRONG:        # responsive balance: paused as soon as a meter has no complete window (not after 15 minutes)
            if (rules[0].extra or {}).get("state") == PAUSED:
                status = "incomplete"
        elif not any(b.complete for b in tail):          # no complete recent bucket (or no bucket at all): the zone is NOT known to be fine
            status = "incomplete"
    hint = sum(1 for b in final[-10:] if b.fault_hint) >= 8
    last = next((b for b in reversed(final) if b.complete), final[-1] if final else None)
    return Evaluation(level, status, reason, rules, last, hint)
