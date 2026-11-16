import { describe, expect, it } from "vitest";
import type { AvailableNode, LiveZone, PipelineDoc, ValidationMessage } from "../../lib/v2";
import {
  SNAP_DIST, addMeter, addPart, applySnap, canConnect, detachPulledLinks, findSnap, portPoint, pushHistoryMerged, canRedo, canUndo, connect, docToFlow, emptyDoc, flowToDoc, messageTarget, meterArrowDeg, meterPoint, newHistory, nodeLabel,
  partSeverities, partSize, patchPart, pipeUnder, portSide, pushHistory, redo, removePart, rotatePart, rotateSide, undo, unplacedNodes, zoneColor, zoneOfParts, ZONE_COLORS,
} from "./docModel";

/** src -> p1 -> t1 -> (out1) p2 -> o1 ; (out2) p3 -> o2, with a meter on p1. Built through the same functions the editor uses. */
function house(): PipelineDoc {
  let d = emptyDoc("House");
  const add = (kind: any, x: number, y: number, extra: any = {}) => { const r = addPart(d, kind, x, y, extra); d = r.doc; return r.id; };
  const src = add("source", 0, 0), p1 = add("pipe", 100, 0), t1 = add("tee", 240, -20), p2 = add("pipe", 320, -20), p3 = add("pipe", 240, 60, { rot: 90 }), o1 = add("outlet", 460, -20), o2 = add("outlet", 240, 200);
  const link = (a: [string, string], b: [string, string]) => { d = connect(d, { part: a[0], port: a[1] }, { part: b[0], port: b[1] }).doc; };
  link([src, "out"], [p1, "in"]); link([p1, "out"], [t1, "in"]); link([t1, "out1"], [p2, "in"]); link([t1, "out2"], [p3, "in"]); link([p2, "out"], [o1, "in"]); link([p3, "out"], [o2, "in"]);
  return d;
}
const NODE = (n: number, name = ""): AvailableNode => ({ type: "flow", number: n, name, label: nodeLabel({ number: n, name }), nodeRef: n });

describe("document <-> canvas round trip", () => {
  it("docToFlow then flowToDoc returns the same document", () => {
    let d = house();
    d = addMeter(d, NODE(2, "Main outlet"), 140, 4).doc;                    // lands on p1
    const { nodes, edges } = docToFlow(d);
    expect(nodes).toHaveLength(d.parts.length);
    expect(edges).toHaveLength(d.links.length);
    const back = flowToDoc(d, nodes, edges);
    expect(back).toEqual(d);
  });
  it("keeps every link port and every layout field through the mapping", () => {
    const d = house();
    const { nodes, edges } = docToFlow(d);
    const moved = nodes.map((n) => (n.id === "p2" ? { ...n, position: { x: 400.4, y: 40.6 } } : n));
    const back = flowToDoc(d, moved, edges);
    expect(back.parts.find((p) => p.id === "p2")).toMatchObject({ x: 400, y: 41, rot: 0 });
    expect(back.links.map((l) => [l.id, l.from, l.to])).toEqual(d.links.map((l) => [l.id, l.from, l.to]));
  });
  it("rotated parts swap their box and the ports follow the rotation", () => {
    const d = house();
    const p3 = d.parts.find((p) => p.id === "p3")!;                       // rot 90
    expect(partSize(p3)).toEqual({ w: 20, h: 120 });
    expect(portSide("pipe", "in", 90)).toBe("top");
    expect(portSide("pipe", "out", 90)).toBe("bottom");
    expect(portSide("tee", "out2", 180)).toBe("top");
    expect(rotateSide("left", 270)).toBe("bottom");
  });
  it("attached meters sit on their pipe and are not stored with their own position", () => {
    let d = house();
    d = addMeter(d, NODE(2), 150, 4).doc;
    const m = d.parts.find((p) => p.kind === "node")!;
    expect(m.pipe).toBe("p1");
    const { nodes } = docToFlow(d);
    const fn = nodes.find((n) => n.id === m.id)!;
    expect(fn.position.y).toBe(10 - 14);                                   // pipe centre line y=10, marker 28 high
    const moved = flowToDoc(d, nodes.map((n) => (n.id === "p1" ? { ...n, position: { x: 100, y: 200 } } : n)), docToFlow(d).edges);
    expect(meterPoint(moved, moved.parts.find((p) => p.id === m.id)!).y).toBe(210);   // the meter followed the pipe
  });
});

