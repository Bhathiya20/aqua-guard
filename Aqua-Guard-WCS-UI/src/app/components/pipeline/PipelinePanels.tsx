import { useState } from "react";
import { RuleProgress, WeakWhy, settingsSentence } from "../LeakInfo";
import { AlertTriangle, ArrowLeftRight, ChevronDown, ChevronRight, CircleAlert, Info, Lightbulb, RotateCw, Trash2 } from "lucide-react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Label } from "../ui/label";
import { Loading, Pill } from "../common";
import { usePolling, fmtTime, fmtClock } from "../../lib/usePolling";
import { batteryText, leakCheck, v2, type AvailableNode, type Coverage, type DocPart, type LiveZone, type NodeV2, type PipelineDoc, type Suggestion, type ValidationMessage, type ZoneInfo } from "../../lib/v2";
import { KIND_NAMES, OUTLET_TYPES, ZONE_COLORS, messageTarget, nodeLabel, unplacedNodes, zoneColor, zoneTint, type Sev } from "./docModel";

// ------------------------------------------------------------------ palette
export type PaletteItem = { kind: Exclude<DocPart["kind"], "node">; outletType?: "tap" | "shower" | "garden" | "tank"; label: string } | { node: AvailableNode; label: string };
export const PART_ITEMS: PaletteItem[] = [
  { kind: "source", label: "Main supply" }, { kind: "pipe", label: "Pipe" }, { kind: "tee", label: "T junction" }, { kind: "elbow", label: "L elbow" }, { kind: "cross", label: "Cross junction" },
  { kind: "valve", label: "Valve" }, { kind: "outlet", outletType: "tap", label: "Outlet - tap" }, { kind: "outlet", outletType: "shower", label: "Outlet - shower" },
  { kind: "outlet", outletType: "garden", label: "Outlet - garden" }, { kind: "outlet", outletType: "tank", label: "Outlet - tank inlet" }, { kind: "cap", label: "End cap" },
];
export const itemKey = (it: PaletteItem) => ("node" in it ? `node-${it.node.number}` : `${it.kind}${it.outletType ? "-" + it.outletType : ""}`);

/** Small drawing of a part (same shapes as on the canvas) so the palette shows what you are about to place. */
export function PartIcon({ item }: { item: PaletteItem }) {
  const c = "#475569";
  let body: React.ReactNode;
  if ("node" in item) body = <><rect x="3" y="9" width="30" height="6" rx="2" fill="#e2e8f0" stroke={c} /><circle cx="18" cy="12" r="7" fill="#fff" stroke="#2563eb" strokeWidth="2" /><path d="M14 12 h8 M19 9 l3 3 l-3 3" stroke="#2563eb" strokeWidth="1.6" fill="none" /></>;
  else switch (item.kind) {
    case "source": body = <><rect x="2" y="4" width="18" height="16" rx="4" fill="#dbeafe" stroke={c} /><path d="M20 12 H34" stroke={c} strokeWidth="5" /></>; break;
    case "pipe": body = <rect x="2" y="9" width="32" height="6" rx="2" fill="#e2e8f0" stroke={c} />; break;
    case "tee": body = <path d="M2 8 H34 M18 8 V22" stroke={c} strokeWidth="5" fill="none" />; break;
    case "elbow": body = <path d="M2 8 H18 V22" stroke={c} strokeWidth="5" fill="none" />; break;
    case "cross": body = <path d="M2 12 H34 M18 2 V22" stroke={c} strokeWidth="5" fill="none" />; break;
    case "valve": body = <><path d="M2 12 H8 M28 12 H34" stroke={c} strokeWidth="4" /><path d="M8 5 L18 12 L8 19 Z M28 5 L18 12 L28 19 Z" fill="#fde68a" stroke={c} /></>; break;
    case "outlet": body = item.outletType === "shower" ? <><path d="M2 6 H14 V10" stroke={c} strokeWidth="4" fill="none" /><path d="M10 12 H26 L22 14 H14 Z" fill="#cffafe" stroke={c} /><path d="M13 17 v4 M18 17 v4 M23 17 v4" stroke="#0ea5e9" strokeWidth="1.5" /></>
      : item.outletType === "garden" ? <><path d="M2 12 H16" stroke={c} strokeWidth="4" /><path d="M20 20 c-4-5 -4-8 0-12 c4 4 4 7 0 12z" fill="#bbf7d0" stroke="#16a34a" /><path d="M26 18 c-2-3 -2-5 0-7 c2 2 2 4 0 7z" fill="#bbf7d0" stroke="#16a34a" /></>
      : item.outletType === "tank" ? <><path d="M2 12 H10" stroke={c} strokeWidth="4" /><rect x="10" y="3" width="22" height="18" rx="3" fill="#cffafe" stroke={c} /><path d="M10 12 h22" stroke="#0ea5e9" /></>
      : <><path d="M2 12 H16" stroke={c} strokeWidth="4" /><path d="M16 8 h10 v4 h-4 v6" stroke={c} strokeWidth="3" fill="none" /><path d="M24 20 v2" stroke="#0ea5e9" strokeWidth="2" /></>; break;
    case "cap": body = <><path d="M2 12 H24" stroke={c} strokeWidth="5" /><rect x="24" y="5" width="6" height="14" rx="1" fill="#cbd5e1" stroke={c} /></>; break;
    default: body = <rect x="4" y="4" width="28" height="16" fill="#e2e8f0" stroke={c} />;
  }
  return <svg viewBox="0 0 36 24" width="36" height="24" aria-hidden className="shrink-0">{body}</svg>;
}

