"""Pipeline presets: ready-made network documents (pure functions, no database).

Every builder takes the nodes to use as dicts {"number": int, "nodeRef": int|None, "name": str} and returns a pipeline document.
"""

STEP = 180          # editor layout only: horizontal distance between parts


def _node_part(i: int, n: dict, pipe: str, pos: float = 0.5, direction: str = "forward") -> dict:
    p = {"id": f"n{i}", "kind": "node", "nodeType": "flow", "number": n["number"], "pipe": pipe, "pos": pos, "dir": direction}
    if n.get("nodeRef"):
        p["nodeRef"] = n["nodeRef"]
    if n.get("name"):
        p["name"] = n["name"]
    return p


def calibration_rig(nodes: list, name: str = "Calibration rig") -> dict:
    """Series rig:  Supply -> Node A -> Node B -> ... -> outlet into a measuring container.  Never meant to be activated."""
    parts = [{"id": "src", "kind": "source", "x": 0, "y": 0, "rot": 0, "label": "Supply"}]
    links, prev = [], ("src", "out")
    for i, n in enumerate(nodes, 1):
        pid = f"p{i}"
        parts.append({"id": pid, "kind": "pipe", "x": 100 + (i - 1) * STEP, "y": 10, "rot": 0, "lengthM": 1.0, "diameterMm": 20, "label": f"Pipe {i}"})
        parts.append(_node_part(i, n, pid))
        links.append({"id": f"e{i}", "from": {"part": prev[0], "port": prev[1]}, "to": {"part": pid, "port": "in"}})
        prev = (pid, "out")
    parts.append({"id": "o1", "kind": "outlet", "x": 100 + len(nodes) * STEP, "y": 0, "rot": 0, "outletType": "tank", "label": "Measuring container"})
    links.append({"id": f"e{len(nodes) + 1}", "from": {"part": prev[0], "port": prev[1]}, "to": {"part": "o1", "port": "in"}})
    return {"schema": 1, "name": name, "preset": "calibration_rig", "parts": parts, "links": links}


# ----------------------------------------------------------------------------------------------- the other presets
class _B:
    """Tiny document builder."""

    def __init__(self):
        self.parts, self.links = [], []

    def add(self, pid, kind, x=0, y=0, rot=0, **kw):
        self.parts.append({"id": pid, "kind": kind, "x": x, "y": y, "rot": rot, **kw})

    def link(self, a, b):
        pa, qa = a.split(".")
        pb, qb = b.split(".")
        self.links.append({"id": f"e{len(self.links) + 1}", "from": {"part": pa, "port": qa}, "to": {"part": pb, "port": qb}})

    def node(self, i, n, pipe, pos=0.5):
        if n is not None:                                  # an unassigned slot simply leaves the meter out (draft)
            self.parts.append(_node_part(i, n, pipe, pos))

    def doc(self, name, preset):
        return {"schema": 1, "name": name, "preset": preset, "parts": self.parts, "links": self.links}


def leak_bench(nodes, name="Leak bench"):
    """Supply -> Node A -> T -> (branch 1: Node B -> tap) + (branch 2: dead-end 'leak tap').  The zone between A and B is strong: any water the
    leak tap lets out is flow that A sees and B does not."""
    a, b = (list(nodes) + [None, None])[:2]
    d = _B()
    d.add("src", "source", label="Supply")
    d.add("p1", "pipe", 100, 10, lengthM=1, diameterMm=20, label="Pipe 1")
    d.add("t1", "tee", 280, -10, label="T junction")
    d.add("p3", "pipe", 360, 90, lengthM=0.5, diameterMm=20, label="Leak branch")
    d.add("c1", "cap", 520, 90, label="Leak tap (open = simulated leak)")
    d.add("p2", "pipe", 360, 10, lengthM=1, diameterMm=20, label="Pipe to Node B")
    d.add("p4", "pipe", 520, 10, lengthM=1, diameterMm=20, label="Pipe 4")
    d.add("o1", "outlet", 680, 0, outletType="tap", label="Tap")
    d.node("n1", a, "p1")
    d.node("n2", b, "p2")
    d.link("src.out", "p1.in")
    d.link("p1.out", "t1.in")
    d.link("t1.out1", "p3.in")
    d.link("p3.out", "c1.in")
    d.link("t1.out2", "p2.in")
    d.link("p2.out", "p4.in")
    d.link("p4.out", "o1.in")
    return d.doc(name, "leak_bench")