describe("connection rules", () => {
  it("joins an outlet port to an inlet port, whichever end was dragged first", () => {
    let d = emptyDoc();
    const s = addPart(d, "source", 0, 0); d = s.doc;
    const p = addPart(d, "pipe", 100, 0); d = p.doc;
    const a = connect(d, { part: p.id, port: "in" }, { part: s.id, port: "out" });
    expect(a.result.ok).toBe(true);
    expect(a.doc.links[0]).toMatchObject({ from: { part: s.id, port: "out" }, to: { part: p.id, port: "in" } });
  });
  it("rejects in-in and out-out, self links, nodes, and ports already in use", () => {
    let d = house();
    const q = addPart(d, "pipe", 600, 0); d = q.doc;
    const q2 = addPart(d, "pipe", 700, 0); d = q2.doc;
    expect(canConnect(d, { part: q.id, port: "in" }, { part: q2.id, port: "in" }).ok).toBe(false);
    expect(canConnect(d, { part: q.id, port: "out" }, { part: q2.id, port: "out" }).ok).toBe(false);
    expect(canConnect(d, { part: q.id, port: "in" }, { part: q.id, port: "out" })).toMatchObject({ ok: false, reason: expect.stringMatching(/itself/) });
    expect(canConnect(d, { part: "p1", port: "out" }, { part: q.id, port: "in" })).toMatchObject({ ok: false, reason: expect.stringMatching(/already connected/) });
    d = addMeter(d, NODE(5), 150, 4).doc;
    const m = d.parts.find((p) => p.kind === "node")!;
    expect(canConnect(d, { part: m.id, port: "out" }, { part: q.id, port: "in" }).ok).toBe(false);
  });
  it("refuses a connection that would make a circle", () => {
    let d = emptyDoc();
    const a = addPart(d, "pipe", 0, 0); d = a.doc;
    const b = addPart(d, "pipe", 200, 0); d = b.doc;
    d = connect(d, { part: a.id, port: "out" }, { part: b.id, port: "in" }).doc;
    const r = canConnect(d, { part: b.id, port: "out" }, { part: a.id, port: "in" });
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/circle/);
  });
  it("allows only one main supply", () => {
    let d = emptyDoc();
    const a = addPart(d, "source", 0, 0); d = a.doc;
    const b = addPart(d, "source", 100, 100);
    expect(b.id).toBe(a.id);
    expect(b.doc.parts).toHaveLength(1);
  });
  it("deleting a part removes its links; deleting a pipe detaches its meters but keeps them", () => {
    let d = house();
    d = addMeter(d, NODE(2), 150, 4).doc;
    const noP2 = removePart(d, "p2");
    expect(noP2.links.some((l) => l.from.part === "p2" || l.to.part === "p2")).toBe(false);
    const noP1 = removePart(d, "p1");
    const m = noP1.parts.find((p) => p.kind === "node")!;
    expect(m.pipe).toBeNull();
    expect(noP1.links.every((l) => l.from.part !== "p1" && l.to.part !== "p1")).toBe(true);
  });
});

describe("rotation", () => {
  it("cycles through 0, 90, 180, 270 and back", () => {
    let d = emptyDoc();
    const t = addPart(d, "tee", 0, 0); d = t.doc;
    const seq = [0, 1, 2, 3, 4].map(() => { const r = d.parts[0].rot; d = rotatePart(d, t.id); return r; });
    expect(seq).toEqual([0, 90, 180, 270, 0]);
  });
  it("does not rotate a node", () => {
    const d = addMeter(house(), NODE(2), 150, 4).doc;
    const m = d.parts.find((p) => p.kind === "node")!;
    expect(rotatePart(d, m.id)).toBe(d);
  });
  it("snaps new parts to the 20 px grid", () => {
    const r = addPart(emptyDoc(), "pipe", 133, 47);
    expect(r.doc.parts[0]).toMatchObject({ x: 140, y: 40 });
  });
});

