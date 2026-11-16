import { createContext, memo, useContext, useEffect } from "react";
import { Handle, Position, useUpdateNodeInternals, type NodeProps } from "@xyflow/react";
import { portSide, PORTS, KIND_NAMES, zoneColor, type FlowNodeData, type Sev } from "./docModel";
import type { LiveZone, PartKind } from "../../lib/v2";

export interface LiveFlow { flowLpm: number | null; fresh: boolean }
export interface CanvasCtxValue {
  mode: "design" | "live";
  severities: Record<string, Sev>;
  zoneOfPart: Record<string, LiveZone>;
  pipeSplit: Record<string, { upstream: LiveZone; downstream: LiveZone; at: number }>;
  live: Record<string, LiveFlow>;
  highlight: string | null;
  pendingPort: { part: string; port: string } | null;
  snapHint: { a: { part: string; port: string }; b: { part: string; port: string } } | null;
  readOnly: boolean;
  labels: Record<string, string>;                                  // part id -> display name (own label or default by type/order)
  zoneTints: Record<string, string>;                               // zone id -> fill tint (Live view)
  zoneTag: Record<string, LiveZone>;                               // part id -> the zone whose name is written next to this part (one anchor per zone)
  rename: (id: string) => void;                                    // double-click
}
export const CanvasCtx = createContext<CanvasCtxValue>({ mode: "design", severities: {}, zoneOfPart: {}, pipeSplit: {}, live: {}, highlight: null, pendingPort: null, snapHint: null, readOnly: false, labels: {}, zoneTints: {}, zoneTag: {}, rename: () => {} });

const SIDE_POS: Record<string, Position> = { left: Position.Left, right: Position.Right, top: Position.Top, bottom: Position.Bottom };
const SEV_RING: Record<Sev, string> = { error: "#dc2626", warning: "#d97706", info: "#2563eb" };

const FILL: Record<string, string> = {
  source: "#dbeafe", pipe: "#e2e8f0", elbow: "#e2e8f0", valve: "#fde68a", tee: "#e2e8f0", cross: "#e2e8f0", outlet: "#cffafe", cap: "#cbd5e1",
};
const OUTLET_NAMES: Record<string, string> = { tap: "Tap", shower: "Shower", garden: "Garden", tank: "Tank inlet" };

/** A pipe-network part: a box with one handle per port (rotated with the part). */
export const PartNode = memo(function PartNode({ id, data, selected }: NodeProps) {
  const d = data as unknown as FlowNodeData;
  const ctx = useContext(CanvasCtx);
  const upd = useUpdateNodeInternals();
  const p = d.part;
  const kind = p.kind as Exclude<PartKind, "node">;
  useEffect(() => { upd(id); }, [d.rot, id, upd]);

  const live = ctx.mode === "live";
  const zone = ctx.zoneOfPart[id];
  const col = live ? zoneColor(zone?.status ?? "not_monitored") : null;
  const split = ctx.pipeSplit[id];
  const sev = ctx.severities[id];
  const tint = (z: LiveZone | undefined) => (z ? ctx.zoneTints[z.id] ?? zoneColor(z.status).fill : zoneColor("not_monitored").fill);
  let background: string = live ? tint(zone) : FILL[kind];
  if (live && split) {
    const a = { fill: tint(split.upstream) }, b = { fill: tint(split.downstream) };
    const at = Math.round(split.at * 100);
    const dirDeg = { 0: 90, 90: 180, 180: 270, 270: 0 }[d.rot as 0 | 90 | 180 | 270];
    background = `linear-gradient(${dirDeg}deg, ${a.fill} ${at}%, ${b.fill} ${at}%)`;
  }
  const border = live ? (split ? "#64748b" : col!.stroke) : selected ? "#2563eb" : "#64748b";     // status colour = border (and the zone badge)
  const ring = ctx.highlight === id ? "0 0 0 4px rgba(37,99,235,.45)" : sev ? `0 0 0 3px ${SEV_RING[sev]}` : undefined;
  const label = ctx.labels[id] || p.label || (kind === "outlet" ? OUTLET_NAMES[p.outletType ?? "tap"] : KIND_NAMES[kind]);
  const showName = kind === "outlet" || kind === "cap" || kind === "valve" || kind === "source";      // named parts: the name is always visible next to them
  const tag = ctx.zoneTag[id];
  const small = d.w < 60 || d.h < 30;
  const vertical = d.rot % 180 !== 0;

  return (
    <div data-testid={`part-${id}`} data-kind={kind} title={`${label} (${id}) - double-click to rename`} onDoubleClick={(e) => { e.stopPropagation(); if (!ctx.readOnly) ctx.rename(id); }}
      style={{ width: d.w, height: d.h, background, border: `2px solid ${border}`, borderRadius: kind === "pipe" ? 3 : 8, boxShadow: ring, position: "relative" }}
      className="flex items-center justify-center text-[10px] font-medium text-slate-800 select-none">
      <div style={{ position: "absolute", inset: 0, overflow: "hidden", borderRadius: "inherit", pointerEvents: "none" }} className="flex items-center justify-center">
      {kind === "elbow" && <svg width="100%" height="100%" viewBox="0 0 40 40" style={{ transform: `rotate(${d.rot}deg)` }} aria-hidden><path d="M0 20 H20 V40" stroke="#475569" strokeWidth="6" fill="none" /></svg>}
      {kind === "tee" && <svg width="100%" height="100%" viewBox="0 0 60 60" style={{ transform: `rotate(${d.rot}deg)` }} aria-hidden><path d="M0 30 H60 M30 30 V60" stroke="#475569" strokeWidth="6" fill="none" /></svg>}
      {kind === "cross" && <svg width="100%" height="100%" viewBox="0 0 60 60" style={{ transform: `rotate(${d.rot}deg)` }} aria-hidden><path d="M0 30 H60 M30 0 V60" stroke="#475569" strokeWidth="6" fill="none" /></svg>}
      {kind === "valve" && <svg width="100%" height="100%" viewBox="0 0 60 20" style={{ transform: `rotate(${d.rot}deg)` }} aria-hidden><path d="M12 2 L30 10 L12 18 Z M48 2 L30 10 L48 18 Z" fill="#475569" /></svg>}
      {kind === "cap" && <span className="font-bold">■</span>}
      {kind === "pipe" && !vertical && !small && <span className="px-1 truncate">{p.lengthM ? `${p.lengthM} m` : ""}</span>}
      </div>

      {showName && <span data-testid={`label-${id}`} className="pointer-events-none absolute left-1/2 top-full z-10 mt-0.5 -translate-x-1/2 whitespace-nowrap rounded bg-white/90 px-1 text-[10px] font-semibold leading-tight text-slate-800 shadow-sm">{label}</span>}
      {tag && <span data-testid={`zonetag-${id}`} className="pointer-events-none absolute bottom-full left-0 z-10 mb-0.5 max-w-[190px] truncate rounded px-1.5 py-0.5 text-[10px] font-semibold leading-tight text-slate-900 shadow-sm" style={{ background: ctx.zoneTints[tag.id], border: `2px solid ${zoneColor(tag.status).stroke}` }}>{tag.name} · {zoneColor(tag.status).label}</span>}

      {PORTS[kind].map((port) => {
        const side = portSide(kind, port, d.rot as 0 | 90 | 180 | 270)!;
        const pending = ctx.pendingPort && ctx.pendingPort.part === id && ctx.pendingPort.port === port;
        const snapping = !!ctx.snapHint && [ctx.snapHint.a, ctx.snapHint.b].some((r) => r.part === id && r.port === port);
        return (
          <Handle key={port} id={port} type={port === "in" ? "target" : "source"} position={SIDE_POS[side]} isConnectable={!ctx.readOnly && !live}
            title={`${port === "in" ? "inlet" : "outlet"} ${port}`} data-testid={`handle-${id}-${port}`}
            data-snap={snapping ? "1" : undefined}
            style={{ width: snapping ? 16 : 12, height: snapping ? 16 : 12, background: pending ? "#2563eb" : snapping ? "#f59e0b" : port === "in" ? "#0ea5e9" : "#16a34a", border: "2px solid white", boxShadow: snapping ? "0 0 0 5px rgba(245,158,11,.55)" : undefined, zIndex: 5 }} />
        );
      })}
    </div>
  );
});

