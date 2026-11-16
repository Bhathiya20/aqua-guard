"""Network validation: one connected tree flowing away from the single source.  Pure Python.

validate(net, known_nodes=None) -> list of messages {severity: error|warning, code, partId, message}; the network is usable for the
analysis only when there is no error.  Every message names the part the editor must highlight ("T junction 2: port out2 is not connected").
"""
from dataclasses import dataclass, field

from .model import KINDS, NODE_KIND, Network, node_key


def msg(severity, code, part_id, text):
    return {"severity": severity, "code": code, "partId": part_id, "message": text}


@dataclass
class Tree:
    root: str | None = None
    order: list = field(default_factory=list)        # part ids, parents before children
    parent: dict = field(default_factory=dict)       # id -> parent id
    entry: dict = field(default_factory=dict)        # id -> port the water enters through
    children: dict = field(default_factory=dict)     # id -> [child ids] in out-port order
    reverse: list = field(default_factory=list)      # ids entered through an outlet port
    loops: list = field(default_factory=list)        # (id, id) pairs closing a circle
    unreached: list = field(default_factory=list)    # ids with links that the source cannot reach


def port_links(net: Network):
    """(part, port) -> [(other part, other port, link id)] using only links whose ends exist and are valid."""
    pl: dict = {}
    for lk in net.links:
        (pa, qa), (pb, qb) = lk.a, lk.b
        if pa in net.parts and pb in net.parts and qa in net.parts[pa].ports and qb in net.parts[pb].ports:
            pl.setdefault((pa, qa), []).append((pb, qb, lk.id))
            pl.setdefault((pb, qb), []).append((pa, qa, lk.id))
    return pl


def build_tree(net: Network) -> Tree:
    t = Tree()
    sources = [p for p in net.order if p in net.parts and net.parts[p].kind == "source"]
    if len(sources) != 1:
        return t
    pl = port_links(net)
    root = sources[0]
    t.root, t.order, t.entry[root] = root, [root], None
    t.children[root] = []
    seen_links, queue = set(), [root]
    while queue:
        cur = queue.pop(0)
        part = net.parts[cur]
        for port in part.ports:
            if port == t.entry.get(cur):
                continue
            for (o, oq, lid) in sorted(pl.get((cur, port), []), key=lambda x: net.order.index(x[0])):
                if lid in seen_links:
                    continue
                seen_links.add(lid)
                if o in t.entry:                             # already reached: this link closes a circle
                    t.loops.append((cur, o))
                    continue
                t.parent[o], t.entry[o] = cur, oq
                t.children.setdefault(cur, []).append(o)
                t.children.setdefault(o, [])
                t.order.append(o)
                if oq != "in":
                    t.reverse.append(o)
                queue.append(o)
    linked = {pp for (pp, _), v in pl.items() if v}
    t.unreached = [p for p in net.order if p in net.parts and p not in t.entry and p in linked]
    return t


def validate(net: Network, known_nodes=None) -> list:
    out = []
    L = net.label
    sources = [p for p in net.order if p in net.parts and net.parts[p].kind == "source"]
    if not sources:
        out.append(msg("error", "no_source", None, "The network has no main supply. Add exactly one."))
    elif len(sources) > 1:
        for s in sources[1:]:
            out.append(msg("error", "multiple_sources", s, f"{L(s)}: there can be only one main supply."))

    # --- link sanity
    link_count: dict = {}
    for lk in net.links:
        ok = True
        for (pid, port) in (lk.a, lk.b):
            if pid in net.nodes:
                out.append(msg("error", "link_to_node", pid, f"{L(pid)} is a sensor on a pipe and cannot be connected like a pipe part."))
                ok = False
            elif pid not in net.parts:
                out.append(msg("error", "link_unknown_part", None, f"A connection refers to a part that does not exist ({pid})."))
                ok = False
            elif port not in net.parts[pid].ports:
                out.append(msg("error", "link_bad_port", pid, f"{L(pid)} has no port named {port}."))
                ok = False
        if ok and lk.a == lk.b:
            out.append(msg("error", "link_self", lk.a[0], f"{L(lk.a[0])} is connected to itself."))
            ok = False
        if ok:
            for end in (lk.a, lk.b):
                link_count[end] = link_count.get(end, 0) + 1
    for (pid, port), n in link_count.items():
        if n > 1:
            out.append(msg("error", "port_multiple", pid, f"{L(pid)}: port {port} has {n} connections; a port takes exactly one."))

    # --- open ports / unconnected parts
    for pid in net.order:
        p = net.parts.get(pid)
        if p is None:
            continue
        used = [q for q in p.ports if link_count.get((pid, q))]
        if not used:
            out.append(msg("warning", "unconnected_part", pid, f"{L(pid)} is not connected to anything."))
            continue
        for q in p.ports:
            if not link_count.get((pid, q)):
                if p.kind in ("outlet", "cap"):
                    out.append(msg("error", "port_open", pid, f"{L(pid)} is not connected to the pipe it belongs to."))
                elif p.kind == "source":
                    out.append(msg("error", "port_open", pid, f"{L(pid)}: the supply is not connected to a pipe."))
                else:
                    out.append(msg("error", "port_open", pid, f"{L(pid)}: port {q} is not connected (connect it, or end it with an end cap)."))

    # --- tree shape
    tree = build_tree(net)
    if tree.root:
        for pid in tree.reverse:
            out.append(msg("error", "reverse_flow", pid, f"{L(pid)} is connected backwards: water would enter through its outlet port. Rotate or flip it so the inlet faces the supply."))
        for (a, b) in tree.loops:
            out.append(msg("error", "loop", a, f"Loop: {L(a)} and {L(b)} are connected in a circle. The network must be a tree flowing away from the supply."))
        for pid in tree.unreached:
            out.append(msg("error", "not_connected_to_supply", pid, f"{L(pid)} is not connected to the main supply."))

    # --- nodes (sensors on pipes)
    seen: dict = {}
    on_pipe: dict = {}
    for nid in net.order:
        n = net.nodes.get(nid)
        if n is None:
            continue
        raw = n.raw
        if raw.get("nodeType", "flow") != "flow":
            out.append(msg("error", "node_not_flow", nid, f"{L(nid)}: only flow nodes can be placed on a pipe."))
        key = node_key(n)
        if key in seen:
            out.append(msg("error", "node_twice", nid, f"{L(nid)} is placed more than once. Each node can be placed only once."))
        seen[key] = nid
        pipe = raw.get("pipe")
        if pipe not in net.parts or net.parts[pipe].kind != "pipe":
            out.append(msg("error", "node_not_on_pipe", nid, f"{L(nid)} must sit on a pipe."))
        else:
            pos = float(raw.get("pos", 0.5))
            if not 0.0 < pos < 1.0:
                out.append(msg("error", "node_pos", nid, f"{L(nid)}: place the sensor inside the pipe, not at its end."))
            if any(abs(pos - q) < 1e-6 for q in on_pipe.get(pipe, [])):
                out.append(msg("error", "node_overlap", nid, f"{L(nid)} sits at the same spot as another sensor on {L(pipe)}."))
            on_pipe.setdefault(pipe, []).append(pos)
        if raw.get("dir", "forward") == "reverse":
            out.append(msg("error", "node_reverse", nid, f"{L(nid)}: the flow arrow points against the water flow. Flip it."))
        if known_nodes is not None and key not in known_nodes:
            out.append(msg("error", "node_unknown", nid, f"{L(nid)} is not a paired node of the gateway. Only paired nodes can be placed."))
    return out


def is_valid(messages) -> bool:
    return not any(m["severity"] == "error" for m in messages)