describe("nodes on pipes", () => {
  it("dropping a node over a pipe sets pipe and position; elsewhere it stays free", () => {
    let d = house();
    d = addMeter(d, NODE(2), 160, 4).doc;                                   // p1 spans x 100..220, y 0..20
    const on = d.parts.find((p) => p.kind === "node")!;
    expect(on.pipe).toBe("p1");
    expect(on.pos).toBeCloseTo(0.62, 1);                                    // marker centre x=174 on a pipe 100..220
    const off = addMeter(d, NODE(3), 900, 900).doc.parts.find((p) => p.kind === "node" && p.number === 3)!;
    expect(off.pipe).toBeNull();
  });
  it("position is measured from the pipe inlet, also for rotated pipes", () => {
    const d = house();
    const p3 = d.parts.find((p) => p.id === "p3")!;                          // vertical, inlet on top: x 240..260, y 60..180
    expect(pipeUnder(d, 250, 80)!.pos).toBeLessThan(0.3);
    expect(pipeUnder(patchPart(d, "p3", { rot: 270 }), 250, 80)!.pos).toBeGreaterThan(0.7);     // inlet at the bottom now
    expect(p3.rot).toBe(90);
  });
  it("the flow arrow follows pipe rotation and the direction flag", () => {
    let d = house();
    d = addMeter(d, NODE(2), 150, 4).doc;
    const m = d.parts.find((p) => p.kind === "node")!;
    expect(meterArrowDeg(d, m)).toBe(0);
    expect(meterArrowDeg(patchPart(d, m.id, { dir: "reverse" }), patchPart(d, m.id, { dir: "reverse" }).parts.find((p) => p.id === m.id)!)).toBe(180);
    const rotated = rotatePart(d, "p1");
    expect(meterArrowDeg(rotated, rotated.parts.find((p) => p.id === m.id)!)).toBe(90);
  });
  it("the palette lists only paired nodes that are not placed yet, labelled with number and name", () => {
    const avail = [NODE(2, "Main outlet"), NODE(3, "Com 34 last one"), NODE(4, "Com 37 2nd one")];
    let d = addMeter(house(), avail[0], 150, 4).doc;
    expect(unplacedNodes(avail, d).map((n) => n.label)).toEqual(["Node 3 - Com 34 last one", "Node 4 - Com 37 2nd one"]);
    d = addMeter(d, avail[0], 150, 4).doc;                                   // placing again changes nothing
    expect(d.parts.filter((p) => p.kind === "node")).toHaveLength(1);
  });
});

describe("undo / redo", () => {
  it("walks back and forward through edits and forgets the future after a new edit", () => {
    let h = newHistory(emptyDoc());
    expect(canUndo(h)).toBe(false);
    const d1 = addPart(h.present, "source", 0, 0).doc;
    h = pushHistory(h, d1);
    const d2 = addPart(d1, "pipe", 100, 0).doc;
    h = pushHistory(h, d2);
    expect(h.present.parts).toHaveLength(2);
    h = undo(h);
    expect(h.present.parts).toHaveLength(1);
    expect(canRedo(h)).toBe(true);
    h = redo(h);
    expect(h.present.parts).toHaveLength(2);
    h = undo(undo(h));
    expect(h.present.parts).toHaveLength(0);
    expect(undo(h)).toBe(h);                                                  // nothing left
    h = pushHistory(h, addPart(h.present, "outlet", 0, 0).doc);
    expect(canRedo(h)).toBe(false);
  });
  it("ignores an unchanged document", () => {
    const h = newHistory(house());
    expect(pushHistory(h, JSON.parse(JSON.stringify(h.present)))).toBe(h);
  });
});

describe("validation messages -> parts", () => {
  const msgs: ValidationMessage[] = [
    { severity: "warning", code: "unconnected_part", partId: "p2", message: "Pipe 2 is not connected to anything." },
    { severity: "error", code: "port_open", partId: "p2", message: "Pipe 2: port out is not connected." },
    { severity: "error", code: "no_source", partId: null, message: "The network has no main supply." },
    { severity: "error", code: "node_pos", partId: "gone", message: "stale" },
  ];
  it("outlines each named part with its worst severity and ignores document-wide or stale messages", () => {
    expect(partSeverities(msgs, house())).toEqual({ p2: "error" });
  });
  it("clicking a message targets the part it names", () => {
    const d = house();
    expect(messageTarget(msgs[1], d)).toBe("p2");
    expect(messageTarget(msgs[2], d)).toBeNull();
    expect(messageTarget(msgs[3], d)).toBeNull();
  });
});

describe("live zone colours", () => {
  const zone = (id: string, status: LiveZone["status"], parts: string[], pipes: Array<[string, number]>, outletIds: string[] = []): LiveZone => ({
    id, name: id, class: "strong", status, level: 0, reason: "", equation: "", inMeter: null, outMeters: [], outlets: [], outletIds, pipes: pipes.map(([i, f]) => ({ id: i, fraction: f })), parts, balance: null,
    meterFaultHint: null, suggestions: [], parent: null, rules: [], completeBuckets: 0, finalBuckets: 0, nodesMissing: [],
  });
  it("has one distinct colour per status, with a legend label", () => {
    const keys = ["normal", "suspected", "confirmed", "not_monitored", "incomplete"] as const;
    expect(new Set(keys.map((k) => ZONE_COLORS[k].stroke)).size).toBe(5);
    expect(zoneColor("confirmed").label).toBe("Confirmed leak");
    expect(zoneColor("whatever")).toBe(ZONE_COLORS.not_monitored);
  });
  it("colours parts, pipes and outlets by their zone; the more alarming zone wins on shared parts", () => {
    const zs = [zone("zone:source", "normal", ["src", "t1"], [["p1", 0.5]]), zone("zone:flow2", "confirmed", ["t1"], [["p1", 0.5], ["p2", 1]], ["o1"])];
    const m = zoneOfParts(zs);
    expect(m.parts["t1"].status).toBe("confirmed");
    expect(m.parts["src"].status).toBe("normal");
    expect(m.parts["o1"].status).toBe("confirmed");
    expect(m.pipeSplit["p1"]).toMatchObject({ at: 0.5 });
    expect(m.pipeSplit["p1"].upstream.id).toBe("zone:source");
    expect(m.pipeSplit["p2"]).toBeUndefined();
  });
});

