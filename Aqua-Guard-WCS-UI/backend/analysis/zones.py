"""Zone partition of a validated network (brief section 7.1).  Pure Python.

A zone = the pipe segments and outlets downstream of one inflow meter (or of the source) and upstream of the next meters.
  * strong leak-monitored : has an inflow meter and NO outlets      -> any sustained unaccounted flow is a leak or a meter fault
  * weak   leak-monitored : has an inflow meter AND outlets          -> unaccounted flow = real use + possible leak (night-flow / continuous-flow rules)
  * not monitored         : no inflow meter (the part upstream of the first sensor)  -> suggestions say where to add a node
Mass balance per zone:  Q_unaccounted = Q_in - sum(Q_out), Q_out = the meters that start the zones directly downstream.
"""
from dataclasses import dataclass, field

from pipeline.model import Network, node_key
from pipeline.validate import build_tree

STRONG, WEAK, NONE = "strong", "weak", "not_monitored"


@dataclass(frozen=True)
class Meter:
    part_id: str
    key: tuple                     # ("flow", number)
    pipe: str
    pos: float
    node_ref: int | None
    label: str


@dataclass
class Zone:
    id: str
    name: str
    in_meter: Meter | None = None
    out_meters: list = field(default_factory=list)
    parts: list = field(default_factory=list)          # graph part ids (excluding pipes and outlets)
    pipes: list = field(default_factory=list)          # [(pipe id, fraction of the pipe inside this zone)]
    outlets: list = field(default_factory=list)
    parent: str | None = None
    cls: str = NONE
    reason: str = ""
    equation: str = ""
    pipe_length: float = 0.0
    suggestions: list = field(default_factory=list)

    def meters(self):
        return ([self.in_meter] if self.in_meter else []) + list(self.out_meters)


@dataclass
class Partition:
    zones: list
    coverage: dict
    suggestions: list
    zone_of_part: dict

    def zone(self, zid):
        return next((z for z in self.zones if z.id == zid), None)


def _pipe_weight(net: Network, pid: str, use_len: bool) -> float:
    raw = net.parts[pid].raw
    return float(raw.get("lengthM") or 0) if use_len else 1.0


def partition(net: Network) -> Partition:
    tree = build_tree(net)
    if tree.root is None:
        return Partition([], {"pipeTotal": 0, "pipeCovered": 0, "outletsTotal": 0, "outletsCovered": 0, "zones": 0, "monitored": 0, "unit": "pipes",
                              "text": "No network"}, [], {})
    metered: dict = {}                                  # pipe id -> [Meter] in flow order (nodes carry pos from the pipe inlet)
    for nid in net.order:
        n = net.nodes.get(nid)
        if n is None or n.raw.get("pipe") not in net.parts:
            continue
        m = Meter(nid, node_key(n), n.raw["pipe"], float(n.raw.get("pos", 0.5)), n.raw.get("nodeRef"), n.label)
        metered.setdefault(m.pipe, []).append(m)
    for ms in metered.values():
        ms.sort(key=lambda m: m.pos)
    use_len = all(float(net.parts[p].raw.get("lengthM") or 0) > 0 for p in net.order if p in net.parts and net.parts[p].kind == "pipe") and \
        any(net.parts[p].kind == "pipe" for p in net.order if p in net.parts)

    zones: list = []
    zone_of_part: dict = {}
    root = Zone("zone:source", f"{net.label(tree.root)} (before the first sensor)")
    zones.append(root)

    def new_zone(parent: Zone, m: Meter) -> Zone:
        z = Zone(f"zone:{m.key[0]}{m.key[1]}", f"After {m.label}", in_meter=m, parent=parent.id)
        parent.out_meters.append(m)
        zones.append(z)
        return z

    def visit(pid: str, zone: Zone):
        p = net.parts[pid]
        exit_zone = zone
        zone_of_part[pid] = zone.id
        if p.kind == "pipe":
            ms = metered.get(pid, [])
            prev, cur = 0.0, zone
            for m in ms:
                cur.pipes.append((pid, m.pos - prev))
                cur = new_zone(cur, m)
                prev = m.pos
            cur.pipes.append((pid, 1.0 - prev))
            exit_zone = cur
        elif p.kind == "outlet":
            zone.outlets.append(pid)
        else:
            zone.parts.append(pid)
        for ch in tree.children.get(pid, []):
            visit(ch, exit_zone)

    visit(tree.root, root)

    # ---- classification, equations, lengths
    for z in zones:
        z.pipe_length = sum(_pipe_weight(net, pid, use_len) * frac for pid, frac in z.pipes)
        if z.in_meter is None:
            z.cls = NONE
            z.reason = "No sensor measures the water entering this part of the network, so leaks here cannot be told apart from normal use."
        elif z.outlets:
            z.cls = WEAK
            z.reason = "Has a sensor at its inlet and outlets inside it: unaccounted flow is real use plus any leak, so night-flow and continuous-flow rules are used."
        else:
            z.cls = STRONG
            z.reason = "Has a sensor at its inlet and no outlets: any sustained unaccounted flow is a leak or a meter fault."
        qin = f"Q[{z.in_meter.label}]" if z.in_meter else "Q_in (not measured)"
        qout = " + ".join(f"Q[{m.label}]" for m in z.out_meters)
        z.equation = f"Q_unaccounted({z.name}) = {qin}" + (f" - ({qout})" if qout else "")

    suggestions = _suggestions(net, tree, zones, zone_of_part)
    for s in suggestions:
        zone = next((z for z in zones if z.id == s["zone"]), None)
        if zone:
            zone.suggestions.append(s)

    # ---- coverage: what part of the pipes / outlets sits in a zone that has an inflow meter
    mon = [z for z in zones if z.in_meter]
    pipe_total = sum(z.pipe_length for z in zones)
    pipe_cov = sum(z.pipe_length for z in mon)
    out_total = sum(len(z.outlets) for z in zones)
    out_cov = sum(len(z.outlets) for z in mon)
    nz, nm = len(zones), len(mon)
    cov = {"pipeTotal": round(pipe_total, 3), "pipeCovered": round(pipe_cov, 3), "pipeFraction": round(pipe_cov / pipe_total, 3) if pipe_total else 0.0,
           "unit": "m" if use_len else "pipes", "outletsTotal": out_total, "outletsCovered": out_cov,
           "outletFraction": round(out_cov / out_total, 3) if out_total else 0.0, "zones": nz, "monitored": nm,
           "strong": sum(1 for z in zones if z.cls == STRONG), "weak": sum(1 for z in zones if z.cls == WEAK),
           "text": f"{nm} of {nz} zones leak-monitored; {out_cov} of {out_total} outlets covered"}
    return Partition(zones, cov, suggestions, zone_of_part)


