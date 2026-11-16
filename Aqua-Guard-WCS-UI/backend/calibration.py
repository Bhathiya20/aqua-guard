"""Calibration maths (pure Python: no Flask, no database).

Everything is derived from CUMULATIVE pulse counters (per boot session), so lost packets never matter: a run is
    pulses = cum_end - cum_start        (same boot_id at both ends, otherwise the run is invalid)
    pulses/L = pulses / measured volume
Samples are plain dicts: {"boot": int, "seq": int, "cum": int, "measured": epoch_seconds}.
"""
from statistics import median

PPL_MIN, PPL_MAX = 300.0, 700.0        # values the wizard refuses outside of (pulses per litre)
CONFIRM_DIFF = 0.15                    # a change larger than this needs an extra confirmation
SPREAD_WARN_PCT = 5.0
RUN_DEVIATION_PCT = 3.0        # a node's pulses/L in one run vs ITS OWN mean over all valid runs
MIN_RUN_L = 2.0
RECOMMENDED_RUNS = (3, 5)
IDLE_FRESH_S = 15                      # the newest sample must be this recent for a node to count as idle at Start
STOP_TIMEOUT_S = 60


def _by_seq(samples):
    return sorted(samples, key=lambda s: s["seq"])


def idle_now(samples, now_epoch: float):
    """Is the node idle RIGHT NOW (used by Start)?  -> (ok, reason).  Idle = the two newest samples of the newest boot have
    increasing seq and identical cum_pulses, and the newest one was measured within IDLE_FRESH_S."""
    if not samples:
        return False, "no data yet"
    newest = max(samples, key=lambda s: (s["measured"], s["seq"]))
    same = _by_seq([s for s in samples if s["boot"] == newest["boot"]])
    if now_epoch - newest["measured"] > IDLE_FRESH_S:
        return False, "no recent data"
    if len(same) < 2:
        return False, "waiting for a second sample"
    a, b = same[-2], same[-1]
    if b["seq"] <= a["seq"]:
        return False, "waiting for new samples"
    if a["cum"] != b["cum"]:
        return False, "water is flowing"
    return True, ""


def idle_after(samples, boot: int, after_epoch: float):
    """Stop rule: among the NEW samples (measured strictly after `after_epoch`, same boot) find the first pair with increasing seq
    and identical cum_pulses.  -> the pair's first sample (cum_end) or None.  Judged on measured time, not arrival time."""
    new = _by_seq([s for s in samples if s["boot"] == boot and s["measured"] > after_epoch])
    for a, b in zip(new, new[1:]):
        if b["seq"] > a["seq"] and a["cum"] == b["cum"]:
            return a
    return None


def rebooted(samples, boot: int, since_epoch: float) -> bool:
    """A sample of ANOTHER boot measured since the run started means the node restarted during the run."""
    return any(s["boot"] != boot and s["measured"] >= since_epoch for s in samples)


# ------------------------------------------------------------------------------------------------ results
def ppl_x10(value: float) -> int:
    return int(round(value * 10))


