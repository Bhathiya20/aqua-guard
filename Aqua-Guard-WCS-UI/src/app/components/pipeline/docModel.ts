// Pure pipeline-document logic for the editor: geometry, connection rules, rotation, undo/redo, validation mapping, live zone colours.
// No React in here, so everything is unit-tested (docModel.test.ts).  The document is the single source of truth; React Flow nodes/edges are derived from it.
import type { AvailableNode, DocLink, DocPart, LiveZone, PartKind, PipelineDoc, ValidationMessage, ZoneStatus } from "../../lib/v2";

export const GRID = 20;
export type Rot = 0 | 90 | 180 | 270;
export type Side = "left" | "right" | "top" | "bottom";
export const GRAPH_KINDS: Exclude<PartKind, "node">[] = ["source", "pipe", "elbow", "valve", "tee", "cross", "outlet", "cap"];
export const OUTLET_TYPES = ["tap", "shower", "garden", "tank"] as const;

export const KIND_NAMES: Record<PartKind, string> = {
  source: "Main supply", pipe: "Pipe", elbow: "L elbow", valve: "Valve", tee: "T junction", cross: "Cross junction", outlet: "Outlet", cap: "End cap", node: "Node",
};
/** Port names per kind, in the same order as the backend (pipeline/model.py KINDS). */
export const PORTS: Record<Exclude<PartKind, "node">, string[]> = {
  source: ["out"], pipe: ["in", "out"], elbow: ["in", "out"], valve: ["in", "out"], tee: ["in", "out1", "out2"], cross: ["in", "out1", "out2", "out3"], outlet: ["in"], cap: ["in"],
};
/** Which side of the unrotated part each port sits on. */
const SIDES: Record<Exclude<PartKind, "node">, Record<string, Side>> = {
  source: { out: "right" }, pipe: { in: "left", out: "right" }, valve: { in: "left", out: "right" }, elbow: { in: "left", out: "bottom" },
  tee: { in: "left", out1: "right", out2: "bottom" }, cross: { in: "left", out1: "right", out2: "top", out3: "bottom" }, outlet: { in: "left" }, cap: { in: "left" },
};
const BASE: Record<PartKind, { w: number; h: number }> = {
  source: { w: 80, h: 40 }, pipe: { w: 120, h: 20 }, elbow: { w: 40, h: 40 }, valve: { w: 60, h: 20 }, tee: { w: 60, h: 60 }, cross: { w: 60, h: 60 }, outlet: { w: 80, h: 40 }, cap: { w: 40, h: 20 }, node: { w: 28, h: 28 },
};
const ORDER: Side[] = ["top", "right", "bottom", "left"];       // clockwise

export const snap = (v: number) => Math.round(v / GRID) * GRID;
export const rotOf = (p: DocPart): Rot => ((((p.rot ?? 0) % 360) + 360) % 360) as Rot;
export const isGraphKind = (k: PartKind): k is Exclude<PartKind, "node"> => k !== "node";

export function rotateSide(side: Side, rot: Rot): Side {
  return ORDER[(ORDER.indexOf(side) + rot / 90) % 4];
}
export function portSide(kind: Exclude<PartKind, "node">, port: string, rot: Rot): Side | null {
  const s = SIDES[kind][port];
  return s ? rotateSide(s, rot) : null;
}
export function partSize(p: DocPart): { w: number; h: number } {
  const b = BASE[p.kind];
  return rotOf(p) % 180 === 0 ? b : { w: b.h, h: b.w };
}

// ------------------------------------------------------------------ construction
export function emptyDoc(name = "New network"): PipelineDoc { return { schema: 1, name, parts: [], links: [] }; }

export function nextId(doc: PipelineDoc, prefix: string): string {
  const used = new Set([...doc.parts.map((p) => p.id), ...doc.links.map((l) => l.id)]);
  for (let i = 1; ; i++) if (!used.has(`${prefix}${i}`)) return `${prefix}${i}`;
}
const PREFIX: Record<PartKind, string> = { source: "src", pipe: "p", elbow: "l", valve: "v", tee: "t", cross: "x", outlet: "o", cap: "c", node: "n" };

