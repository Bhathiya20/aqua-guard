"""Per-node usage profiles: the duration detector (forgotten tap / continuous flow / night flow).  Pure logic, no database.

Why this exists next to the zone balance: the balance (analysis/balance.py) finds water that LEAVES the pipe between two meters, but a tap left
open at the end of a branch is a perfectly balanced flow.  It is found by how LONG one node has been flowing.  The two detectors are independent:
neither suppresses the other.

Flow state of one node (hysteresis, so a noisy flow rate cannot reset the timer):
  idle -> flowing   when a sample is >= flow_on_lpm
  flowing -> idle   when flow stayed below flow_off_lpm for no_flow_s (flow between off and on keeps the run alive)
T_flow = how long the current run has lasted.  Status from T_flow:
  NO_FLOW | NORMAL | PROLONGED_USAGE (T_flow >= T_lim, warning) | CONTINUOUS_ABNORMAL_USAGE (T_flow >= T_max, critical)
  | NIGHT_FLOW (any confirmed flow during quiet hours, critical)  | UNKNOWN (no fresh data: never "normal")
An "allowed long-use schedule" (e.g. a laboratory on weekdays 08:00-17:00) switches the T_lim / T_max rule off while it is active; when it ends the
clock restarts from that moment.  "24 h use allowed" switches the night rule off for that node.
"""
from dataclasses import dataclass

# Built-in presets (seconds).  "default" is only the factory value: the owner edits the real defaults on the settings page.
PRESETS = {
    "default":    {"t_lim_s": 900, "t_max_s": 1800},                       # 15 min / 30 min
    "washroom":   {"t_lim_s": 90, "t_max_s": 240},
    "canteen":    {"t_lim_s": 300, "t_max_s": 600},
    "laboratory": {"t_lim_s": 7 * 3600, "t_max_s": int(7.5 * 3600)},
    "custom":     {},
}
PRESET_LABEL = {"default": "Default", "washroom": "Washroom", "canteen": "Canteen", "laboratory": "Laboratory", "custom": "Custom"}

FACTORY = {
    "preset": "default", "t_lim_s": 900, "t_max_s": 1800,
    "flow_on_lpm": 0.3, "flow_off_lpm": 0.15, "no_flow_s": 10,
    "quiet_start": "22:00", "quiet_end": "05:00", "quiet_confirm_s": 60,
    "allow_24h": False, "schedule": [],                                     # schedule: [{"days": [0..6] (Mon=0), "start": "08:00", "end": "17:00"}]
}
FIELDS = tuple(FACTORY)
MAX_GAP_S = 120          # no sample for this long while flowing: the run is over (we cannot know what happened)
FRESH_S = 30             # newest sample older than this: status UNKNOWN

DEMO_T_LIM_S, DEMO_T_MAX_S, DEMO_QUIET_CONFIRM_S, DEMO_MAX_S = 30, 60, 10, 3600


def hm(s) -> int | None:
    try:
        h, m = str(s).split(":")
        h, m = int(h), int(m)
        return h * 60 + m if 0 <= h < 24 and 0 <= m < 60 else None
    except (ValueError, AttributeError):
        return None