def _suggestions(net: Network, tree, zones, zone_of_part) -> list:
    out = []
    metered = {m.pipe for z in zones for m in z.meters()}        # a pipe that already carries a node needs no suggestion

    def downstream_outlets(pid):
        res, stack = [], [pid]
        while stack:
            cur = stack.pop()
            if net.parts[cur].kind == "outlet":
                res.append(cur)
            stack.extend(tree.children.get(cur, []))
        return res

    def branch_text(pipe_id, outlets):
        parent = tree.parent.get(pipe_id)
        after = net.label(parent) if parent else "the supply"
        if outlets:
            names = [net.label(o) for o in outlets]
            what = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
            return f"Add a node on {net.label(pipe_id)} (the pipe after {after}) to monitor {what}."
        return f"Add a node on {net.label(pipe_id)} (the pipe after {after}) to monitor that branch."

    def first_pipes(pid):
        """First pipe on every branch below `pid` (stop descending at it)."""
        res = []
        for ch in tree.children.get(pid, []):
            if net.parts[ch].kind == "pipe":
                res.append(ch)
            else:
                res.extend(first_pipes(ch))
        return res

    for z in zones:
        if z.cls == NONE:
            # a not-monitored zone starts at the source: the first pipe on each branch is where a node closes the gap
            for fp in first_pipes(tree.root):
                if zone_of_part.get(fp) == z.id:
                    out.append({"kind": "cover", "zone": z.id, "partId": fp, "text": branch_text(fp, downstream_outlets(fp))})
        elif z.cls == WEAK:
            # a weak zone with several unmetered branches: a node on a branch tells real use from a leak there
            zset = set(z.parts) | {p for p, _ in z.pipes}
            for pid in z.parts:
                if net.parts[pid].kind in ("tee", "cross"):
                    for ch in tree.children.get(pid, []):
                        for fp in ([ch] if net.parts[ch].kind == "pipe" else first_pipes(ch)):
                            if fp in zset and fp != (z.in_meter.pipe if z.in_meter else None) and fp not in metered and downstream_outlets(fp):
                                out.append({"kind": "refine", "zone": z.id, "partId": fp, "text": branch_text(fp, downstream_outlets(fp))})
    return out
