"""Responsive flow-balance detector of a strong zone (WLDS theory).  Pure functions, no clock, no DB.

Signed discrepancy per short aligned window:   D = Q_in - sum(Q_out)      (never |D|)

  NORMAL                   -x- <= D <= x+
  SUSPICIOUS               D > x+  (live counter T_D, shown at once; no alert)
  POSSIBLE_LEAK            D > x+ for T_leak or longer            -> alert level 1 ("suspected"); level 2 ("confirmed") after confirm_min
  MEASUREMENT_INCONSISTENCY  D < -x-   (outflow larger than inflow: a sensor / calibration / synchronisation fault or a wrong test - NOT a leak)
  PAUSED                   a meter has no complete data (never "normal", never zero)

Windows are 5 s long (see `ZoneParams.window_s`): short enough to react within seconds, long enough to hold several pulses and 2-3 reports of
every node, and `fine_windows` interpolates the cumulative volume, so the nodes' different report phases cancel.  Noise is handled by requiring
CONSECUTIVE windows: a state starts only when the newest two complete windows agree, and a run is broken by two normal windows in a row.
x+ and x- are the larger of the zone's fixed values (default 0.3 L/min, or the values learned from a no-leak test: mean +- 3 SD) and the meters'
proportional tolerance, so a high flow with its normal meter error is not a leak.
"""
from dataclasses import dataclass

from .rules import CONFIRMED, SUSPECTED, RuleResult, ZoneBucket, _dur, _prog

NORMAL, SUSPICIOUS, POSSIBLE_LEAK, INCONSISTENT, PAUSED = "normal", "suspicious", "possible_leak", "inconsistent", "paused"
MIN_CONSECUTIVE = 2          # newest complete windows that must agree before a state is shown
BREAK_AFTER = 2              # consecutive normal windows that end a run
STATE_TEXT = {NORMAL: "Normal", SUSPICIOUS: "Suspicious", POSSIBLE_LEAK: "Possible leak", INCONSISTENT: "Measurement inconsistency", PAUSED: "Leak check paused"}


@dataclass(frozen=True)
class FastWindow:
    ts: int
    q_in_l: float | None
    q_out_l: float | None
    d_l: float | None            # signed discrepancy in litres over the window
    xp_l: float | None           # +threshold in litres
    xm_l: float | None           # -threshold in litres (positive number)
    complete: bool
    confidence: str

    @property
    def cls(self) -> str:        # pos | neg | ok  (only meaningful when complete)
        if not self.complete:
            return "gap"
        return "pos" if self.d_l > self.xp_l else "neg" if self.d_l < -self.xm_l else "ok"


def fast_series(zone, node_windows: dict, p, t0: int, t1: int) -> list:
    """Per-window balance of a strong zone.  node_windows: node key -> {ts: Bucket} made with fine_windows(window_s)."""
    w = p.window_s
    minutes = w / 60.0
    involved = zone.meters()
    out = []
    ts = t0 - (t0 % w)
    while ts < t1:
        bs = [node_windows.get(m.key, {}).get(ts) for m in involved]
        complete = bool(involved) and zone.in_meter is not None and all(b is not None and b.complete for b in bs)
        low = any(b is not None and b.confidence == "low" for b in bs)
        if not complete:
            out.append(FastWindow(ts, None, None, None, None, None, False, "low" if low else "high"))
        else:
            qin = bs[0].liters
            qout = sum(b.liters for b in bs[1:])
            tol = sum(p.tolerance_by_node.get(m.key, p.tolerance) * b.liters for m, b in zip(involved, bs))
            out.append(FastWindow(ts, qin, qout, qin - qout, max(p.x_plus_lpm * minutes, tol), max(p.x_minus_lpm * minutes, tol), True, "low" if low else "high"))
        ts += w
    return out


def final_fast(series: list, now: int, p) -> list:
    lim = now - p.fast_grace_s
    return [b for b in series if b.ts + p.window_s <= lim]


def _run(final: list, cls: str, p):
    """The run of windows of class `cls` that ends at the newest complete window: (start_ts, end_ts) or None.
    Starts only when the newest MIN_CONSECUTIVE complete windows are all `cls`; incomplete windows are skipped (a hole longer than
    bridge_fast_s ends the run, so two separate episodes are never joined); BREAK_AFTER normal windows in a row end it."""
    comp = [b for b in final if b.complete]
    if len(comp) < MIN_CONSECUTIVE or any(b.cls != cls for b in comp[-MIN_CONSECUTIVE:]):
        return None
    start = end = comp[-1].ts
    ok_streak, last_ts = 0, None
    for b in reversed(final):
        if not b.complete:
            continue
        if last_ts is not None and last_ts - b.ts > p.bridge_fast_s + p.window_s:
            break
        last_ts = b.ts
        if b.cls == cls:
            start, ok_streak = b.ts, 0
        else:
            ok_streak += 1
            if ok_streak >= BREAK_AFTER or b.cls != "ok":
                break
    return start, end


def _paused(final: list, p) -> bool:
    tail = final[-p.pause_windows:]
    return not any(b.complete for b in tail)