/** A flow node placed on a pipe: round sensor symbol, arrow along the flow, live flow underneath in the Live view. */
export const MeterNode = memo(function MeterNode({ id, data, selected }: NodeProps) {
  const d = data as unknown as FlowNodeData;
  const ctx = useContext(CanvasCtx);
  const p = d.part;
  const live = ctx.mode === "live" ? ctx.live[id] : undefined;
  const sev = ctx.severities[id];
  const ring = ctx.highlight === id ? "0 0 0 4px rgba(37,99,235,.45)" : sev ? `0 0 0 3px ${SEV_RING[sev]}` : undefined;
  return (
    <div data-testid={`part-${id}`} data-kind="node" title={`Node ${p.number}${p.name ? ` - ${p.name}` : ""}${p.pipe ? "" : " (not on a pipe yet)"}`} style={{ width: d.w, height: d.h, position: "relative" }}>
      <div style={{ width: d.w, height: d.h, borderRadius: "50%", background: p.pipe ? "#1d4ed8" : "#f59e0b", border: `2px solid ${selected ? "#93c5fd" : "white"}`, boxShadow: ring ?? "0 1px 4px rgba(0,0,0,.4)" }}
        className="flex items-center justify-center text-[11px] font-bold text-white">
        {p.number}
      </div>
      <svg width={d.w + 20} height={d.h + 20} viewBox={`${-10} ${-10} ${d.w + 20} ${d.h + 20}`} style={{ position: "absolute", left: -10, top: -10, pointerEvents: "none" }} aria-hidden>
        <g transform={`translate(${d.w / 2} ${d.h / 2}) rotate(${d.arrowDeg ?? 0})`}><path d={`M${d.w / 2 + 1} 0 l8 -5 v10 z`} fill="#0f172a" /></g>
      </svg>
      {live && (
        <span data-testid={`flow-${id}`} className="absolute left-1/2 top-full mt-0.5 -translate-x-1/2 whitespace-nowrap rounded bg-slate-900/85 px-1.5 py-0.5 text-[10px] font-mono text-white">
          {live.fresh && live.flowLpm !== null ? `${live.flowLpm.toFixed(2)} L/min` : "no data"}
        </span>
      )}
    </div>
  );
});

export const nodeTypes = { part: PartNode, meter: MeterNode };