export function Palette({ doc, available, pending, disabled, onPick }: { doc: PipelineDoc; available: AvailableNode[]; pending: PaletteItem | null; disabled: boolean; onPick: (it: PaletteItem) => void }) {
  const [q, setQ] = useState("");
  const nodes = unplacedNodes(available, doc);
  const needle = q.trim().toLowerCase();
  const shown = nodes.filter((n) => !needle || `${nodeLabel(n)} ${n.gatewayId ?? ""}`.toLowerCase().includes(needle));
  const gateways = Array.from(new Set(shown.map((n) => n.gatewayId ?? "")));
  const btn = (it: PaletteItem) => {
    const active = pending && itemKey(pending) === itemKey(it);
    return (
      <button key={itemKey(it)} type="button" disabled={disabled} draggable={!disabled} data-testid={`palette-${itemKey(it)}`} aria-pressed={!!active}
        onClick={() => onPick(it)}
        onDragStart={(e) => { e.dataTransfer.setData("application/x-aqua", JSON.stringify(it)); e.dataTransfer.effectAllowed = "copy"; }}
        className={`flex w-full items-center gap-2 rounded-md border px-2 py-1.5 text-left text-sm transition-colors disabled:opacity-50 ${active ? "border-blue-500 bg-blue-50" : "bg-card hover:bg-muted"}`}>
        <PartIcon item={it} /><span className="min-w-0 truncate">{it.label}</span>
      </button>
    );
  };
  return (
    <div className="space-y-4" data-testid="palette">
      <div>
        <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Parts</h3>
        <div className="grid gap-1.5">{PART_ITEMS.map(btn)}</div>
      </div>
      <div>
        <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Paired nodes (not placed yet)</h3>
        {nodes.length > 4 && <Input aria-label="Search nodes" placeholder="Search nodes" className="mb-1.5 h-8" value={q} onChange={(e) => setQ(e.target.value)} />}
        {nodes.length === 0 ? <p className="text-xs text-muted-foreground" data-testid="palette-no-nodes">{available.length === 0 ? "No paired flow node available. Pair one on the Nodes page." : "All paired nodes are placed."}</p>
          : shown.length === 0 ? <p className="text-xs text-muted-foreground">No node matches “{q}”.</p>
          : gateways.map((g) => (
            <div key={g} className="mb-2 space-y-1.5" data-testid={g ? `palette-gw-${g}` : undefined}>
              {gateways.length > 1 && <div className="text-[11px] font-medium text-muted-foreground">Gateway {g || "?"}</div>}
              <div className="grid gap-1.5">{shown.filter((n) => (n.gatewayId ?? "") === g).map((n) => btn({ node: n, label: nodeLabel(n) }))}</div>
            </div>))}
        <p className="mt-1.5 text-[11px] text-muted-foreground">Drop a node onto a pipe: it becomes the sensor on that pipe.</p>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ properties
export function Properties({ part, doc, info, disabled, onPatch, onRotate, onDelete }: {
  part: DocPart | null; doc: PipelineDoc; info?: NodeV2; disabled: boolean;
  onPatch: (patch: Partial<DocPart>) => void; onRotate: () => void; onDelete: () => void;
}) {
  if (!part) return <p className="text-sm text-muted-foreground">Select a part on the canvas to edit it.</p>;
  const isNode = part.kind === "node";
  const pipe = isNode && part.pipe ? doc.parts.find((p) => p.id === part.pipe) : undefined;
  return (
    <div className="space-y-3" data-testid="properties">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-semibold">{isNode ? `Node ${part.number}` : KIND_NAMES[part.kind]} <span className="font-mono text-xs text-muted-foreground">{part.id}</span></h3>
      </div>
      {!isNode && (
        <div className="grid gap-1.5"><Label htmlFor="pp-label">Label</Label><Input id="pp-label" value={part.label ?? ""} maxLength={40} disabled={disabled} placeholder={KIND_NAMES[part.kind]} onChange={(e) => onPatch({ label: e.target.value })} /></div>
      )}
      {part.kind === "pipe" && (
        <div className="grid grid-cols-2 gap-2">
          <div className="grid gap-1.5"><Label htmlFor="pp-len">Length (m)</Label><Input id="pp-len" type="number" min={0} step={0.1} disabled={disabled} value={part.lengthM ?? ""} onChange={(e) => onPatch({ lengthM: e.target.value === "" ? undefined : Math.max(0, Number(e.target.value)) })} /></div>
          <div className="grid gap-1.5"><Label htmlFor="pp-dia">Diameter (mm)</Label><Input id="pp-dia" type="number" min={0} step={1} disabled={disabled} value={part.diameterMm ?? ""} onChange={(e) => onPatch({ diameterMm: e.target.value === "" ? undefined : Math.max(0, Number(e.target.value)) })} /></div>
        </div>
      )}
      {part.kind === "outlet" && (
        <div className="grid gap-1.5"><Label htmlFor="pp-out">Outlet type</Label>
          <select id="pp-out" className="h-9 rounded-md border bg-input-background px-2 text-sm" disabled={disabled} value={part.outletType ?? "tap"} onChange={(e) => onPatch({ outletType: e.target.value as DocPart["outletType"] })}>
            {OUTLET_TYPES.map((t) => <option key={t} value={t}>{t === "tank" ? "tank inlet" : t}</option>)}
          </select></div>
      )}
      {isNode && (
        <div className="space-y-2 text-sm">
          <div className="grid gap-1.5"><Label htmlFor="pp-name">Name</Label><Input id="pp-name" value={info?.name ?? part.name ?? ""} readOnly disabled /></div>
          <div className="flex justify-between"><span className="text-muted-foreground">Flow now</span><span className="font-mono">{info?.live?.flowLpm != null ? `${info.live.flowLpm.toFixed(2)} L/min` : "—"}</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">Battery</span><span className="font-mono">{info ? batteryText(info.batteryMv) : "—"}</span></div>
          <div className="flex justify-between"><span className="text-muted-foreground">On pipe</span><span className="font-mono">{pipe ? `${pipe.label || pipe.id} at ${Math.round((part.pos ?? 0.5) * 100)} %` : <span className="text-amber-700">not on a pipe</span>}</span></div>
          <div className="flex justify-between items-center"><span className="text-muted-foreground">Flow direction</span>
            <Button size="sm" variant="outline" disabled={disabled} onClick={() => onPatch({ dir: part.dir === "reverse" ? "forward" : "reverse" })}><ArrowLeftRight className="mr-1 h-3.5 w-3.5" />{part.dir === "reverse" ? "Against the flow" : "With the flow"}</Button></div>
        </div>
      )}
      {!disabled && (
        <div className="flex gap-2 pt-1">
          {!isNode && <Button size="sm" variant="outline" onClick={onRotate}><RotateCw className="mr-1 h-3.5 w-3.5" />Rotate 90°</Button>}
          <Button size="sm" variant="outline" className="text-red-700" onClick={onDelete}><Trash2 className="mr-1 h-3.5 w-3.5" />Delete</Button>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ validation bar
const SEV_ICON: Record<string, typeof Info> = { error: CircleAlert, warning: AlertTriangle, info: Info };
const SEV_TONE: Record<string, string> = { error: "text-red-700", warning: "text-amber-700", info: "text-blue-700" };

export function ValidationBar({ doc, messages, valid, partial, coverage, suggestions, checking, error, onPick, freeNodes = [], onPlace }: {
  doc: PipelineDoc; messages: ValidationMessage[]; valid: boolean | null; partial?: boolean; coverage?: Coverage; suggestions?: Suggestion[]; checking: boolean; error: string; onPick: (partId: string) => void;
  freeNodes?: AvailableNode[]; onPlace?: (node: AvailableNode, pipeId: string) => void;
}) {
  const [open, setOpen] = useState(true);
  const [sure, setSure] = useState<string | null>(null);                  // part id waiting for "place anyway?"
  const errs = messages.filter((m) => m.severity === "error").length, warns = messages.filter((m) => m.severity === "warning").length;
  const count = messages.length + (suggestions?.length ?? 0);
  const unplaced = freeNodes.filter((n) => n.type === "flow" && !doc.parts.some((p) => p.kind === "node" && p.number === n.number))
    .sort((a, b) => Number(!!a.outOfService) - Number(!!b.outOfService));       // a node that is in service is offered first
  return (
    <div className="flex max-h-[40vh] min-h-0 flex-col rounded-lg border bg-card" data-testid="validation-bar">
      <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2 text-sm">
        <button type="button" className="inline-flex items-center gap-1 font-semibold" aria-expanded={open} aria-controls="checks-list" data-testid="checks-toggle" onClick={() => setOpen((o) => !o)}>
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}Checks <span className="rounded-full bg-muted px-1.5 text-xs font-normal" data-testid="checks-count">{count}</span></button>
        {valid === null ? <Pill tone="gray">{checking ? "checking…" : "—"}</Pill> : valid ? <Pill tone="green">valid</Pill> : <Pill tone="red">{errs} error{errs === 1 ? "" : "s"}</Pill>}
        {warns > 0 && <Pill tone="amber">{warns} warning{warns === 1 ? "" : "s"}</Pill>}
        {coverage && <span className="text-xs text-muted-foreground" data-testid="coverage">{coverage.text}{partial ? " (preview - fix the errors first)" : ""}</span>}
        {error && <span className="text-xs text-red-600">Could not check: {error}</span>}
      </div>
      {open && <ul id="checks-list" className="min-h-0 flex-1 overflow-y-auto divide-y text-sm">
        {messages.length === 0 && valid && <li className="px-3 py-2 text-muted-foreground">No problems found.</li>}
        {messages.map((m, i) => {
          const Icon = SEV_ICON[m.severity] ?? Info;
          const target = messageTarget(m, doc);
          return (
            <li key={`${m.code}-${m.partId}-${i}`}>
              <button type="button" disabled={!target} onClick={() => target && onPick(target)} data-testid={`msg-${m.code}`}
                className="flex w-full items-start gap-2 px-3 py-1.5 text-left enabled:hover:bg-muted disabled:cursor-default">
                <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${SEV_TONE[m.severity]}`} /><span>{m.message}</span>
              </button>
            </li>
          );
        })}
        {(suggestions ?? []).map((s, i) => (
          <li key={`s${i}`} className="flex flex-wrap items-start gap-2 px-3 py-1.5" data-testid="suggestion">
            <Lightbulb className="mt-0.5 h-4 w-4 shrink-0 text-emerald-700" /><span className="min-w-0 flex-1">{String(s.text ?? s.message ?? "")}</span>
            {s.partId && <Button type="button" size="sm" variant="outline" className="h-7" data-testid="suggestion-show" onClick={() => onPick(String(s.partId))}>Show</Button>}
            {s.partId && onPlace && unplaced.length > 0 && (unplaced[0].outOfService && sure === String(s.partId) ? (
              <span className="flex flex-wrap items-center gap-1.5 rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-xs" data-testid="suggestion-confirm">
                Node {unplaced[0].number} is out of service and sends no data. Place it anyway?
                <Button type="button" size="sm" className="h-6" data-testid="suggestion-confirm-yes" onClick={() => { setSure(null); onPlace(unplaced[0], String(s.partId)); }}>Place anyway</Button>
                <Button type="button" size="sm" variant="outline" className="h-6" onClick={() => setSure(null)}>Cancel</Button>
              </span>
            ) : (
              <Button type="button" size="sm" className="h-7" data-testid="suggestion-place" onClick={() => (unplaced[0].outOfService ? setSure(String(s.partId)) : onPlace(unplaced[0], String(s.partId)))}>
                Place Node {unplaced[0].number} here{unplaced[0].outOfService && <span className="ml-1.5 rounded bg-slate-200 px-1 text-[10px] font-semibold text-slate-700" data-testid="suggestion-oos">out of service</span>}
              </Button>
            ))}
          </li>
        ))}
      </ul>}
    </div>
  );
}

export function severityOfBar(messages: ValidationMessage[]): Sev | null { return messages.some((m) => m.severity === "error") ? "error" : messages.length ? "warning" : null; }

// ------------------------------------------------------------------ live view
export function Legend_() {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs" data-testid="zone-legend">
      {Object.entries(ZONE_COLORS).map(([k, c]) => (
        <span key={k} className="inline-flex items-center gap-1.5"><span className="inline-block h-3 w-3 rounded-sm border-2" style={{ background: c.fill, borderColor: c.stroke }} />{c.label}</span>
      ))}
    </div>
  );
}

export const STATUS_LABEL: Record<string, string> = { normal: "Normal", suspected: "Suspected leak", confirmed: "Confirmed leak", not_monitored: "Not monitored", incomplete: "Incomplete data" };
const RULE_NAMES: Record<string, string> = { balance: "Balance (in vs out)", night_flow: "Night flow", continuous_flow: "Continuous flow" };
export const ruleName = (r: string) => RULE_NAMES[r] ?? r;

export function ZoneSummary({ z }: { z: LiveZone }) {   // rule progress and the configured values come from LeakInfo
  const b = z.balance;
  const c = zoneColor(z.status);
  return (
    <div className="space-y-2 text-sm" data-testid="zone-summary">
      <div className="flex flex-wrap items-center gap-2"><span className="inline-block h-3 w-3 rounded-sm border-2" style={{ background: c.fill, borderColor: c.stroke }} /><b>{z.name}</b><Pill tone={z.status === "confirmed" ? "red" : z.status === "suspected" ? "amber" : z.status === "normal" ? "green" : "gray"}>{STATUS_LABEL[z.status] ?? z.status}</Pill><Pill tone="blue">{z.class === "not_monitored" ? "not monitored" : z.class}</Pill></div>
      {z.reason && <p className="text-muted-foreground">{z.reason}</p>}
      <div className="grid grid-cols-3 gap-2 text-center">
        <div className="rounded-md border p-2"><div className="text-[11px] text-muted-foreground">Q in</div><div className="font-mono font-semibold">{b ? b.qIn.toFixed(2) : "—"}</div></div>
        <div className="rounded-md border p-2"><div className="text-[11px] text-muted-foreground">Σ Q out</div><div className="font-mono font-semibold">{b ? b.qOut.toFixed(2) : "—"}</div></div>
        <div className="rounded-md border p-2"><div className="text-[11px] text-muted-foreground">Unaccounted</div><div className={`font-mono font-semibold ${b && b.unaccounted > b.thresholdLpm ? "text-red-600" : ""}`}>{b ? b.unaccounted.toFixed(2) : "—"}</div></div>
      </div>
      <p className="text-[11px] text-muted-foreground">L/min, last complete bucket. {z.equation}</p>
      {z.nodesMissing.length > 0 && <p className="text-xs text-amber-700">No recent data from: {z.nodesMissing.join(", ")}.</p>}
      <WeakWhy z={z} />
      <RuleProgress z={z} />
      {z.settings && z.class !== "not_monitored" && <p className="text-[11px] text-muted-foreground" data-testid="zone-settings">{settingsSentence(z)}</p>}
    </div>
  );
}

export function ZoneDetail({ pipelineId, zone }: { pipelineId: number; zone: LiveZone }) {
  const [hours, setHours] = useState(6);
  const series = usePolling(() => v2.zoneSeries(pipelineId, zone.id, hours), 10000, [zone.id, hours]);
  const leaks = usePolling(() => v2.zoneLeaks(pipelineId), 15000, [pipelineId]);
  const bucketMin = (series.data?.bucketS ?? 60) / 60;
  const rows = (series.data?.buckets ?? []).map((b) => ({
    t: fmtClock(b.ts),
    qin: b.qInL === null ? null : +(b.qInL / bucketMin).toFixed(3), qout: b.qOutL === null ? null : +(b.qOutL / bucketMin).toFixed(3), unacc: b.unaccountedL === null ? null : +(b.unaccountedL / bucketMin).toFixed(3),
  }));
  const mine = (leaks.data?.leaks ?? []).filter((l) => l.zone_id === zone.id);
  return (
    <div className="space-y-3" data-testid="zone-detail">
      <ZoneSummary z={zone} />
      <div className="flex items-center justify-between"><h4 className="text-sm font-semibold">Usage in this zone</h4>
        <select aria-label="Chart range" className="h-8 rounded-md border bg-input-background px-2 text-xs" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
          <option value={1}>1 h</option><option value={6}>6 h</option><option value={24}>24 h</option><option value={72}>3 days</option></select></div>
      {series.loading ? <Loading label="Loading chart…" /> : rows.length === 0 ? <p className="text-xs text-muted-foreground">No finished measurement period yet.</p> : (
        <div className="h-48"><ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" /><XAxis dataKey="t" tick={{ fontSize: 10 }} /><YAxis tick={{ fontSize: 10 }} unit="" /><Tooltip /><Legend wrapperStyle={{ fontSize: 11 }} />
            <Line type="monotone" dataKey="qin" name="Q in (L/min)" stroke="#2563eb" dot={false} strokeWidth={2} connectNulls />
            <Line type="monotone" dataKey="qout" name="Σ Q out" stroke="#16a34a" dot={false} strokeWidth={2} connectNulls />
            <Line type="monotone" dataKey="unacc" name="Unaccounted" stroke="#dc2626" dot={false} strokeWidth={2} connectNulls />
          </LineChart></ResponsiveContainer></div>
      )}
      <h4 className="text-sm font-semibold">Leak history</h4>
      {mine.length === 0 ? <p className="text-xs text-muted-foreground">No leak events in this zone.</p> : (
        <ul className="space-y-1 text-xs">{mine.slice(0, 10).map((l) => (
          <li key={l.id} className="rounded border p-2"><div className="flex items-center gap-2"><Pill tone={l.state === "confirmed" ? "red" : l.state === "suspected" ? "amber" : "green"}>{l.active ? l.state : "resolved"}</Pill><span className="text-muted-foreground">{fmtTime(l.created_at)}{l.cleared_at ? ` → ${fmtTime(l.cleared_at)}` : ""} · v{l.pipeline_version}</span></div><div className="mt-1">{l.message}</div></li>))}</ul>
      )}
    </div>
  );
}

export function ZoneList({ zones, selected, onSelect }: { zones: LiveZone[]; selected: string | null; onSelect: (id: string) => void }) {
  const swatch = (z: LiveZone) => ({ background: zoneTint(zones, z.id), borderColor: zoneColor(z.status).stroke });
  return (
    <ul className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-1" data-testid="zone-list">
      {zones.map((z) => {
        const c = zoneColor(z.status), b = z.balance;
        return (
          <li key={z.id}>
            <button type="button" onClick={() => onSelect(z.id)} data-testid={`zone-${z.id}`} className={`flex w-full items-center gap-2 rounded-md border px-2.5 py-2 text-left text-sm ${selected === z.id ? "border-blue-500 bg-blue-50" : "hover:bg-muted"}`}>
              <span className="inline-block h-3.5 w-3.5 shrink-0 rounded-sm border-2" style={swatch(z)} title={`${z.name}: ${c.label}`} />
              <span className="min-w-0 flex-1"><span className="block truncate font-medium">{z.name}</span><span className="block text-[11px] text-muted-foreground">{b ? `in ${b.qIn.toFixed(2)} · out ${b.qOut.toFixed(2)} · unacc ${b.unaccounted.toFixed(2)} L/min` : STATUS_LABEL[z.status] ?? z.status}</span></span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

/** Right panel of the Live view when no zone is selected: every zone at a glance. */
export function LiveSummary({ zones, tints, onSelect }: { zones: LiveZone[]; tints: Record<string, string>; onSelect: (id: string) => void }) {
  const lc = leakCheck(zones);
  const tone = lc.state === "confirmed" ? "red" : lc.state === "suspected" || lc.state === "paused" || lc.state === "watching" ? "amber" : lc.state === "ok" ? "green" : "gray";
  return (
    <div className="space-y-3 text-sm" data-testid="live-summary">
      <div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold">All zones</h3><Pill tone={tone as never}>{lc.headline}</Pill></div>
      {zones.length === 0 ? <p className="text-muted-foreground">Waiting for the first analysis…</p> : (
        <ul className="space-y-1.5">{zones.map((z) => {
          const b = z.balance, c = zoneColor(z.status), miss = [...(z.nodesOffline ?? []), ...z.nodesMissing];
          return (
            <li key={z.id}><button type="button" onClick={() => onSelect(z.id)} data-testid={`summary-${z.id}`} className="w-full rounded-md border p-2 text-left hover:bg-muted" style={{ borderLeft: `6px solid ${c.stroke}` }}>
              <div className="flex items-center gap-2"><span className="inline-block h-3 w-3 shrink-0 rounded-sm" style={{ background: tints[z.id] }} /><b className="min-w-0 flex-1 truncate">{z.name}</b><span className="text-[11px]" style={{ color: c.stroke }}>{STATUS_LABEL[z.status] ?? z.status}</span></div>
              <div className="mt-0.5 text-[11px] text-muted-foreground">{b ? `in ${b.qIn.toFixed(2)} · out ${b.qOut.toFixed(2)} · unaccounted ${b.unaccounted.toFixed(2)} L/min` : z.status === "incomplete" ? `no recent data${miss.length ? ` from ${miss.join(", ")}` : ""}` : z.reason || "—"}</div>
              {z.watching && z.rules.filter((r) => r.progress?.counting).map((r) => <div key={r.rule} className="mt-0.5 text-[11px] text-amber-800" data-testid={`watch-${z.id}`}>{r.progress!.text}</div>)}
            </button></li>);
        })}</ul>
      )}
      <p className="text-xs text-muted-foreground">Click a zone here or on the canvas for its chart and leak history. Fill colour = the zone; border and badge = its leak state.</p>
    </div>
  );
}

export function zoneInfoText(z: ZoneInfo) { return `${z.name} (${z.class === "not_monitored" ? "not monitored" : z.class})`; }
