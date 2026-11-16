"""Small builders for pipeline documents used by the validation, zone and analysis tests."""


class Doc:
    def __init__(self, name="Test"):
        self.parts, self.links, self._n = [], [], 0

    def add(self, pid, kind, **kw):
        self.parts.append({"id": pid, "kind": kind, **kw})
        return pid

    def node(self, pid, number, pipe, pos=0.5, **kw):
        self.parts.append({"id": pid, "kind": "node", "nodeType": "flow", "number": number, "pipe": pipe, "pos": pos, "dir": "forward", **kw})
        return pid

    def link(self, a, b):
        """a, b = 'part.port' strings."""
        self._n += 1
        pa, qa = a.split(".")
        pb, qb = b.split(".")
        self.links.append({"id": f"e{self._n}", "from": {"part": pa, "port": qa}, "to": {"part": pb, "port": qb}})

    def chain(self, *ids):
        """Link out -> in along single-in/single-out parts (source has only 'out'; the last part uses 'in')."""
        for a, b in zip(ids, ids[1:]):
            self.link(f"{a}.out", f"{b}.in")

    def doc(self, **extra):
        return {"schema": 1, "name": "Test", "parts": self.parts, "links": self.links, **extra}


def straight():
    """src -> p1[node1] -> p2[node2] -> outlet.   zones: before node1 | node1..node2 (no outlet: strong) | after node2 (outlet: weak)"""
    d = Doc()
    d.add("src", "source"); d.add("p1", "pipe", lengthM=4); d.add("p2", "pipe", lengthM=4); d.add("o1", "outlet", outletType="tap")
    d.node("n1", 1, "p1"); d.node("n2", 2, "p2")
    d.chain("src", "p1", "p2", "o1")
    return d


def tee_tree():
    """src -> p1[node1] -> t1 -> (out1) p2[node2] -> o1 ; (out2) p3 -> o2"""
    d = Doc()
    d.add("src", "source"); d.add("p1", "pipe", lengthM=5); d.add("t1", "tee"); d.add("p2", "pipe", lengthM=3); d.add("p3", "pipe", lengthM=2)
    d.add("o1", "outlet", outletType="shower"); d.add("o2", "outlet", outletType="tap")
    d.node("n1", 1, "p1"); d.node("n2", 2, "p2")
    d.link("src.out", "p1.in"); d.link("p1.out", "t1.in"); d.link("t1.out1", "p2.in"); d.link("t1.out2", "p3.in"); d.link("p2.out", "o1.in"); d.link("p3.out", "o2.in")
    return d


def elbow_chain():
    """src -> p1 -> l1 -> p2[node1] -> l2 -> p3 -> outlet"""
    d = Doc()
    d.add("src", "source"); d.add("p1", "pipe"); d.add("l1", "elbow"); d.add("p2", "pipe"); d.add("l2", "elbow"); d.add("p3", "pipe"); d.add("o1", "outlet")
    d.node("n1", 1, "p2")
    d.chain("src", "p1", "l1", "p2", "l2", "p3", "o1")
    return d


def main_only():
    """The user's real set-up: ONE node on the main supply, everything else downstream of it (one weak zone)."""
    d = Doc()
    d.add("src", "source"); d.add("p1", "pipe"); d.add("t1", "tee"); d.add("p2", "pipe"); d.add("p3", "pipe"); d.add("o1", "outlet"); d.add("o2", "outlet")
    d.node("n1", 1, "p1", 0.3)
    d.link("src.out", "p1.in"); d.link("p1.out", "t1.in"); d.link("t1.out1", "p2.in"); d.link("t1.out2", "p3.in"); d.link("p2.out", "o1.in"); d.link("p3.out", "o2.in")
    return d


def unmetered():
    """src -> p1 -> outlet, no sensor at all."""
    d = Doc()
    d.add("src", "source"); d.add("p1", "pipe"); d.add("o1", "outlet")
    d.chain("src", "p1", "o1")
    return d


def strong_only():
    """src -> p1[node1] -> p2[node2] -> p3 -> cap : both zones after the sensors have no outlets."""
    d = Doc()
    d.add("src", "source"); d.add("p1", "pipe"); d.add("p2", "pipe"); d.add("c1", "cap")
    d.node("n1", 1, "p1"); d.node("n2", 2, "p2")
    d.chain("src", "p1", "p2", "c1")
    return d
