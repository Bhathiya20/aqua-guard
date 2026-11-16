"""Pipeline network JSON: structure, labels, parsing.  Pure Python, no Flask, no database.

Document (schema 1)
-------------------
{
  "schema": 1, "name": "House",
  "parts": [ {"id": "src", "kind": "source", "x": 0, "y": 0, "rot": 0, "label": "Main supply"},
             {"id": "p1",  "kind": "pipe", "lengthM": 3.0, "diameterMm": 20},
             {"id": "t1",  "kind": "tee"}, {"id": "o1", "kind": "outlet", "outletType": "tap"}, {"id": "c1", "kind": "cap"},
             {"id": "n1",  "kind": "node", "nodeType": "flow", "number": 1, "nodeRef": 7, "pipe": "p1", "pos": 0.5, "dir": "forward"} ],
  "links": [ {"id": "e1", "from": {"part": "src", "port": "out"}, "to": {"part": "p1", "port": "in"}} ]
}
Water flows away from the source.  Every part (except the source) has ONE inlet port named "in" and its outlet ports "out"/"out1"/"out2"...;
a part entered through an outlet port is connected backwards (reverse flow).  A node is NOT a graph vertex: it is a meter sitting on a
pipe at `pos` (0..1 measured from the pipe inlet) and `dir` says which way its arrow points ("forward" = along the flow).
Layout fields (x, y, rot, label ...) are for the editor only and never matter for the analysis.
"""
import re
from dataclasses import dataclass, field

SCHEMA = 1
MAX_PARTS = 400
KINDS = {                       # kind -> (display name, ports)
    "source": ("Main supply", ("out",)),
    "pipe": ("Pipe", ("in", "out")),
    "elbow": ("L elbow", ("in", "out")),
    "valve": ("Valve", ("in", "out")),
    "tee": ("T junction", ("in", "out1", "out2")),
    "cross": ("Cross junction", ("in", "out1", "out2", "out3")),
    "outlet": ("Outlet", ("in",)),
    "cap": ("End cap", ("in",)),
}
OUTLET_TYPES = ("tap", "shower", "garden", "tank")
NODE_KIND = "node"
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,40}")


class DocError(ValueError):
    """The JSON is structurally unusable (maps to HTTP 400).  Design mistakes are validation MESSAGES instead."""


@dataclass
class Part:
    id: str
    kind: str
    raw: dict
    label: str = ""

    @property
    def ports(self):
        return KINDS[self.kind][1] if self.kind in KINDS else ()

    @property
    def in_port(self):
        return "in" if self.kind != "source" else None

    @property
    def out_ports(self):
        return tuple(p for p in self.ports if p != "in")


@dataclass
class Link:
    id: str
    a: tuple          # (part id, port)
    b: tuple


@dataclass
class Network:
    name: str
    parts: dict = field(default_factory=dict)      # id -> Part (graph parts only)
    nodes: dict = field(default_factory=dict)      # id -> Part (kind == "node")
    links: list = field(default_factory=list)
    order: list = field(default_factory=list)      # ids in document order (labels are numbered by it)

    def label(self, pid: str) -> str:
        p = self.parts.get(pid) or self.nodes.get(pid)
        return p.label if p else pid


def _num(v, lo, hi, what, default=None):
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not (lo <= v <= hi):
        raise DocError(f"{what} must be a number between {lo} and {hi}")
    return v


_OUTLET_BASE = {"tap": "Tap", "shower": "Shower", "garden": "Garden tap", "tank": "Tank inlet"}


def default_label(kind: str, outlet_type, counters: dict) -> str:
    """Names by type and order: 'Tap 1', 'Shower 1', 'Garden tap', 'End cap 1', 'Pipe 6'.  The frontend mirrors this (docModel.partLabels)."""
    if kind == NODE_KIND:
        return "Node"
    if kind == "outlet":
        base = _OUTLET_BASE.get(outlet_type, "Outlet")
        key = ("outlet", base)
    else:
        base = KINDS[kind][0]
        key = (kind, base)
    counters[key] = counters.get(key, 0) + 1
    n = counters[key]
    if kind == "source" or (kind == "outlet" and base == "Garden tap" and n == 1):
        return base                                    # unique / first of its kind: no number
    return f"{base} {n}"


def parse(doc) -> Network:
    """Structural parse + display labels ('T junction 2').  Raises DocError for anything that is not a network document."""
    if not isinstance(doc, dict):
        raise DocError("the network must be a JSON object")
    if doc.get("schema") != SCHEMA:
        raise DocError(f"unsupported network schema {doc.get('schema')!r} (expected {SCHEMA})")
    parts, links = doc.get("parts"), doc.get("links")
    if not isinstance(parts, list) or not isinstance(links, list):
        raise DocError("parts and links must be lists")
    if len(parts) > MAX_PARTS or len(links) > MAX_PARTS * 3:
        raise DocError(f"too many parts (limit {MAX_PARTS})")
    net = Network(name=str(doc.get("name", ""))[:80])
    counters: dict = {}
    for raw in parts:
        if not isinstance(raw, dict):
            raise DocError("every part must be an object")
        pid, kind = raw.get("id"), raw.get("kind")
        if not isinstance(pid, str) or not _ID.fullmatch(pid):
            raise DocError(f"bad part id {pid!r}")
        if pid in net.parts or pid in net.nodes:
            raise DocError(f"duplicate part id {pid!r}")
        if kind != NODE_KIND and kind not in KINDS:
            raise DocError(f"unknown part kind {kind!r}")
        lab = str(raw.get("label") or "").strip()[:40]
        auto = default_label(kind, raw.get("outletType", "tap"), counters)
        if kind == NODE_KIND:
            num = raw.get("number")
            if isinstance(num, bool) or not isinstance(num, int) or not 1 <= num <= 250:
                raise DocError(f"node {pid!r}: number must be 1..250")
            _num(raw.get("pos"), 0.0, 1.0, f"node {pid!r}: pos", 0.5)
            if raw.get("dir", "forward") not in ("forward", "reverse"):
                raise DocError(f"node {pid!r}: dir must be forward or reverse")
            p = Part(pid, kind, raw, lab or f"Node {num}")
            net.nodes[pid] = p
        else:
            if kind == "outlet" and raw.get("outletType", "tap") not in OUTLET_TYPES:
                raise DocError(f"outlet {pid!r}: outletType must be one of {', '.join(OUTLET_TYPES)}")
            if kind == "pipe":
                _num(raw.get("lengthM"), 0, 100000, f"pipe {pid!r}: lengthM")
                _num(raw.get("diameterMm"), 0, 5000, f"pipe {pid!r}: diameterMm")
            net.parts[pid] = Part(pid, kind, raw, lab or auto)
        net.order.append(pid)
    seen = set()
    for raw in links:
        if not isinstance(raw, dict):
            raise DocError("every link must be an object")
        lid = raw.get("id")
        if not isinstance(lid, str) or not _ID.fullmatch(lid) or lid in seen:
            raise DocError(f"bad or duplicate link id {lid!r}")
        seen.add(lid)
        ends = []
        for side in ("from", "to"):
            e = raw.get(side)
            if not isinstance(e, dict) or not isinstance(e.get("part"), str) or not isinstance(e.get("port"), str):
                raise DocError(f"link {lid!r}: '{side}' must be {{part, port}}")
            ends.append((e["part"], e["port"]))
        net.links.append(Link(lid, ends[0], ends[1]))
    net.name = net.name or "Network"
    return net


def node_key(p: Part):
    """Identity of a placed node inside the document: (type, number)."""
    return (p.raw.get("nodeType", "flow"), p.raw.get("number"))
