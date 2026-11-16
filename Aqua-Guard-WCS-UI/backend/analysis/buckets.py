"""Cumulative pulses -> volume per aligned time bucket, with an honest 'complete' flag.  Pure Python.

Input rows come from flow_samples: each sample already carries `liters` = delta_pulses / pulses-per-litre, where delta is the
DIFFERENCE OF THE CUMULATIVE COUNTER within the same boot ID (a new boot ID starts a new counter, so a reset never creates volume
or negative volume).  `t` is the MEASUREMENT time (boot anchor + node uptime), never the arrival time: a retransmitted sample
therefore lands in the bucket in which the water actually flowed.

A bucket is COMPLETE only when the node demonstrably covered all of it:
  * a sample within max_gap_s of the bucket start AND of its end, no hole longer than max_gap_s in between,
  * and (strict) no sample inside it that still has missing sequence numbers right before it (its volume may belong to an earlier time).
Anything else is INCOMPLETE and must be excluded from leak decisions: missing data is never treated as zero flow.
"""
from bisect import bisect_left
from dataclasses import dataclass


@dataclass(frozen=True)
class Sample:
    t: int                 # measurement time, unix seconds
    liters: float
    gap_before: int = 0    # sequence numbers missing right before this sample (volume may belong to an earlier time)
    src: str = "gw_epoch"  # gw_epoch (gateway NTP) | recv (backend receive time) | legacy
    boot: int = 0


@dataclass(frozen=True)
class Bucket:
    ts: int
    liters: float
    complete: bool
    confidence: str        # high | low (low = measurement time came from the backend clock)
    samples: int


def align(t: int, bucket_s: int) -> int:
    return t - (t % bucket_s)


def node_buckets(samples, start: int, end: int, bucket_s: int = 60, max_gap_s: float = 10.0, strict_gaps: bool = True) -> dict:
    """Buckets [ts, ts+bucket_s) for ts in [align(start), end).  `samples` must cover at least [start - max_gap_s, end + max_gap_s]."""
    ss = sorted(samples, key=lambda s: s.t)
    times = [s.t for s in ss]
    out = {}
    ts = align(start, bucket_s)
    while ts < end:
        lo, hi = bisect_left(times, ts), bisect_left(times, ts + bucket_s)
        inb = ss[lo:hi]
        if not inb:
            out[ts] = Bucket(ts, 0.0, False, "high", 0)
        else:
            ok = (inb[0].t - ts) <= max_gap_s and (ts + bucket_s - inb[-1].t) <= max_gap_s
            ok = ok and all((b.t - a.t) <= max_gap_s for a, b in zip(inb, inb[1:]))
            if strict_gaps and any(s.gap_before > 0 for s in inb):
                ok = False
            low = any(s.src not in ("gw_epoch",) for s in inb)
            out[ts] = Bucket(ts, sum(s.liters for s in inb), ok, "low" if low else "high", len(inb))
        ts += bucket_s
    return out


def fine_windows(samples, start: int, end: int, window_s: int = 5, max_gap_s: float = 6.0) -> dict:
    """Short aligned windows for the responsive balance rule (WLDS).

    Nodes report independently and in their own rhythm (about one frame every 2 s), so counting the samples that fall into a 5 s window would
    give 2 or 3 intervals per node (+-20 % of volume) and a false difference between two healthy meters.  Instead every sample is an INTERVAL
    (previous sample time, this sample time] holding `liters`, spread evenly over its length (the node only counts pulses, so within the interval
    the best estimate of the flow is constant).  A window's volume is the overlap of those intervals with it: the same wall-clock window for every
    node, whatever its phase.  The window is COMPLETE only if intervals cover all of it: an interval is invalid (a hole) when it is longer than
    `max_gap_s`, when it starts at another boot, or when sequence numbers are missing right before the sample (its volume may belong to an earlier time).
    Missing data is never zero flow."""
    ss = sorted(samples, key=lambda s: s.t)
    iv = []                                                     # (t0, t1, liters, low confidence) of VALID intervals, ordered
    for a, b in zip(ss, ss[1:]):
        if b.t <= a.t or (b.t - a.t) > max_gap_s or b.boot != a.boot or b.gap_before > 0:
            continue
        iv.append((a.t, b.t, b.liters, b.src not in ("gw_epoch",)))
    ends = [x[1] for x in iv]
    out = {}
    ts = align(start, window_s)
    while ts < end:
        te = ts + window_s
        i = bisect_left(ends, ts + 1e-9)                       # first interval ending after the window start
        vol = cov = 0.0
        low = False
        n = 0
        while i < len(iv) and iv[i][0] < te:
            a, b, lit, lo = iv[i]
            ov = min(b, te) - max(a, ts)
            if ov > 0:
                vol += lit * ov / (b - a)
                cov += ov
                low = low or lo
                n += 1
            i += 1
        out[ts] = Bucket(ts, vol, cov >= window_s - 1e-6, "low" if low else "high", n)
        ts = te
    return out