def validate(p: dict) -> list[str]:
    """Human-readable problems of a full profile (empty list = fine)."""
    err = []
    if p.get("preset") not in PRESETS:
        err.append("Unknown preset.")
    for k in ("t_lim_s", "t_max_s", "no_flow_s", "quiet_confirm_s"):
        if not isinstance(p.get(k), (int, float)) or isinstance(p.get(k), bool) or p[k] <= 0 or p[k] > 7 * 86400:
            err.append(f"{k} must be a positive number of seconds.")
    if not err and p["t_max_s"] <= p["t_lim_s"]:
        err.append("T_max must be longer than T_lim.")
    for k in ("flow_on_lpm", "flow_off_lpm"):
        if not isinstance(p.get(k), (int, float)) or isinstance(p.get(k), bool) or not 0 < p[k] <= 30:
            err.append(f"{k} must be between 0 and 30 L/min.")
    if not err and p["flow_off_lpm"] > p["flow_on_lpm"]:
        err.append("Flow-off must not be above flow-on.")
    for k in ("quiet_start", "quiet_end"):
        if hm(p.get(k)) is None:
            err.append(f"{k} must look like 22:00.")
    if not isinstance(p.get("allow_24h"), bool):
        err.append("allow_24h must be true or false.")
    sch = p.get("schedule")
    if not isinstance(sch, list) or len(sch) > 14:
        err.append("The allowed schedule must be a list of at most 14 entries.")
    else:
        for e in sch:
            ok = isinstance(e, dict) and isinstance(e.get("days"), list) and e["days"] and all(isinstance(d, int) and 0 <= d <= 6 for d in e["days"]) \
                and hm(e.get("start")) is not None and hm(e.get("end")) is not None and hm(e["start"]) < hm(e["end"])
            if not ok:
                err.append("Each schedule entry needs days (0=Mon..6=Sun) and a start earlier than its end (same day).")
                break
    return err


def resolve(defaults: dict, override: dict | None) -> dict:
    """Effective profile: global defaults, then the preset's limits, then the fields the node edited itself."""
    out = dict(FACTORY)
    out.update({k: v for k, v in (defaults or {}).items() if k in FIELDS})
    if override:
        pre = override.get("preset")
        if pre in PRESETS and pre not in ("default", "custom"):
            out.update(PRESETS[pre])
        out["preset"] = pre if pre in PRESETS else out["preset"]
        out.update({k: v for k, v in (override.get("fields") or {}).items() if k in FIELDS})
    return out


def minimal_override(defaults: dict, submitted: dict) -> dict:
    """What to store for a node: its preset plus only the fields that differ from what the preset + defaults already give."""
    pre = submitted.get("preset", "custom")
    base = resolve(defaults, {"preset": pre, "fields": {}})
    return {"preset": pre, "fields": {k: submitted[k] for k in FIELDS if k != "preset" and k in submitted and submitted[k] != base[k]}}


def demo_profile(p: dict, quiet_from: int | None, quiet_to: int | None) -> dict:
    """The short limits of the water-test demo (T_lim 30 s, T_max 60 s); quiet hours = [now, demo end] only when asked for."""
    q = dict(p, t_lim_s=DEMO_T_LIM_S, t_max_s=DEMO_T_MAX_S, quiet_confirm_s=DEMO_QUIET_CONFIRM_S, schedule=[], allow_24h=False)
    q["_quiet_epoch"] = (quiet_from, quiet_to) if quiet_from else None
    if not quiet_from:                                   # no night test: quiet hours must not interfere with the long-use test
        q["allow_24h"] = True
    return q


# ---------------------------------------------------------------- time helpers (site local time = UTC + off minutes)
def _local(now: float, off: int) -> float:
    return now + off * 60


def quiet_window(now: float, p: dict, off: int):
    """(inside, start_epoch) of the quiet window that contains `now` (start_epoch None when outside)."""
    ep = p.get("_quiet_epoch")
    if ep:
        return (ep[0] <= now < ep[1]), (ep[0] if ep[0] <= now < ep[1] else None)
    qs, qe = hm(p["quiet_start"]), hm(p["quiet_end"])
    ln = _local(now, off)
    m = int(ln % 86400) // 60
    inside = (qs <= m < qe) if qs < qe else (m >= qs or m < qe) if qs != qe else False
    if not inside:
        return False, None
    day0 = ln - ln % 86400
    start_local = day0 + qs * 60
    if start_local > ln:
        start_local -= 86400
    return True, start_local - off * 60