export function addPart(doc: PipelineDoc, kind: PartKind, x: number, y: number, extra: Partial<DocPart> = {}): { doc: PipelineDoc; id: string } {
  if (kind === "source" && doc.parts.some((p) => p.kind === "source")) return { doc, id: doc.parts.find((p) => p.kind === "source")!.id };   // only one main supply
  const id = nextId(doc, PREFIX[kind]);
  const part: DocPart = { id, kind, x: snap(x), y: snap(y), rot: 0, ...extra };
  if (kind === "pipe") { part.lengthM ??= 3; }
  if (kind === "outlet") part.outletType ??= "tap";
  return { doc: { ...doc, parts: [...doc.parts, part] }, id };       // no label stored: the display name comes from partLabels() (type + order), like the backend
}

export function addMeter(doc: PipelineDoc, node: AvailableNode, x: number, y: number, slack = 14): { doc: PipelineDoc; id: string } {
  if (doc.parts.some((p) => p.kind === "node" && p.number === node.number && (p.nodeType ?? "flow") === node.type)) {
    return { doc, id: doc.parts.find((p) => p.kind === "node" && p.number === node.number)!.id };               // a node can be placed once
  }
  const id = nextId(doc, "n");
  const part: DocPart = { id, kind: "node", nodeType: node.type as "flow" | "rain", number: node.number, nodeRef: node.nodeRef, name: node.name, pipe: null, pos: 0.5, dir: "forward", x: Math.round(x), y: Math.round(y) };
  return attachMeterAt({ ...doc, parts: [...doc.parts, part] }, id, slack);
}

export const partById = (doc: PipelineDoc, id: string) => doc.parts.find((p) => p.id === id);
const update = (doc: PipelineDoc, id: string, f: (p: DocPart) => DocPart): PipelineDoc => ({ ...doc, parts: doc.parts.map((p) => (p.id === id ? f(p) : p)) });
export const patchPart = (doc: PipelineDoc, id: string, patch: Partial<DocPart>) => update(doc, id, (p) => ({ ...p, ...patch }));
export const movePart = (doc: PipelineDoc, id: string, x: number, y: number) => patchPart(doc, id, { x, y });

export function rotatePart(doc: PipelineDoc, id: string): PipelineDoc {
  const p = partById(doc, id);
  if (!p || p.kind === "node") return doc;
  return patchPart(doc, id, { rot: ((rotOf(p) + 90) % 360) as Rot });
}

export function removePart(doc: PipelineDoc, id: string): PipelineDoc {
  const p = partById(doc, id);
  if (!p) return doc;
  let parts = doc.parts.filter((x) => x.id !== id);
  if (p.kind === "pipe") parts = parts.map((x) => (x.kind === "node" && x.pipe === id ? { ...x, pipe: null, x: Math.round(meterPoint(doc, x).x - BASE.node.w / 2), y: Math.round(meterPoint(doc, x).y - BASE.node.h / 2) } : x));
  return { ...doc, parts, links: doc.links.filter((l) => l.from.part !== id && l.to.part !== id) };
}
export const removeLink = (doc: PipelineDoc, id: string): PipelineDoc => ({ ...doc, links: doc.links.filter((l) => l.id !== id) });

// ------------------------------------------------------------------ connections
export interface ConnectResult { ok: boolean; reason?: string; from?: { part: string; port: string }; to?: { part: string; port: string } }

const isOut = (kind: PartKind, port: string) => kind !== "node" && port !== "in" && PORTS[kind as Exclude<PartKind, "node">].includes(port);
const isIn = (kind: PartKind, port: string) => kind !== "node" && port === "in" && PORTS[kind as Exclude<PartKind, "node">].includes("in");

function reaches(doc: PipelineDoc, start: string, target: string): boolean {
  const seen = new Set<string>(), todo = [start];
  while (todo.length) {
    const cur = todo.pop()!;
    if (cur === target) return true;
    if (seen.has(cur)) continue;
    seen.add(cur);
    doc.links.forEach((l) => { if (l.from.part === cur) todo.push(l.to.part); });
  }
  return false;
}