def balance_wlds(zone, final: list, now: int, p) -> RuleResult:
    """The strong-zone rule.  `final` = final_fast(...).  The result's `extra` carries the live numbers for the page."""
    w = p.window_s
    mins = w / 60.0
    comp = [b for b in final if b.complete]
    last2 = comp[-MIN_CONSECUTIVE:]
    d_lpm = (sum(b.d_l for b in last2) / (len(last2) * mins)) if last2 else None
    qin = (sum(b.q_in_l for b in last2) / (len(last2) * mins)) if last2 else None
    qout = (sum(b.q_out_l for b in last2) / (len(last2) * mins)) if last2 else None
    xp = (sum(b.xp_l for b in last2) / (len(last2) * mins)) if last2 else p.x_plus_lpm
    xm = (sum(b.xm_l for b in last2) / (len(last2) * mins)) if last2 else p.x_minus_lpm
    extra = {"state": NORMAL, "tdS": 0, "tLeakS": p.t_leak_s, "dLpm": d_lpm, "qInLpm": qin, "qOutLpm": qout, "xPlusLpm": xp, "xMinusLpm": xm, "windowS": w,
             "completeWindows": len(comp), "finalWindows": len(final)}
    # resolve: enough complete windows without any exceedance (positive); inconsistency does not keep a leak alive
    need = max(1, int(round(p.resolve_min * 60 / w)))
    clear = len(comp) >= need and not any(b.cls == "pos" for b in comp[-need:])

    if not comp or _paused(final, p):
        extra["state"] = PAUSED
        return RuleResult("balance", None, False, "", _prog("Leak check paused: waiting for complete data from every meter of this zone"), extra)

    pos = _run(final, "pos", p)
    if pos:
        td = max(float(now - pos[0]), float(pos[1] + w - pos[0]))
        extra["tdS"] = round(td)
        if td >= p.confirm_min * 60:
            extra["state"] = POSSIBLE_LEAK
            return RuleResult("balance", CONFIRMED, False, f"inflow exceeded outflow by {d_lpm:.2f} L/min (threshold {xp:.2f}) for {_dur(td / 60)}",
                              _prog(f"D above x+ for {_dur(td / 60)}: confirmed (limit {_dur(p.confirm_min)})", td / 60, p.confirm_min), extra)
        if td >= p.t_leak_s:
            extra["state"] = POSSIBLE_LEAK
            return RuleResult("balance", SUSPECTED, False, f"inflow exceeded outflow by {d_lpm:.2f} L/min (threshold {xp:.2f}) for {td:.0f} s",
                              _prog(f"Possible leak: D above x+ for {_dur(td / 60) if td >= 120 else f'{td:.0f} s'} of {_dur(p.confirm_min)} → confirmed", td / 60, p.confirm_min, counting=True), extra)
        extra["state"] = SUSPICIOUS
        return RuleResult("balance", 0, False, "", _prog(f"Suspicious: D above x+ for {td:.0f} s of {p.t_leak_s:.0f} s → possible leak", td, p.t_leak_s, "s", counting=True), extra)

    neg = _run(final, "neg", p)
    if neg:
        td = max(float(now - neg[0]), float(neg[1] + w - neg[0]))
        extra["state"], extra["tdS"] = INCONSISTENT, round(td)
        return RuleResult("balance", 0, clear, "", _prog(f"Measurement inconsistency: outflow is larger than inflow by {-d_lpm:.2f} L/min (x- = {xm:.2f}) for {td:.0f} s", td, p.t_leak_s, "s"), extra)

    extra["state"] = NORMAL
    return RuleResult("balance", 0, clear, "", _prog(f"Balance within -{xm:.2f} .. +{xp:.2f} L/min (suspicious at once above it, possible leak after {p.t_leak_s:.0f} s, confirmed after {_dur(p.confirm_min)})", 0, p.t_leak_s, "s"), extra)


def learn_thresholds(series: list, p, floor_lpm: float | None = None) -> dict:
    """WLDS threshold learning from a synchronised NO-LEAK run:  x+ = max(floor, mean(D) + 3 SD),  x- = max(floor, 3 SD - mean(D)).
    `series` = fast_series windows of the run.  Returns the numbers and plain-language warnings (too short, no flow, looks like a leak / air)."""
    import statistics
    floor = p.x_plus_lpm if floor_lpm is None else floor_lpm
    mins = p.window_s / 60.0
    comp = [b for b in series if b.complete]
    d = [b.d_l / mins for b in comp]
    q = [b.q_in_l / mins for b in comp]
    n = len(d)
    mean = statistics.fmean(d) if n else 0.0
    sd = statistics.stdev(d) if n > 1 else 0.0
    flow = statistics.fmean(q) if n else 0.0
    warn = []
    if n < 24:
        warn.append(f"Only {n} complete windows ({n * p.window_s} s): run the test for at least 2 minutes with every node reporting.")
    if series and n / len(series) < 0.8:
        warn.append(f"Only {100 * n // len(series)} % of the windows are complete: a node was missing or lost packets.")
    if n and flow < 0.2:
        warn.append("There was almost no flow: open a tap so that water passes every meter of the zone.")
    if n and flow >= 0.2 and abs(mean) > 0.3 * flow:
        warn.append(f"The average difference ({mean:+.2f} L/min) is more than 30 % of the flow: check the calibration of the nodes, or air / a leak during the test.")
    xp, xm = max(floor, mean + 3 * sd), max(floor, 3 * sd - mean)
    if n and flow >= 0.2 and max(xp, xm) > 0.5 * flow:
        warn.append("The learned limits are large compared with the flow: the test was probably not a clean no-leak run.")
    return {"windows": n, "totalWindows": len(series), "windowS": p.window_s, "meanDLpm": round(mean, 4), "sdDLpm": round(sd, 4), "meanFlowLpm": round(flow, 3),
            "floorLpm": floor, "xPlusLpm": round(xp, 3), "xMinusLpm": round(xm, 3), "warnings": warn, "usable": n >= 24 and not any("almost no flow" in w for w in warn)}
