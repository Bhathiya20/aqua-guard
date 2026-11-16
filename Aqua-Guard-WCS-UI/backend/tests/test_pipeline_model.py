"""Pipeline JSON: structure, validation messages, zone partition and classification (brief sections 6 and 7.1)."""
import copy

import pytest

from analysis.zones import NONE, STRONG, WEAK, partition
from pipeline.model import DocError, parse
from pipeline.validate import is_valid, validate
from tests import netdocs as nd


def msgs(doc, known=None):
    return validate(parse(doc), known)


def codes(m):
    return {x["code"] for x in m}


def test_valid_networks_have_no_errors():
    for d in (nd.straight(), nd.tee_tree(), nd.elbow_chain(), nd.main_only(), nd.unmetered(), nd.strong_only()):
        assert is_valid(msgs(d.doc())), msgs(d.doc())


def test_structural_garbage_is_refused():
    for bad in ({}, {"schema": 2, "parts": [], "links": []}, {"schema": 1, "parts": "x", "links": []},
                {"schema": 1, "parts": [{"id": "a", "kind": "banana"}], "links": []},
                {"schema": 1, "parts": [{"id": "a", "kind": "pipe"}, {"id": "a", "kind": "pipe"}], "links": []},
                {"schema": 1, "parts": [{"id": "o", "kind": "outlet", "outletType": "bidet"}], "links": []},
                {"schema": 1, "parts": [{"id": "p", "kind": "pipe", "lengthM": -1}], "links": []}):
        with pytest.raises(DocError):
            parse(bad)


def test_open_port_message_names_the_part_and_the_port():
    d = nd.tee_tree()
    d.links = [lk for lk in d.links if lk["from"]["port"] != "out2"]               # t1.out2 -> p3.in removed
    m = msgs(d.doc())
    open_ports = [x for x in m if x["code"] == "port_open" and x["partId"] == "t1"]
    assert open_ports and "T junction 1" in open_ports[0]["message"] and "out2" in open_ports[0]["message"] and "end cap" in open_ports[0]["message"]
    assert "not_connected_to_supply" in codes(m)                                   # p3 and o2 are now cut off
    assert not is_valid(m)


def test_loop_is_reported():
    d2 = nd.Doc()                                                                  # both tee branches rejoin into a second tee: a circle
    d2.add("src", "source"); d2.add("t1", "tee"); d2.add("t2", "tee"); d2.add("p1", "pipe"); d2.add("p2", "pipe"); d2.add("o1", "outlet"); d2.add("c1", "cap")
    d2.link("src.out", "t1.in"); d2.link("t1.out1", "p1.in"); d2.link("t1.out2", "p2.in"); d2.link("p1.out", "t2.out1"); d2.link("p2.out", "t2.out2"); d2.link("t2.in", "o1.in")
    m = msgs(d2.doc())
    assert "loop" in codes(m) and not is_valid(m)


def test_reverse_flow_is_reported():
    d = nd.straight()
    for lk in d.links:
        if lk["to"]["part"] == "p2":                                               # p1.out -> p2.in becomes p1.out -> p2.out
            lk["to"]["port"] = "out"
    m = msgs(d.doc())
    assert any(x["code"] == "reverse_flow" and x["partId"] == "p2" and "backwards" in x["message"] for x in m)


def test_two_sources_and_no_source():
    d = nd.straight()
    d.add("src2", "source")
    assert "multiple_sources" in codes(msgs(d.doc()))
    e = nd.Doc(); e.add("p", "pipe")
    assert "no_source" in codes(msgs(e.doc()))


def test_unconnected_part_is_a_warning_not_an_error():
    d = nd.straight()
    d.add("stray", "pipe")
    m = msgs(d.doc())
    w = [x for x in m if x["code"] == "unconnected_part"]
    assert w and w[0]["severity"] == "warning" and w[0]["partId"] == "stray" and is_valid(m)


def test_node_rules():
    d = nd.straight()
    d.node("n1b", 1, "p2", 0.2)                                                    # node 1 placed a second time
    assert "node_twice" in codes(msgs(d.doc()))
    d = nd.straight(); d.parts[-1]["pipe"] = "o1"                                  # not on a pipe
    assert "node_not_on_pipe" in codes(msgs(d.doc()))
    d = nd.straight(); d.parts[-1]["dir"] = "reverse"
    assert "node_reverse" in codes(msgs(d.doc()))
    d = nd.straight(); d.node("n3", 3, "p1", 0.5)                                  # same spot as n1
    assert "node_overlap" in codes(msgs(d.doc()))
    d = nd.straight(); d.parts[-1]["pos"] = 1.0
    assert "node_pos" in codes(msgs(d.doc()))
    d = nd.straight(); d.parts[-1]["nodeType"] = "rain"
    assert "node_not_flow" in codes(msgs(d.doc()))
    # only PAIRED nodes may be placed
    assert "node_unknown" in codes(msgs(nd.straight().doc(), known={("flow", 1)}))
    assert is_valid(msgs(nd.straight().doc(), known={("flow", 1), ("flow", 2)}))


def test_labels_count_per_kind_in_document_order():
    net = parse(nd.tee_tree().doc())
    assert net.label("t1") == "T junction 1" and net.label("p2") == "Pipe 2" and net.label("o1") == "Shower 1" and net.label("o2") == "Tap 1" and net.label("n2") == "Node 2"