/** Decides whether two ports can be joined and in which direction (water flows from an outlet port into an inlet port). */
export function canConnect(doc: PipelineDoc, a: { part: string; port: string }, b: { part: string; port: string }): ConnectResult {
  const pa = partById(doc, a.part), pb = partById(doc, b.part);
  if (!pa || !pb) return { ok: false, reason: "That part no longer exists." };
  if (pa.kind === "node" || pb.kind === "node") return { ok: false, reason: "A node is a sensor on a pipe: drop it on a pipe instead of connecting it." };
  if (pa.id === pb.id) return { ok: false, reason: "A part cannot be connected to itself." };
  let from = a, to = b;
  if (isIn(pa.kind, a.port) && isOut(pb.kind, b.port)) { from = b; to = a; }
  else if (!(isOut(pa.kind, a.port) && isIn(pb.kind, b.port))) return { ok: false, reason: "Connect an outlet port (where water leaves) to an inlet port (where water enters)." };
  const used = (r: { part: string; port: string }) => doc.links.some((l) => (l.from.part === r.part && l.from.port === r.port) || (l.to.part === r.part && l.to.port === r.port));
  if (used(from) || used(to)) return { ok: false, reason: "That port is already connected. Remove the existing connection first." };
  if (reaches(doc, to.part, from.part)) return { ok: false, reason: "That would make the water run in a circle." };
  return { ok: true, from, to };
}

export function connect(doc: PipelineDoc, a: { part: string; port: string }, b: { part: string; port: string }): { doc: PipelineDoc; result: ConnectResult } {
  const result = canConnect(doc, a, b);
  if (!result.ok) return { doc, result };
  const link: DocLink = { id: nextId(doc, "e"), from: result.from!, to: result.to! };
  return { doc: { ...doc, links: [...doc.links, link] }, result };
}

// ------------------------------------------------------------------ nodes (meters) on pipes
/** Where water enters the pipe, by rotation: 0 = left edge, 90 = top, 180 = right, 270 = bottom. */
export function pipeAxis(p: DocPart): { horizontal: boolean; reversed: boolean } {
  const r = rotOf(p);
  return { horizontal: r === 0 || r === 180, reversed: r === 180 || r === 270 };
}

/** Centre of a meter on the canvas: on its pipe at `pos`, or at its own x/y when it is not on a pipe. */
export function meterPoint(doc: PipelineDoc, m: DocPart): { x: number; y: number } {
  const pipe = m.pipe ? partById(doc, m.pipe) : undefined;
  if (pipe && pipe.kind === "pipe") {
    const { w, h } = partSize(pipe), { horizontal, reversed } = pipeAxis(pipe);
    const t = reversed ? 1 - (m.pos ?? 0.5) : (m.pos ?? 0.5);
    const x = pipe.x ?? 0, y = pipe.y ?? 0;
    return horizontal ? { x: x + t * w, y: y + h / 2 } : { x: x + w / 2, y: y + t * h };
  }
  return { x: (m.x ?? 0) + BASE.node.w / 2, y: (m.y ?? 0) + BASE.node.h / 2 };
}

/** Direction the flow arrow points on screen (degrees, 0 = right, 90 = down) for a node on a pipe. */
export function meterArrowDeg(doc: PipelineDoc, m: DocPart): number {
  const pipe = m.pipe ? partById(doc, m.pipe) : undefined;
  const base = pipe ? rotOf(pipe) : 0;                         // water runs "in -> out": rot 0 right, 90 down, 180 left, 270 up
  return (base + (m.dir === "reverse" ? 180 : 0)) % 360;
}