def branch_balance(nodes, name="Branch balance"):
    """Supply -> Node A -> T -> (Node B -> tap) + (Node C -> tap).  The zone after A is strong: A should equal B + C."""
    a, b, c = (list(nodes) + [None, None, None])[:3]
    d = _B()
    d.add("src", "source", label="Supply")
    d.add("p1", "pipe", 100, 10, lengthM=1, diameterMm=20, label="Pipe 1")
    d.add("t1", "tee", 280, -10, label="T junction")
    d.add("p2", "pipe", 360, -50, lengthM=1, diameterMm=20, label="Branch 1")
    d.add("o1", "outlet", 520, -60, outletType="tap", label="Tap 1")
    d.add("p3", "pipe", 360, 70, lengthM=1, diameterMm=20, label="Branch 2")
    d.add("o2", "outlet", 520, 60, outletType="tap", label="Tap 2")
    d.node("n1", a, "p1")
    d.node("n2", b, "p2")
    d.node("n3", c, "p3")
    d.link("src.out", "p1.in")
    d.link("p1.out", "t1.in")
    d.link("t1.out1", "p2.in")
    d.link("p2.out", "o1.in")
    d.link("t1.out2", "p3.in")
    d.link("p3.out", "o2.in")
    return d.doc(name, "branch_balance")


def single_meter_house(nodes, name="Single meter house"):
    """Supply -> Node A -> T -> tap + shower.  One meter for everything after it: a weak zone (night flow / continuous flow rules)."""
    a = (list(nodes) + [None])[0]
    d = _B()
    d.add("src", "source", label="Supply")
    d.add("p1", "pipe", 100, 10, lengthM=3, diameterMm=25, label="Main pipe")
    d.add("t1", "tee", 280, -10, label="T junction")
    d.add("p2", "pipe", 360, -50, lengthM=2, diameterMm=20, label="Branch 1")
    d.add("o1", "outlet", 520, -60, outletType="tap", label="Tap 1")
    d.add("p3", "pipe", 360, 70, lengthM=2, diameterMm=20, label="Branch 2")
    d.add("o2", "outlet", 520, 60, outletType="shower", label="Shower 1")
    d.node("n1", a, "p1")
    d.link("src.out", "p1.in")
    d.link("p1.out", "t1.in")
    d.link("t1.out1", "p2.in")
    d.link("p2.out", "o1.in")
    d.link("t1.out2", "p3.in")
    d.link("p3.out", "o2.in")
    return d.doc(name, "single_meter_house")


_HOUSE_PARTS = [{"id": "src", "kind": "source", "x": 0, "y": 0, "rot": 0, "label": "Main supply"}, {"id": "p0", "kind": "pipe", "x": 100, "y": 10, "rot": 0, "lengthM": 6, "diameterMm": 25, "label": "Main pipe"}, {"id": "t1", "kind": "tee", "x": 220, "y": -10, "rot": 0, "label": "Split 1"}, {"id": "p1", "kind": "pipe", "x": 300, "y": 10, "rot": 0, "lengthM": 4, "diameterMm": 20, "label": "Kitchen branch"}, {"id": "o1", "kind": "outlet", "x": 440, "y": 0, "rot": 0, "outletType": "tap", "label": "Kitchen tap"}, {"id": "p2", "kind": "pipe", "x": 240, "y": 70, "rot": 90, "lengthM": 2, "diameterMm": 20, "label": "Down pipe"}, {"id": "t2", "kind": "tee", "x": 220, "y": 190, "rot": 90, "label": "Split 2"}, {"id": "p3", "kind": "pipe", "x": 240, "y": 260, "rot": 90, "lengthM": 4, "diameterMm": 20, "label": "Bathroom branch"}, {"id": "o2", "kind": "outlet", "x": 230, "y": 380, "rot": 90, "outletType": "shower", "label": "Shower"}, {"id": "p4", "kind": "pipe", "x": 100, "y": 200, "rot": 180, "lengthM": 5, "diameterMm": 20, "label": "Garden branch"}, {"id": "o3", "kind": "outlet", "x": 20, "y": 190, "rot": 180, "outletType": "garden", "label": "Garden tap"}]
_HOUSE_LINKS = [{"id": "e1", "from": {"part": "src", "port": "out"}, "to": {"part": "p0", "port": "in"}}, {"id": "e2", "from": {"part": "p0", "port": "out"}, "to": {"part": "t1", "port": "in"}}, {"id": "e3", "from": {"part": "t1", "port": "out1"}, "to": {"part": "p1", "port": "in"}}, {"id": "e4", "from": {"part": "p1", "port": "out"}, "to": {"part": "o1", "port": "in"}}, {"id": "e5", "from": {"part": "t1", "port": "out2"}, "to": {"part": "p2", "port": "in"}}, {"id": "e6", "from": {"part": "p2", "port": "out"}, "to": {"part": "t2", "port": "in"}}, {"id": "e7", "from": {"part": "t2", "port": "out1"}, "to": {"part": "p3", "port": "in"}}, {"id": "e8", "from": {"part": "p3", "port": "out"}, "to": {"part": "o2", "port": "in"}}, {"id": "e9", "from": {"part": "t2", "port": "out2"}, "to": {"part": "p4", "port": "in"}}, {"id": "e10", "from": {"part": "p4", "port": "out"}, "to": {"part": "o3", "port": "in"}}]
_HOUSE_NODES = [["n2", "p0", 0.5], ["n3", "p1", 0.5], ["n4", "p3", 0.5], ["n5", "p4", 0.5]]