def summarize(nodes: list, runs: list) -> dict:
    """nodes = [{"id", "label"}]; runs = [{"id", "idx", "status", "volumeL", "durationS", "nodes": {node_id: {"pulses", "valid", "reason"}}}].
    Returns per-run rows, per-node statistics and the list of warnings."""
    good = [r for r in runs if r["status"] == "done" and (r.get("volumeL") or 0) > 0]
    per_run, warnings = [], []
    labels = {n["id"]: n["label"] for n in nodes}
    for r in runs:
        row = {"id": r["id"], "idx": r["idx"], "status": r["status"], "volumeL": r.get("volumeL"), "durationS": r.get("durationS"), "reason": r.get("reason"), "nodes": {}}
        vol = r.get("volumeL") or 0
        for n in nodes:
            c = r["nodes"].get(n["id"])
            if c is None:
                continue
            ok = bool(c.get("valid")) and r["status"] == "done" and vol > 0 and c.get("pulses") is not None
            cell = {"pulses": c.get("pulses"), "valid": bool(c.get("valid")), "reason": c.get("reason"), "ppl": None, "flowLpm": None}
            if ok:
                cell["ppl"] = round(c["pulses"] / vol, 2)
                if r.get("durationS"):
                    cell["flowLpm"] = round(vol / (r["durationS"] / 60.0), 2)
            row["nodes"][n["id"]] = cell
        per_run.append(row)
        if r["status"] == "done" and 0 < vol < MIN_RUN_L:
            warnings.append({"code": "small_volume", "severity": "warning", "runId": r["id"], "nodeId": None,
                             "message": f"Run {r['idx']} used only {vol:g} L: use at least {MIN_RUN_L:g} L for a reliable result."})
    per_node = []
    for n in nodes:
        pts = [(r["nodes"][n["id"]]["pulses"], r["volumeL"], r["nodes"][n["id"]]["pulses"] / r["volumeL"]) for r in good
               if n["id"] in r["nodes"] and r["nodes"][n["id"]].get("valid") and r["nodes"][n["id"]].get("pulses") is not None]
        item = {"id": n["id"], "label": n["label"], "runs": len(pts), "meanPpl": None, "medianPpl": None, "weightedPpl": None, "spreadPct": None, "quality": "none"}
        if pts:
            vals = [p[2] for p in pts]
            mean = sum(vals) / len(vals)
            item["meanPpl"] = round(mean, 2)
            item["medianPpl"] = round(median(vals), 2)
            item["weightedPpl"] = round(sum(p[0] for p in pts) / sum(p[1] for p in pts), 2)
            if len(vals) >= 2 and mean > 0:
                item["spreadPct"] = round((max(vals) - min(vals)) / mean * 100.0, 2)
                item["quality"] = "good" if item["spreadPct"] <= 2.0 else "ok" if item["spreadPct"] <= SPREAD_WARN_PCT else "poor"
                if item["spreadPct"] > SPREAD_WARN_PCT:
                    warnings.append({"code": "spread", "severity": "warning", "runId": None, "nodeId": n["id"],
                                     "message": f"{n['label']}: the runs differ by {item['spreadPct']:.1f} % (limit {SPREAD_WARN_PCT:g} %). Repeat the runs, check for air, "
                                                "or delete the odd run."})
            else:
                item["quality"] = "unknown"
        per_node.append(item)
    # per run: each node's ratio = this run's pulses/L / that node's mean over all valid runs (sensors may legitimately differ from each other,
    # so nodes are never compared with one another, only with themselves)
    if len(good) >= 2:
        mean = {n["id"]: next(x for x in per_node if x["id"] == n["id"])["meanPpl"] for n in nodes}
        for row in per_run:
            if row["status"] != "done" or not row["volumeL"]:
                continue
            dev = {nid: c["ppl"] / mean[nid] - 1.0 for nid, c in row["nodes"].items() if c["ppl"] and mean.get(nid)}
            if not dev:
                continue
            lim = RUN_DEVIATION_PCT / 100.0
            big = {nid: d for nid, d in dev.items() if abs(d) > lim}
            if len(big) == len(dev) and (all(d > 0 for d in big.values()) or all(d < 0 for d in big.values())):
                way = "more" if next(iter(big.values())) > 0 else "fewer"
                alone = " (or air / a leak at the node)" if len(dev) == 1 else ""
                warnings.append({"code": "run_volume", "severity": "warning", "runId": row["id"], "nodeId": None,
                                 "message": f"Volume for run {row['idx']} may be wrong{alone}: " + ("every node" if len(dev) > 1 else labels[next(iter(dev))]) +
                                            f" counted {way} pulses per litre than its own average (by more than {RUN_DEVIATION_PCT:g} %). Re-measure the volume or repeat the run."})
            elif len(big) == 1 and len(dev) >= 2:
                nid, d = next(iter(big.items()))
                warnings.append({"code": "run_node", "severity": "warning", "runId": row["id"], "nodeId": nid,
                                 "message": f"{labels[nid]} run {row['idx']}: air or leak at this node ({d * 100:+.1f} % against its own average while the other nodes agree)."})
    if len(good) < RECOMMENDED_RUNS[0]:
        warnings.append({"code": "few_runs", "severity": "info", "runId": None, "nodeId": None,
                         "message": f"{len(good)} valid run{'s' if len(good) != 1 else ''} so far; {RECOMMENDED_RUNS[0]}-{RECOMMENDED_RUNS[1]} are recommended "
                                    f"(>= {MIN_RUN_L:g} L each: slow, medium and fast pours)."})
    return {"runs": per_run, "nodes": per_node, "warnings": warnings, "validRuns": len(good)}


def proposed_value(node_stat: dict, method: str):
    """-> the value for the node (pulses/L, float) or None.  method: 'weighted' (sum pulses / sum litres) | 'median' (of the runs)."""
    v = node_stat.get("medianPpl") if method == "median" else node_stat.get("weightedPpl")
    return v


def check_apply(value: float | None, current: float) -> tuple[str, str]:
    """-> (verdict, message).  verdict: 'ok' | 'confirm' (differs > 15 % from the current value) | 'refuse' (outside 300-700 or no value)."""
    if value is None:
        return "refuse", "no valid run for this node"
    if not PPL_MIN <= value <= PPL_MAX:
        return "refuse", f"{value:g} pulses/L is outside the allowed range {PPL_MIN:g}-{PPL_MAX:g}; check the runs (air, leak, wrong volume)."
    if current and abs(value - current) / current > CONFIRM_DIFF:
        return "confirm", f"{value:g} differs {abs(value - current) / current * 100:.0f} % from the current {current:g} pulses/L."
    return "ok", ""