/** The pipe whose box contains the point (nearest centre wins), and the position along it measured from its inlet. */
export function pipeUnder(doc: PipelineDoc, cx: number, cy: number, slack = 14): { pipe: DocPart; pos: number } | null {
  let best: { pipe: DocPart; pos: number; d: number } | null = null;
  for (const p of doc.parts) {
    if (p.kind !== "pipe") continue;
    const { w, h } = partSize(p), x = p.x ?? 0, y = p.y ?? 0;
    if (cx < x - slack || cx > x + w + slack || cy < y - slack || cy > y + h + slack) continue;
    const { horizontal, reversed } = pipeAxis(p);
    let t = horizontal ? (cx - x) / w : (cy - y) / h;
    t = Math.min(0.95, Math.max(0.05, t));
    const pos = reversed ? 1 - t : t;
    const d = Math.hypot(cx - (x + w / 2), cy - (y + h / 2));
    if (!best || d < best.d) best = { pipe: p, pos: Math.round(pos * 100) / 100, d };
  }
  return best ? { pipe: best.pipe, pos: best.pos } : null;
}

/** After a meter was dropped/dragged: snap it onto the pipe below it (sets pipe + pos) or detach it. */
export function attachMeterAt(doc: PipelineDoc, id: string, slack = 14): { doc: PipelineDoc; id: string } {
  const m = partById(doc, id);
  if (!m || m.kind !== "node") return { doc, id };
  const c = { x: (m.x ?? 0) + BASE.node.w / 2, y: (m.y ?? 0) + BASE.node.h / 2 };
  const hit = pipeUnder(doc, c.x, c.y, slack);
  // a sensor snapped onto a pipe points along that pipe's flow
  return { doc: hit ? patchPart(doc, id, { pipe: hit.pipe.id, pos: hit.pos, dir: "forward" }) : patchPart(doc, id, { pipe: null }), id };
}

// ------------------------------------------------------------------ magnetic connect
export const SNAP_DIST = 24;
type PortRef = { part: string; port: string };

/** Canvas position of a port: the middle of the side it sits on. */
export function portPoint(p: DocPart, port: string): { x: number; y: number } | null {
  if (p.kind === "node") return null;
  const side = portSide(p.kind, port, rotOf(p));
  if (!side) return null;
  const { w, h } = partSize(p), x = p.x ?? 0, y = p.y ?? 0;
  return side === "left" ? { x, y: y + h / 2 } : side === "right" ? { x: x + w, y: y + h / 2 } : side === "top" ? { x: x + w / 2, y } : { x: x + w / 2, y: y + h };
}
const portUsed = (doc: PipelineDoc, part: string, port: string) => doc.links.some((l) => (l.from.part === part && l.from.port === port) || (l.to.part === part && l.to.port === port));
export const freePorts = (doc: PipelineDoc, p: DocPart): string[] => (p.kind === "node" ? [] : PORTS[p.kind].filter((q) => !portUsed(doc, p.id, q)));

export interface SnapHit { mine: PortRef; other: PortRef; dx: number; dy: number; dist: number }
export interface SnapResult { hit: SnapHit | null; refused: string | null }

/** Closest compatible pair (my free port, another part's free port, outlet -> inlet) within `maxDist`, and how far the part must move so the two ports touch. */
export function findSnap(doc: PipelineDoc, id: string, maxDist = SNAP_DIST): SnapResult {
  const me = partById(doc, id);
  if (!me || me.kind === "node") return { hit: null, refused: null };
  const cands: SnapHit[] = [];
  for (const mp of freePorts(doc, me)) {
    const a = portPoint(me, mp)!;
    for (const q of doc.parts) {
      if (q.id === id || q.kind === "node") continue;
      for (const qp of freePorts(doc, q)) {
        if ((mp === "in") === (qp === "in")) continue;          // an inlet only meets an outlet
        const b = portPoint(q, qp)!, dist = Math.hypot(a.x - b.x, a.y - b.y);
        if (dist <= maxDist) cands.push({ mine: { part: id, port: mp }, other: { part: q.id, port: qp }, dx: b.x - a.x, dy: b.y - a.y, dist });
      }
    }
  }
  cands.sort((x, y) => x.dist - y.dist);
  let refused: string | null = null;
  for (const c of cands) {
    const r = canConnect(doc, c.mine, c.other);
    if (r.ok) return { hit: c, refused: null };
    refused ??= r.reason ?? null;
  }
  return { hit: null, refused };
}