def _weekday(local_t: float) -> int:
    return int(local_t // 86400 + 3) % 7                  # 1970-01-01 was a Thursday (3 with Monday = 0)


def schedule_state(now: float, p: dict, off: int):
    """(inside, last_end_epoch): is an allowed long-use window active now, and when did the latest one end (None when none in the last 8 days)."""
    sch = p.get("schedule") or []
    if not sch:
        return False, None
    ln = _local(now, off)
    m = int(ln % 86400) // 60
    inside, last_end = False, None
    for back in range(0, 9):
        day0 = ln - ln % 86400 - back * 86400
        wd = _weekday(day0)
        for e in sch:
            if wd not in e["days"]:
                continue
            s, en = hm(e["start"]), hm(e["end"])
            if back == 0 and s <= m < en:
                inside = True
            end_local = day0 + en * 60
            if end_local <= ln:
                ep = end_local - off * 60
                last_end = ep if last_end is None else max(last_end, ep)
    return inside, last_end


# ---------------------------------------------------------------- run tracking (incremental, restart safe)
@dataclass
class Run:
    flowing: bool = False
    flow_since: float | None = None
    last_flow_at: float | None = None
    processed_until: float = 0.0
    peak_lpm: float = 0.0


def advance(run: Run, samples, p: dict) -> Run:
    """Feed samples [(t, flow_lpm)] newer than run.processed_until, in time order."""
    on, off, nf = p["flow_on_lpm"], p["flow_off_lpm"], p["no_flow_s"]
    for t, q in samples:
        if t <= run.processed_until:
            continue
        if run.flowing and run.last_flow_at is not None and t - run.last_flow_at > MAX_GAP_S and t - run.processed_until > MAX_GAP_S:
            run.flowing, run.flow_since, run.last_flow_at, run.peak_lpm = False, None, None, 0.0        # data gap: the old run is over
        if q >= on and not run.flowing:
            run.flowing, run.flow_since, run.last_flow_at, run.peak_lpm = True, t, t, q
        elif run.flowing and q >= off:
            run.last_flow_at = t                                 # between off and on still counts: noisy flow keeps the timer
            run.peak_lpm = max(run.peak_lpm, q)
        elif run.flowing and t - (run.last_flow_at or t) >= nf:
            run.flowing, run.flow_since, run.last_flow_at, run.peak_lpm = False, None, None, 0.0
        run.processed_until = t
    return run


def classify(run: Run, now: float, p: dict, off_min: int) -> dict:
    """Status of the node right now.  -> {status, tFlowS, severity, next:{what, inS}|None, allowed, quiet}"""
    fresh = run.processed_until > 0 and now - run.processed_until <= FRESH_S
    if not fresh:
        return {"status": "UNKNOWN", "tFlowS": None, "severity": None, "next": None, "allowed": False, "quiet": False}
    if not run.flowing:
        return {"status": "NO_FLOW", "tFlowS": 0, "severity": None, "next": None, "allowed": False, "quiet": False}
    ref = max(now, run.processed_until)
    t_flow = ref - run.flow_since
    quiet, q_start = quiet_window(now, p, off_min)
    in_sched, last_end = schedule_state(now, p, off_min)
    night = bool(quiet and not p["allow_24h"])
    out = {"tFlowS": int(t_flow), "allowed": in_sched, "quiet": night, "next": None}
    if night:
        t_q = ref - max(run.flow_since, q_start)
        if t_q >= p["quiet_confirm_s"]:
            return dict(out, status="NIGHT_FLOW", severity="critical")
        out["next"] = {"what": "night flow", "inS": int(p["quiet_confirm_s"] - t_q)}
    if in_sched:
        return dict(out, status="NORMAL", severity=None)       # allowed long use: the T_lim / T_max rule is off
    start = run.flow_since if last_end is None else max(run.flow_since, last_end)
    t_eff = ref - start
    if t_eff >= p["t_max_s"]:
        return dict(out, status="CONTINUOUS_ABNORMAL_USAGE", severity="critical", next=None)
    cand = out["next"]
    if t_eff >= p["t_lim_s"]:
        n = {"what": "T_max", "inS": int(p["t_max_s"] - t_eff)}
        return dict(out, status="PROLONGED_USAGE", severity="warning", next=n if cand is None or n["inS"] < cand["inS"] else cand)
    n = {"what": "T_lim", "inS": int(p["t_lim_s"] - t_eff)}
    return dict(out, status="NORMAL", severity=None, next=n if cand is None or n["inS"] < cand["inS"] else cand)
