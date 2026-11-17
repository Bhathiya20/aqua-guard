"""The demo clock and the unified time model.  Pure logic: no database, no Flask, no real clock.

DEMO CLOCK (demo only).  An administrator can make every time-based rule evaluate "now" as if it were NIGHT (inside the quiet hours) or DAY
(outside them), for at most one hour.  It changes ONLY how the rules read the clock: stored timestamps, the real clock, the gateway and all
readings are never touched.

TIME MODEL.  One source of truth for "when is it quiet":
  global default   = the quiet hours of the usage defaults (Usage settings page), shared by node usage profiles and zone night-flow rules
  zone override    = optional, per zone (Leak Detection -> zone -> Settings); a value that is not overridden is inherited
"""
from dataclasses import dataclass

AUTO, DAY, NIGHT = "auto", "day", "night"
MODES = (AUTO, DAY, NIGHT)
MAX_S = 3600                                        # the demo clock switches itself off after one hour

# zone time rules and their factory values; quiet hours are NOT here: they come from the usage defaults
ZONE_TIME_FACTORY = {"continuous_suspect_h": 6.0, "continuous_confirm_h": 12.0}
ZONE_TIME_FIELDS = ("quiet_start", "quiet_end", "continuous_suspect_h", "continuous_confirm_h")


@dataclass(frozen=True)
class Clock:
    mode: str = AUTO
    since: float | None = None                      # epoch at which the forced state began (the start of the simulated night)
    until: float | None = None

    @property
    def night(self) -> bool:
        return self.mode == NIGHT

    @property
    def day(self) -> bool:
        return self.mode == DAY

    @property
    def forced(self) -> bool:
        return self.mode in (DAY, NIGHT)


REAL = Clock()


def active(mode: str | None, since, until, now: float) -> Clock:
    """The clock in force at `now` from the stored values (an expired or unknown setting means the real clock)."""
    if mode not in (DAY, NIGHT) or since is None or until is None or until <= now:
        return REAL
    return Clock(mode, float(since), float(until))


def hm(s) -> int | None:
    try:
        h, m = str(s).split(":")
        h, m = int(h), int(m)
        return h * 60 + m if 0 <= h < 24 and 0 <= m < 60 else None
    except (ValueError, AttributeError):
        return None


def hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def resolve_zone_time(quiet_start: str, quiet_end: str, override: dict | None) -> dict:
    """Effective time rules of one zone and where each value comes from.
    -> {"values": {quiet_start, quiet_end, continuous_suspect_h, continuous_confirm_h}, "source": {field: "default"|"zone"}}"""
    o = override or {}
    vals = {"quiet_start": quiet_start, "quiet_end": quiet_end, **ZONE_TIME_FACTORY}
    src = {k: "default" for k in vals}
    for k in ZONE_TIME_FIELDS:
        if k in o and o[k] is not None:
            vals[k] = o[k]
            src[k] = "zone"
    return {"values": vals, "source": src}


def validate_zone_time(sub: dict) -> list[str]:
    """Problems of a submitted partial override (empty list = fine)."""
    err = []
    for k in ("quiet_start", "quiet_end"):
        if k in sub and hm(sub[k]) is None:
            err.append(f"{k} must look like 22:00.")
    if "quiet_start" in sub and "quiet_end" in sub and hm(sub["quiet_start"]) is not None and hm(sub["quiet_start"]) == hm(sub["quiet_end"]):
        err.append("Quiet hours must not start and end at the same time.")
    for k in ("continuous_suspect_h", "continuous_confirm_h"):
        if k in sub and (not isinstance(sub[k], (int, float)) or isinstance(sub[k], bool) or not 0.05 <= sub[k] <= 72):
            err.append(f"{k} must be between 0.05 and 72 hours.")
    return err