/** Move the part so the two ports touch, and join them. */
export function applySnap(doc: PipelineDoc, hit: SnapHit): PipelineDoc {
  const p = partById(doc, hit.mine.part)!;
  return connect(patchPart(doc, p.id, { x: (p.x ?? 0) + hit.dx, y: (p.y ?? 0) + hit.dy }), hit.mine, hit.other).doc;
}

/** Links of `id` that touched in `before` (<= maxDist) but are further apart in `now`: the part was pulled away on purpose; tiny moves keep their links. */
export function detachPulledLinks(before: PipelineDoc, now: PipelineDoc, id: string, maxDist = SNAP_DIST): PipelineDoc {
  const gap = (d: PipelineDoc, l: DocLink) => {
    const a = partById(d, l.from.part), b = partById(d, l.to.part);
    const pa = a && portPoint(a, l.from.port), pb = b && portPoint(b, l.to.port);
    return pa && pb ? Math.hypot(pa.x - pb.x, pa.y - pb.y) : 0;
  };
  const drop = new Set(now.links.filter((l) => (l.from.part === id || l.to.part === id) && gap(before, l) <= maxDist && gap(now, l) > maxDist).map((l) => l.id));
  return drop.size ? { ...now, links: now.links.filter((l) => !drop.has(l.id)) } : now;
}

// ------------------------------------------------------------------ palette
export function unplacedNodes(available: AvailableNode[], doc: PipelineDoc): AvailableNode[] {
  const placed = new Set(doc.parts.filter((p) => p.kind === "node").map((p) => `${p.nodeType ?? "flow"}:${p.number}`));
  return available.filter((n) => !placed.has(`${n.type}:${n.number}`));
}
export const nodeLabel = (n: { number: number; name?: string }) => `Node ${n.number}${n.name ? ` - ${n.name}` : ""}`;

// ------------------------------------------------------------------ React Flow mapping
export interface FlowNodeData { part: DocPart; rot: Rot; w: number; h: number; arrowDeg?: number; [k: string]: unknown }
export interface FlowNode { id: string; type: "part" | "meter"; position: { x: number; y: number }; data: FlowNodeData; style: { width: number; height: number }; width: number; height: number; zIndex: number; draggable?: boolean; selectable?: boolean }
export interface FlowEdge { id: string; source: string; sourceHandle: string; target: string; targetHandle: string; type: "smoothstep"; data?: Record<string, unknown> }

export function docToFlow(doc: PipelineDoc): { nodes: FlowNode[]; edges: FlowEdge[] } {
  const nodes: FlowNode[] = doc.parts.map((p) => {
    if (p.kind === "node") {
      const c = meterPoint(doc, p);
      return { id: p.id, type: "meter", position: { x: c.x - BASE.node.w / 2, y: c.y - BASE.node.h / 2 }, data: { part: p, rot: 0, w: BASE.node.w, h: BASE.node.h, arrowDeg: meterArrowDeg(doc, p) },
               style: { width: BASE.node.w, height: BASE.node.h }, width: BASE.node.w, height: BASE.node.h, zIndex: 10 };
    }
    const { w, h } = partSize(p);
    return { id: p.id, type: "part", position: { x: p.x ?? 0, y: p.y ?? 0 }, data: { part: p, rot: rotOf(p), w, h }, style: { width: w, height: h }, width: w, height: h, zIndex: 1 };
  });
  const edges: FlowEdge[] = doc.links.map((l) => ({ id: l.id, source: l.from.part, sourceHandle: l.from.port, target: l.to.part, targetHandle: l.to.port, type: "smoothstep" }));
  return { nodes, edges };
}