describe("magnetic connect", () => {
  const two = (dx: number, dy: number) => {            // source at (0,0): outlet port at (80,20); pipe placed so its inlet is dx/dy away from that
    let d = addPart(emptyDoc(), "source", 0, 0).doc;
    d = patchPart(addPart(d, "pipe", 0, 0).doc, "p1", { x: 80 + dx, y: 10 + dy });
    return d;
  };
  it("finds the closest compatible free port pair and snaps so the ports touch", () => {
    const d = two(14, 6);
    const r = findSnap(d, "p1");
    expect(r.hit).toMatchObject({ mine: { part: "p1", port: "in" }, other: { part: "src1", port: "out" } });
    const after = applySnap(d, r.hit!);
    expect(after.links).toHaveLength(1);
    const a = portPoint(after.parts.find((p) => p.id === "src1")!, "out")!, b = portPoint(after.parts.find((p) => p.id === "p1")!, "in")!;
    expect(b).toEqual(a);
  });
  it("does nothing beyond the snap distance", () => {
    expect(findSnap(two(SNAP_DIST + 6, 0), "p1")).toEqual({ hit: null, refused: null });
  });
  it("only joins an outlet to an inlet, and never a port that is already linked", () => {
    let d = two(10, 0);
    expect(findSnap(d, "p1").hit).not.toBeNull();
    d = connect(d, { part: "src1", port: "out" }, { part: "p1", port: "in" }).doc;
    expect(findSnap(d, "p1").hit).toBeNull();                      // both ports are taken now
    const e = patchPart(addPart(two(10, 0), "pipe", 0, 0).doc, "p2", { x: 80 + 10, y: 10 });   // another pipe whose inlet is next to the same outlet
    expect(findSnap(e, "p2").hit?.other.part).toBe("src1");
    const f = patchPart(addPart(addPart(emptyDoc(), "outlet", 0, 0).doc, "outlet", 0, 0).doc, "o2", { x: 4, y: 0 });
    expect(findSnap(f, "o2").hit).toBeNull();                       // inlet next to inlet
  });
  it("refuses a loop and says why", () => {
    let d = addPart(emptyDoc(), "pipe", 0, 0).doc;                  // p1: in (0,10) out (120,10)
    d = addPart(d, "pipe", 200, 0).doc;                              // p2
    d = connect(d, { part: "p1", port: "out" }, { part: "p2", port: "in" }).doc;
    d = patchPart(d, "p1", { x: 0, y: 0 });
    d = patchPart(d, "p2", { x: -110, y: 0 });                      // p2's outlet (10,10) now sits next to p1's inlet (0,10): joining them would close a circle
    const r = findSnap(d, "p2");
    expect(r.hit).toBeNull();
    expect(r.refused).toMatch(/circle/);
  });
  it("sets the flow arrow of a sensor snapped onto a pipe along the pipe", () => {
    const d = addPart(emptyDoc(), "pipe", 100, 100, { rot: 180 }).doc;
    const r = addMeter(d, { type: "flow", number: 2, name: "N2", label: "Node 2", nodeRef: 2 } as AvailableNode, 160 - 14, 110 - 14 + 20, SNAP_DIST);   // 20 px below the pipe: only the magnet distance reaches it
    const m = r.doc.parts.find((p) => p.id === r.id)!;
    expect(m.pipe).toBe("p1");
    expect(meterArrowDeg(r.doc, m)).toBe(180);
  });
  it("detaches only the links that were pulled clearly apart", () => {
    let d = connect(two(0, 0), { part: "src1", port: "out" }, { part: "p1", port: "in" }).doc;
    const nudged = patchPart(d, "p1", { x: 90, y: 10 });
    expect(detachPulledLinks(d, nudged, "p1").links).toHaveLength(1);          // 10 px: keeps the link
    const pulled = patchPart(d, "p1", { x: 200, y: 60 });
    expect(detachPulledLinks(d, pulled, "p1").links).toHaveLength(0);
  });
  it("merges two commits of one gesture into a single undo step", () => {
    const h0 = newHistory(emptyDoc("a"));
    const d1 = addPart(emptyDoc("a"), "pipe", 0, 0).doc, d2 = addPart(d1, "pipe", 200, 0).doc;
    const h = pushHistoryMerged(pushHistoryMerged(h0, d1, false), d2, true);
    expect(h.past).toHaveLength(1);
    expect(undo(h).present).toEqual(emptyDoc("a"));
  });
});