# --------------------------------------------------------------------------- zones
def zones(d):
    return {z.id: z for z in partition(parse(d.doc())).zones}


def test_straight_line_zones_and_classes():
    z = zones(nd.straight())
    assert list(z) == ["zone:source", "zone:flow1", "zone:flow2"]
    assert z["zone:source"].cls == NONE and z["zone:flow1"].cls == STRONG and z["zone:flow2"].cls == WEAK
    assert z["zone:flow1"].in_meter.key == ("flow", 1) and [m.key for m in z["zone:flow1"].out_meters] == [("flow", 2)]
    assert z["zone:flow1"].equation == "Q_unaccounted(After Node 1) = Q[Node 1] - (Q[Node 2])"
    assert z["zone:flow2"].outlets == ["o1"] and z["zone:flow2"].out_meters == []


def test_tee_tree_partition():
    z = zones(nd.tee_tree())
    assert z["zone:flow1"].cls == WEAK and z["zone:flow1"].outlets == ["o2"] and [m.key for m in z["zone:flow1"].out_meters] == [("flow", 2)]
    assert z["zone:flow2"].cls == WEAK and z["zone:flow2"].outlets == ["o1"]
    assert "t1" in z["zone:flow1"].parts and "p3" in [p for p, _ in z["zone:flow1"].pipes]


def test_elbow_chain_partition():
    z = zones(nd.elbow_chain())
    assert z["zone:source"].cls == NONE and set(z["zone:source"].parts) == {"src", "l1"}
    assert z["zone:flow1"].cls == WEAK and set(z["zone:flow1"].parts) == {"l2"} and z["zone:flow1"].outlets == ["o1"]


def test_unmonitored_branch_gets_a_where_to_add_suggestion():
    p = partition(parse(nd.unmetered().doc()))
    z = p.zones[0]
    assert z.cls == NONE and "No sensor" in z.reason
    assert p.suggestions and p.suggestions[0]["partId"] == "p1" and "Pipe 1" in p.suggestions[0]["text"] and "Main supply" in p.suggestions[0]["text"] and "Tap 1" in p.suggestions[0]["text"]
    assert p.coverage["monitored"] == 0 and p.coverage["outletsCovered"] == 0


def test_single_node_on_the_main_supply_makes_one_weak_zone():
    """The real installation: one node on the main supply is a weak leak-monitored zone, NOT 'not monitored'."""
    p = partition(parse(nd.main_only().doc()))
    z = {x.id: x for x in p.zones}
    assert z["zone:flow1"].cls == WEAK and len(z["zone:flow1"].outlets) == 2
    assert z["zone:source"].cls == NONE                                            # only the short stretch before the sensor
    assert p.coverage["outletsCovered"] == 2 and p.coverage["outletFraction"] == 1.0
    refine = [s for s in p.suggestions if s["kind"] == "refine"]
    assert {s["partId"] for s in refine} == {"p2", "p3"}                           # a node on a branch would localise use vs leak


def test_strong_vs_weak_with_only_dead_ends():
    z = zones(nd.strong_only())
    assert z["zone:flow1"].cls == STRONG and z["zone:flow2"].cls == STRONG and z["zone:flow2"].outlets == []


def test_coverage_summary_text_and_fractions():
    p = partition(parse(nd.straight().doc()))
    c = p.coverage
    assert c["zones"] == 3 and c["monitored"] == 2 and c["text"].startswith("2 of 3 zones leak-monitored")
    assert c["unit"] == "m" and c["pipeTotal"] == 8 and 0 < c["pipeCovered"] < 8 and c["outletsCovered"] == 1


def test_a_pipe_with_two_sensors_has_a_strong_zone_between_them():
    d = nd.Doc()
    d.add("src", "source"); d.add("p1", "pipe"); d.add("o1", "outlet"); d.node("a", 1, "p1", 0.25); d.node("b", 2, "p1", 0.75)
    d.chain("src", "p1", "o1")
    z = {x.id: x for x in partition(parse(d.doc())).zones}
    assert z["zone:flow1"].cls == STRONG and z["zone:flow1"].pipes == [("p1", pytest.approx(0.5))] and z["zone:flow2"].cls == WEAK


def test_default_names_follow_type_and_order():
    """Intentional change: 'Outlet 1' became 'Tap 1' / 'Shower 1' / 'Garden tap' / 'End cap 1' (an explicit label always wins)."""
    parts = [{"id": "s", "kind": "source"}, {"id": "o1", "kind": "outlet", "outletType": "tap"}, {"id": "o2", "kind": "outlet", "outletType": "shower"},
             {"id": "o3", "kind": "outlet", "outletType": "garden"}, {"id": "o4", "kind": "outlet", "outletType": "tap"}, {"id": "o5", "kind": "outlet", "outletType": "garden"},
             {"id": "c1", "kind": "cap"}, {"id": "c2", "kind": "cap"}, {"id": "v1", "kind": "valve"}, {"id": "o6", "kind": "outlet", "outletType": "tap", "label": "Kitchen"}]
    net = parse({"schema": 1, "name": "x", "parts": parts, "links": []})
    got = [net.label(p["id"]) for p in parts]
    assert got == ["Main supply", "Tap 1", "Shower 1", "Garden tap", "Tap 2", "Garden tap 2", "End cap 1", "End cap 2", "Valve 1", "Kitchen"]