/** Inverse of docToFlow for the layout fields: positions of graph parts and free meters; links from the edges.  Everything else is kept from `base`. */
export function flowToDoc(base: PipelineDoc, nodes: Array<{ id: string; position: { x: number; y: number } }>, edges: Array<{ id: string; source: string; sourceHandle?: string | null; target: string; targetHandle?: string | null }>): PipelineDoc {
  const pos = new Map(nodes.map((n) => [n.id, n.position]));
  const parts = base.parts.map((p) => {
    const q = pos.get(p.id);
    if (!q) return p;
    if (p.kind === "node" && p.pipe) return p;                       // attached meters are placed by their pipe
    return { ...p, x: Math.round(q.x), y: Math.round(q.y) };
  });
  const links: DocLink[] = edges.map((e) => ({ id: e.id, from: { part: e.source, port: e.sourceHandle ?? "out" }, to: { part: e.target, port: e.targetHandle ?? "in" } }));
  return { ...base, parts, links };
}

// ------------------------------------------------------------------ history (undo / redo)
export interface History { past: PipelineDoc[]; present: PipelineDoc; future: PipelineDoc[] }
const LIMIT = 100;
export const newHistory = (doc: PipelineDoc): History => ({ past: [], present: doc, future: [] });
export function pushHistory(h: History, doc: PipelineDoc): History {
  if (doc === h.present || JSON.stringify(doc) === JSON.stringify(h.present)) return h;
  return { past: [...h.past, h.present].slice(-LIMIT), present: doc, future: [] };
}
/** Like pushHistory, but replaces the newest entry when `merge` (one gesture that commits twice, e.g. Delete removing a part and its links). */
export function pushHistoryMerged(h: History, doc: PipelineDoc, merge: boolean): History {
  if (!merge || h.past.length === 0) return pushHistory(h, doc);
  if (JSON.stringify(doc) === JSON.stringify(h.present)) return h;
  return { past: h.past, present: doc, future: [] };
}
export const canUndo = (h: History) => h.past.length > 0;
export const canRedo = (h: History) => h.future.length > 0;
export function undo(h: History): History { return canUndo(h) ? { past: h.past.slice(0, -1), present: h.past[h.past.length - 1], future: [h.present, ...h.future] } : h; }
export function redo(h: History): History { return canRedo(h) ? { past: [...h.past, h.present], present: h.future[0], future: h.future.slice(1) } : h; }

// ------------------------------------------------------------------ validation messages -> parts
export type Sev = "error" | "warning" | "info";
const RANK: Record<Sev, number> = { error: 3, warning: 2, info: 1 };
/** part id -> worst severity of the messages that name it (used to outline parts on the canvas). */
export function partSeverities(messages: ValidationMessage[], doc?: PipelineDoc): Record<string, Sev> {
  const out: Record<string, Sev> = {};
  const known = doc ? new Set(doc.parts.map((p) => p.id)) : null;
  for (const m of messages) {
    if (!m.partId || (known && !known.has(m.partId))) continue;
    if (!out[m.partId] || RANK[m.severity] > RANK[out[m.partId]]) out[m.partId] = m.severity;
  }
  return out;
}
/** The part to select/zoom to when a message is clicked (null for document-wide messages). */
export const messageTarget = (m: ValidationMessage, doc: PipelineDoc): string | null => (m.partId && doc.parts.some((p) => p.id === m.partId) ? m.partId : null);

// ------------------------------------------------------------------ live view: zone colours
export const ZONE_COLORS: Record<ZoneStatus, { fill: string; stroke: string; label: string }> = {
  normal: { fill: "#dcfce7", stroke: "#16a34a", label: "Normal" },
  suspected: { fill: "#fef3c7", stroke: "#d97706", label: "Suspected leak" },
  confirmed: { fill: "#fee2e2", stroke: "#dc2626", label: "Confirmed leak" },
  not_monitored: { fill: "#e2e8f0", stroke: "#64748b", label: "Not monitored" },
  incomplete: { fill: "#e0e7ff", stroke: "#6366f1", label: "Incomplete data" },
  calibrating: { fill: "#fef9c3", stroke: "#ca8a04", label: "Paused: calibrating" },
};
export const zoneColor = (status: string | undefined) => ZONE_COLORS[(status as ZoneStatus) in ZONE_COLORS ? (status as ZoneStatus) : "not_monitored"];
const SEVERITY: Record<ZoneStatus, number> = { confirmed: 5, suspected: 4, incomplete: 3, calibrating: 3, normal: 2, not_monitored: 1 };

