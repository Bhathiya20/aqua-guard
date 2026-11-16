// Building blocks of the Leak Detection page (comparison, zone diagram, node table, pipeline selector).
import { useState } from "react";
import { toast } from "sonner";
import { Pill } from "../common";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "../ui/alert-dialog";
import { fmtAge, fmtTime } from "../../lib/usePolling";
import { v2, type LeakNodeRow, type LiveZone, type PipelineListItem, type ZoneNodeInfo } from "../../lib/v2";

export const fl = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v.toFixed(2));
const STATE_TONE = { online: "green", offline: "red", out_of_service: "gray", missing: "amber" } as const;
const STATE_TEXT = { online: "online", offline: "offline", out_of_service: "out of service", missing: "not found", pending: "pending" } as Record<string, string>;
export const NodeState = ({ s }: { s: ZoneNodeInfo["state"] }) => <Pill tone={STATE_TONE[s] ?? "gray"}>{STATE_TEXT[s] ?? s}</Pill>;

// ---------------------------------------------------------------- "Leak detection runs on: [pipeline v]"
/** Admin: switching activates the chosen pipeline after a confirmation (the old one is switched off, its versions are kept). Users: read-only. */
export function PipelineSelector({ pipelines, admin, onChanged }: { pipelines: PipelineListItem[]; admin: boolean; onChanged: () => void }) {
  const active = pipelines.find((p) => p.activeVersion) ?? null;
  const [target, setTarget] = useState<PipelineListItem | null>(null);
  const [busy, setBusy] = useState(false);
  const label = (p: PipelineListItem) => `${p.name}${p.preset === "calibration_rig" ? " (calibration rig - not for leak detection)" : ""}`;
  const go = async () => {
    if (!target) return;
    setBusy(true);
    try {
      const vs = await v2.versions(target.id);
      const ok = vs.versions.find((x) => x.valid);                       // newest valid version (newest first)
      if (!ok) { toast.error("This pipeline has no valid version to activate. Open it in the Pipelines page and fix the errors first."); return; }
      await v2.activate(target.id, ok.version);
      toast.success(`Leak detection now runs on “${target.name}”${active ? `; “${active.name}” was switched off` : ""}`);
      setTarget(null);
      onChanged();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Could not switch the pipeline"); } finally { setBusy(false); }
  };
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-card p-3 text-sm" data-testid="pipeline-selector">
      <label htmlFor="leak-pipeline" className="font-medium">Leak detection runs on:</label>
      <select id="leak-pipeline" className="min-w-48 rounded-md border bg-background px-2 py-1.5 disabled:opacity-70" value={active?.id ?? ""} disabled={!admin || busy || pipelines.length === 0}
        onChange={(e) => { const p = pipelines.find((x) => x.id === Number(e.target.value)); if (p && p.id !== active?.id) setTarget(p); }}>
        {!active && <option value="">(no pipeline active)</option>}
        {pipelines.map((p) => <option key={p.id} value={p.id} disabled={p.preset === "calibration_rig"}>{label(p)}</option>)}
      </select>
      {active && <Pill tone="green">● ACTIVE v{active.activeVersion}</Pill>}
      {!admin && <span className="text-xs text-muted-foreground">Only an administrator can switch the pipeline.</span>}
      <AlertDialog open={!!target} onOpenChange={(o) => { if (!o) setTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>Run leak detection on “{target?.name}”?</AlertDialogTitle>
            <AlertDialogDescription>{active ? <>This switches off “{active.name}” (its design is kept and can be activated again). </> : null}Only one pipeline analyses the real nodes at a time. Open leak events of the old pipeline are closed.</AlertDialogDescription></AlertDialogHeader>
          <AlertDialogFooter><AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel><AlertDialogAction disabled={busy} onClick={(e) => { e.preventDefault(); void go(); }}>Switch</AlertDialogAction></AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

// ---------------------------------------------------------------- small diagram of one zone
export function ZoneDiagram({ z }: { z: LiveZone }) {
  const ins = z.inflow ?? [], outs = z.outflow ?? [];
  const rows = Math.max(outs.length, 1);
  const H = 24 + rows * 46, W = 360, cy = H / 2;
  const box = (x: number, y: number, n: ZoneNodeInfo) => (
    <g key={n.partId}>
      <rect x={x} y={y} width="96" height="36" rx="6" className={n.state === "online" ? "fill-blue-50 stroke-blue-600" : "fill-muted stroke-muted-foreground"} strokeWidth="1.5" />
      <text x={x + 48} y={y + 15} textAnchor="middle" className="fill-current text-[11px] font-semibold">{n.label}</text>
      <text x={x + 48} y={y + 29} textAnchor="middle" className="fill-current font-mono text-[10px]">{n.flowLpm === null ? "no data" : `${n.flowLpm.toFixed(2)} L/min`}</text>
    </g>);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" data-testid={`zone-diagram-${z.id}`} className="h-auto w-full max-w-sm rounded-md border bg-card"
      aria-label={`Zone ${z.name}: ${ins.map((n) => n.label).join(", ") || "no inflow node"} in, ${outs.map((n) => n.label).join(", ") || "no measured outflow"} out`}>
      <defs><marker id={`a-${z.id}`} viewBox="0 0 8 8" refX="6" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="currentColor" /></marker></defs>
      {ins[0] ? box(8, cy - 18, ins[0]) : <text x="8" y={cy + 4} className="fill-muted-foreground text-[11px]">no inflow node</text>}
      <g className="text-sky-700" stroke="currentColor" fill="currentColor"><line x1="106" y1={cy} x2="130" y2={cy} strokeWidth="2" markerEnd={`url(#a-${z.id})`} /></g>
      <rect x="134" y="8" width="92" height={H - 16} rx="10" className={z.status === "confirmed" ? "fill-red-100 stroke-red-500" : z.status === "suspected" ? "fill-amber-100 stroke-amber-500" : "fill-emerald-50 stroke-emerald-500"} strokeWidth="1.5" strokeDasharray="4 3" />
      <text x="180" y={cy - 2} textAnchor="middle" className="fill-current text-[10px] font-semibold">{z.name.length > 16 ? `${z.name.slice(0, 15)}…` : z.name}</text>
      {(z.outletCount ?? 0) > 0 && <text x="180" y={cy + 11} textAnchor="middle" className="fill-muted-foreground text-[9px]">{z.outletCount} outlet{z.outletCount === 1 ? "" : "s"} (unmeasured)</text>}
      {outs.length === 0 ? <text x="238" y={cy + 4} className="fill-muted-foreground text-[10px]">no measured outflow</text> : outs.map((n, i) => {
        const y = 12 + i * 46;
        return (<g key={n.partId}><g className="text-sky-700" stroke="currentColor" fill="currentColor"><line x1="228" y1={cy} x2="250" y2={y + 18} strokeWidth="1.5" /><line x1="250" y1={y + 18} x2="256" y2={y + 18} strokeWidth="1.5" markerEnd={`url(#a-${z.id})`} /></g>{box(258, y, n)}</g>);
      })}
    </svg>
  );
}

// ---------------------------------------------------------------- the explicit comparison of one zone
export function Comparison({ z }: { z: LiveZone }) {
  const ins = z.inflow ?? [], outs = z.outflow ?? [];
  const qin = ins.length && ins.every((n) => n.flowLpm !== null) ? ins.reduce((a, n) => a + (n.flowLpm ?? 0), 0) : null;
  const qout = outs.every((n) => n.flowLpm !== null) ? outs.reduce((a, n) => a + (n.flowLpm ?? 0), 0) : null;
  const live = qin !== null && qout !== null ? qin - qout : null;
  const s = z.settings, b = z.balance;
  const list = (ns: ZoneNodeInfo[], empty: string) => ns.length === 0 ? <li className="text-xs text-muted-foreground">{empty}</li> : ns.map((n) => (
    <li key={n.partId} className="flex items-center justify-between gap-2" data-testid={`cmp-${z.id}-${n.number}`}>
      <span className="truncate">{n.label}{n.name && n.name !== n.label ? ` · ${n.name}` : ""}</span>
      <span className="flex items-center gap-1.5"><NodeState s={n.state} /><span className="font-mono font-semibold">{fl(n.flowLpm)}</span><span className="text-[11px] text-muted-foreground">L/min</span></span>
    </li>));
  return (
    <div className="space-y-2" data-testid={`comparison-${z.id}`}>
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="rounded-md border p-2"><div className="mb-1 text-[11px] font-medium uppercase text-muted-foreground">Inflow node (Q in)</div><ul className="space-y-1 text-sm">{list(ins, "No node measures the water entering this zone.")}</ul></div>
        <div className="rounded-md border p-2"><div className="mb-1 text-[11px] font-medium uppercase text-muted-foreground">Outflow node(s) (Σ Q out)</div>
          <ul className="space-y-1 text-sm">{list(outs, (z.outletCount ?? 0) > 0 ? "None: the water leaves through unmeasured outlets." : "None measured.")}</ul></div>
      </div>
      <div className="rounded-md bg-muted/50 p-2 text-sm" data-testid={`formula-${z.id}`}>
        <div className="font-mono text-xs">Q_in − Σ Q_out = unaccounted</div>
        <div className="font-mono" data-testid={`formula-live-${z.id}`}>{fl(qin)} − {fl(qout)} = <b className={live !== null && s && live > Math.max(s.thresholdLpm, (qin ?? 0) * s.tolerancePct / 100) ? "text-red-600" : ""}>{fl(live)}</b> L/min <span className="font-sans text-[11px] text-muted-foreground">right now (live readings)</span></div>
        {b && <div className="font-mono text-xs text-muted-foreground" data-testid={`formula-bucket-${z.id}`}>{fl(b.qIn)} − {fl(b.qOut)} = {fl(b.unaccounted)} L/min <span className="font-sans">last complete {s ? `${s.bucketS} s` : ""} period (what the rules use)</span></div>}
        {s && <div className="mt-1 text-xs" data-testid={`tolerance-${z.id}`}>Allowed tolerance: the larger of <b>{s.thresholdLpm} L/min</b> and <b>{s.tolerancePct} %</b> of Q in{b ? <> = <b>{fl(b.thresholdLpm)} L/min</b> now</> : null}.</div>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- node table
export function NodeTable({ rows }: { rows: LeakNodeRow[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border" data-testid="leak-node-table">
      <table className="w-full text-sm">
        <thead className="bg-muted/50 text-left text-xs text-muted-foreground"><tr><th className="p-2">Node</th><th className="p-2 text-right">Live flow</th><th className="p-2">State</th><th className="p-2">Last seen</th><th className="p-2">Feeds zone(s)</th></tr></thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={5} className="p-3 text-muted-foreground">No node is placed on this pipeline yet.</td></tr>}
          {rows.map((n) => (
            <tr key={n.partId} className="border-t align-top" data-testid={`leak-node-${n.number}`}>
              <td className="p-2 whitespace-nowrap"><b>{n.label}</b>{n.name && n.name !== n.label ? <span className="text-muted-foreground"> · {n.name}</span> : null}</td>
              <td className="p-2 text-right font-mono">{n.flowLpm === null ? "—" : `${n.flowLpm.toFixed(2)} L/min`}</td>
              <td className="p-2"><NodeState s={n.state} /></td>
              <td className="p-2 whitespace-nowrap" title={fmtTime(n.lastSeen)}>{n.lastSeen ? fmtTime(n.lastSeen) : "never"}{n.ageS !== null && n.state !== "online" ? <span className="text-xs text-muted-foreground"> · last reading {fmtAge(n.ageS)}</span> : null}</td>
              <td className="p-2"><ul className="flex flex-wrap gap-1">{n.zones.map((z) => <li key={`${z.id}-${z.role}`}><Pill tone={z.role === "in" ? "blue" : "violet"}>{z.role === "in" ? "in" : "out"} · {z.name}</Pill></li>)}</ul></td>
            </tr>))}
        </tbody>
      </table>
    </div>
  );
}