def my_house(nodes, name="My house"):
    """Main meter + kitchen, bathroom and garden branches (the four-node house of examples/four-node-house.json)."""
    slots = (list(nodes) + [None] * 4)[:4]
    parts = [dict(p) for p in _HOUSE_PARTS]
    for i, ((_pid, pipe, pos), n) in enumerate(zip(_HOUSE_NODES, slots), 1):
        if n is not None:
            parts.append(_node_part(i, n, pipe, pos))
    return {"schema": 1, "name": name, "preset": "my_house", "parts": parts, "links": [dict(x) for x in _HOUSE_LINKS]}


PRESETS = {
    "calibration_rig": {
        "name": "Calibration rig", "builder": calibration_rig, "minNodes": 1, "maxNodes": 8,
        "slots": ["Node A (first in line)", "Node B", "Node C", "Node D", "Node E", "Node F", "Node G", "Node H"],
        "description": "Nodes in series, all measuring the same water, ending in a container of known volume. Used to calibrate pulses per litre "
                       "(the Calibrate wizard builds this for you). Never activate it for leak detection.",
        "physicalSetup": ["Connect supply -> Node A -> Node B -> ... with short hoses, flow arrows pointing along the water.",
                          "Let the last hose run into a measuring container (bucket, jug or scale).",
                          "Open the tap slowly; no branches, no leaks, no trapped air."],
    },
    "leak_bench": {
        "name": "Leak bench", "builder": leak_bench, "minNodes": 2, "maxNodes": 2, "slots": ["Node A (before the leak)", "Node B (after the leak)"],
        "description": "Tests the strong-zone detector: the zone between Node A and Node B has no outlet, so any water leaving through the 'leak tap' is flow "
                       "that A sees and B does not. Open the leak tap a little and the zone should go suspected, then confirmed.",
        "physicalSetup": ["Supply -> Node A -> a T fitting.", "One T branch goes through Node B to a normal tap (the measured use).",
                          "The other T branch ends in a small tap or valve (the 'leak tap') that drains into a bucket.",
                          "Water must pass A before the T and B after it; keep both arrows along the flow."],
    },
    "branch_balance": {
        "name": "Branch balance", "builder": branch_balance, "minNodes": 3, "maxNodes": 3, "slots": ["Node A (main)", "Node B (branch 1)", "Node C (branch 2)"],
        "description": "Tests the balance A = B + C across a junction: with both taps open the unaccounted flow must stay near zero; a wrong calibration "
                       "or a leak on either branch shows up as a difference.",
        "physicalSetup": ["Supply -> Node A -> T fitting.", "Each T branch goes through its own node (B and C) to a tap.",
                          "Open the taps one at a time and together, and compare the flows."],
    },
    "single_meter_house": {
        "name": "Single meter house", "builder": single_meter_house, "minNodes": 1, "maxNodes": 1, "slots": ["Node A (main meter)"],
        "description": "One meter for the whole house with outlets after it: a weak zone, checked with the night-flow and continuous-flow rules instead of a balance.",
        "physicalSetup": ["Fit one node on the main supply pipe, arrow along the flow.", "Everything else hangs after it (taps, shower).",
                          "Leaks show as flow at night or flow that never stops."],
    },
    "my_house": {
        "name": "My house", "builder": my_house, "minNodes": 4, "maxNodes": 4, "slots": ["Main outlet (on the main pipe)", "Kitchen branch", "Bathroom branch", "Garden branch"],
        "description": "The four-node house: a main meter and three branch meters (kitchen, bathroom, garden). Strong zones compare the main flow with the sum "
                       "of the branches; each branch is a weak zone.",
        "physicalSetup": ["Node 1 on the main pipe after the stop tap.", "T fitting -> kitchen branch with Node 2.",
                          "Second T -> bathroom branch with Node 3 and garden branch with Node 4.", "Arrows along the flow on every node."],
    },
}


def preset_list() -> list:
    return [{"id": k, "name": v["name"], "description": v["description"], "physicalSetup": v["physicalSetup"], "slots": v["slots"][: v["maxNodes"]],
             "minNodes": v["minNodes"], "maxNodes": v["maxNodes"]} for k, v in PRESETS.items()]