/** part id -> the zone that colours it.  A part shared by two zones shows the more alarming one; pipes also get a `split` when two zones share them. */
export function zoneOfParts(zones: LiveZone[]): { parts: Record<string, LiveZone>; pipeSplit: Record<string, { upstream: LiveZone; downstream: LiveZone; at: number }> } {
  const parts: Record<string, LiveZone> = {};
  const per: Record<string, Array<{ z: LiveZone; f: number }>> = {};
  const put = (id: string, z: LiveZone) => { if (!parts[id] || SEVERITY[z.status] > SEVERITY[parts[id].status]) parts[id] = z; };
  for (const z of zones) {
    z.parts.forEach((id) => put(id, z));
    (z.outletIds ?? []).forEach((id) => put(id, z));
    z.pipes.forEach((p) => { (per[p.id] ??= []).push({ z, f: p.fraction }); put(p.id, z); });
  }
  const pipeSplit: Record<string, { upstream: LiveZone; downstream: LiveZone; at: number }> = {};
  for (const [id, list] of Object.entries(per)) {
    if (list.length === 2) {
      const [a, b] = list[0].f <= list[1].f ? [list[0], list[1]] : [list[1], list[0]];
      pipeSplit[id] = { upstream: a.z, downstream: b.z, at: a.f };
    }
  }
  return { parts, pipeSplit };
}

// ------------------------------------------------------------------ names (mirror of backend pipeline/model.py default_label)
const OUTLET_BASE: Record<string, string> = { tap: "Tap", shower: "Shower", garden: "Garden tap", tank: "Tank inlet" };
/** part id -> display name: the part's own label, else 'Tap 1', 'Shower 1', 'Garden tap', 'End cap 1', 'Pipe 6' by type and order. Nodes: 'Node N'. */
export function partLabels(doc: PipelineDoc): Record<string, string> {
  const out: Record<string, string> = {}, counters: Record<string, number> = {};
  for (const p of doc.parts) {
    if (p.kind === "node") { out[p.id] = (p.label ?? "").trim() || `Node ${p.number}`; continue; }
    const base = p.kind === "outlet" ? (OUTLET_BASE[p.outletType ?? "tap"] ?? "Outlet") : KIND_NAMES[p.kind];
    const key = `${p.kind}:${base}`;
    counters[key] = (counters[key] ?? 0) + 1;
    const n = counters[key];
    const auto = p.kind === "source" || (p.kind === "outlet" && base === "Garden tap" && n === 1) ? base : `${base} ${n}`;
    out[p.id] = (p.label ?? "").trim() || auto;
  }
  return out;
}

// ------------------------------------------------------------------ live view: one tint per zone (fill), the leak status stays on the border / badge
export const ZONE_TINTS = ["#bfdbfe", "#fbcfe8", "#bbf7d0", "#fde68a", "#ddd6fe", "#fed7aa", "#a5f3fc", "#e9d5ff"];
export const zoneTint = (zones: LiveZone[], id: string): string => ZONE_TINTS[Math.max(0, zones.findIndex((z) => z.id === id)) % ZONE_TINTS.length];

/** Put a paired node (not yet in the design) in the middle of the given pipe: the action behind "Place Node N here". */
export function placeNodeOnPipe(doc: PipelineDoc, node: AvailableNode, pipeId: string): { doc: PipelineDoc; id: string } | null {
  const pipe = partById(doc, pipeId);
  if (!pipe || pipe.kind !== "pipe") return null;
  if (doc.parts.some((p) => p.kind === "node" && p.number === node.number && (p.nodeType ?? "flow") === node.type)) return null;
  const { w, h } = partSize(pipe);
  const cx = (pipe.x ?? 0) + w / 2, cy = (pipe.y ?? 0) + h / 2;
  const added = addMeter(doc, node, cx - BASE.node.w / 2, cy - BASE.node.h / 2, 30);
  return partById(added.doc, added.id)?.pipe === pipeId ? added : { doc: patchPart(added.doc, added.id, { pipe: pipeId, pos: 0.5, dir: "forward" }), id: added.id };
}
