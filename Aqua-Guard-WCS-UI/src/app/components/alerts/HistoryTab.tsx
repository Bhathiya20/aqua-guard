// History tab: resolved events with filters, CSV export and a detail view with the flow / D chart.
import { useMemo, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Download } from "lucide-react";
import { Button } from "../ui/button";
import { Card, CardContent } from "../ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "../ui/dialog";
import { EmptyState, Loading, Pill } from "../common";
import { UsageResetNote } from "../UsageReset";
import { fmtClock, fmtDuration, fmtTime, usePolling } from "../../lib/usePolling";
import { RESOLVE_REASONS, v2, type HistoryEvent } from "../../lib/v2";

const TYPES: Record<string, string> = { leak: "Leak", measurement_inconsistency: "Measurement inconsistency", usage_prolonged: "Prolonged usage", usage_continuous: "Continuous abnormal usage", night_flow: "Night flow",
  node_offline: "Node offline", low_battery: "Low battery", missing_packets: "Missing packets", gateway_offline: "Gateway offline", valve_fault: "Valve fault", tank_full: "Tank full", unsafe_turbidity: "Unsafe turbidity" };
const sevTone = (s: string) => (s === "critical" ? "red" : s === "warning" ? "amber" : "blue");
const lit = (v: number | null) => (v === null ? "—" : `${v.toFixed(v < 10 ? 2 : 1)} L`);

/** Query string of the current filters (also used by the CSV link, so the file always matches what is on screen). */
export function filterQuery(f: Record<string, string>): string {
  const p = new URLSearchParams();
  Object.entries(f).forEach(([k, v]) => { if (v) p.set(k, v); });
  return p.toString();
}

export function EventDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const d = usePolling(() => v2.alertDetail(id), 60000, [id]);
  const e = d.data?.event, s = d.data?.series;
  const data = useMemo(() => (s?.points ?? []).map((p) => ({ ...p, label: fmtClock(p.t) })), [s]);
  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-3xl" data-testid="event-detail">
        <DialogHeader><DialogTitle>{e ? `${e.typeLabel} · ${e.zone?.name ?? e.node?.label ?? ""}` : "Event"}</DialogTitle>
          <DialogDescription>{e ? e.message : "Loading the event…"}</DialogDescription></DialogHeader>
        {!e ? <Loading /> : (
          <div className="space-y-3 text-sm">
            <dl className="grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-3">
              <div><dt className="text-xs text-muted-foreground">Started</dt><dd>{fmtTime(e.startedAt)}</dd></div>
              <div><dt className="text-xs text-muted-foreground">Suspected</dt><dd>{fmtTime(e.suspectedAt)}</dd></div>
              <div><dt className="text-xs text-muted-foreground">Confirmed</dt><dd>{fmtTime(e.confirmedAt)}</dd></div>
              <div><dt className="text-xs text-muted-foreground">Resolved</dt><dd>{fmtTime(e.resolvedAt)}</dd></div>
              <div><dt className="text-xs text-muted-foreground">Duration</dt><dd>{fmtDuration(e.durationS)}</dd></div>
              <div><dt className="text-xs text-muted-foreground">Peak {e.peakKind === "D" ? "D" : "flow"}</dt><dd>{e.peakLpm === null ? "—" : `${e.peakLpm} L/min`}</dd></div>
              <div><dt className="text-xs text-muted-foreground">Water lost (estimate)</dt><dd>{lit(e.lostL)}</dd></div>
              <div><dt className="text-xs text-muted-foreground">Resolved by</dt><dd>{e.resolvedBy}</dd></div>
              <div><dt className="text-xs text-muted-foreground">Reason</dt><dd>{e.reasonLabel ?? "—"}{e.note ? ` · ${e.note}` : ""}</dd></div>
            </dl>
            {data.length === 0 ? <p className="rounded-md border p-3 text-muted-foreground" data-testid="event-nochart">No flow data was stored for this event.</p> : (
              <div className="h-64" data-testid="event-chart"><ResponsiveContainer width="100%" height="100%">
                <LineChart data={data} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}><CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" /><XAxis dataKey="label" tick={{ fontSize: 10 }} minTickGap={24} /><YAxis tick={{ fontSize: 10 }} unit=" L/min" /><Tooltip /><Legend />
                  {s?.kind === "balance" ? (<>
                    <Line type="monotone" dataKey="qIn" name="Q in" stroke="#2563eb" dot={false} /><Line type="monotone" dataKey="qOut" name="Σ Q out" stroke="#16a34a" dot={false} />
                    <Line type="monotone" dataKey="d" name="D = in − out" stroke="#dc2626" strokeWidth={2} dot={false} /></>) : <Line type="monotone" dataKey="flow" name="Flow" stroke="#2563eb" strokeWidth={2} dot={false} />}
                </LineChart></ResponsiveContainer></div>)}
            <p className="text-[11px] text-muted-foreground">Chart: {s?.unit}. Five minutes before the start and after the end are included for context.</p>
          </div>)}
      </DialogContent>
    </Dialog>
  );
}

/** "History": every resolved event with its times, peak, estimated water lost, who closed it and why; filters, CSV, a detail view with the chart. */
export function HistoryTab({ initialNode = "", initialZone = "" }: { initialNode?: string; initialZone?: string }) {
  const [f, setF] = useState<Record<string, string>>({ from: "", to: "", node: initialNode, zone: initialZone, type: "", reason: "" });
  const [open, setOpen] = useState<number | null>(null);
  const qs = filterQuery(f);
  const h = usePolling(() => v2.alertHistory({ ...f, node: f.node ? Number(f.node) : "" } as never), 10000, [qs]);
  const nodes = usePolling(() => v2.nodes(), 30000);
  const rows = h.data?.history ?? [];
  const zones = useMemo(() => Array.from(new Map(rows.filter((r) => r.zone).map((r) => [r.zone!.id, r.zone!.name])).entries()), [rows]);
  const sel = "h-9 rounded-md border bg-input-background px-2 text-sm";
  const set = (k: string) => (e: { target: { value: string } }) => setF((x) => ({ ...x, [k]: e.target.value }));
  return (
    <div className="space-y-3" data-testid="history-tab">
      <UsageResetNote full />
      <div className="flex flex-wrap items-end gap-2" data-testid="history-filters">
        <label className="text-xs">From<input type="date" className={`${sel} block`} value={f.from} onChange={set("from")} aria-label="From date" /></label>
        <label className="text-xs">To<input type="date" className={`${sel} block`} value={f.to} onChange={set("to")} aria-label="To date" /></label>
        <select aria-label="Node" className={sel} value={f.node} onChange={set("node")}><option value="">All nodes</option>{(nodes.data?.nodes ?? []).filter((n) => n.state !== "removed").map((n) => <option key={n.id} value={n.id}>{n.type === "rain" ? "Rain" : "Node"} {n.number}{n.name ? ` (${n.name})` : ""}</option>)}</select>
        <select aria-label="Zone" className={sel} value={f.zone} onChange={set("zone")}><option value="">All zones</option>{zones.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select>
        <select aria-label="Type" className={sel} value={f.type} onChange={set("type")}><option value="">All types</option>{Object.entries(TYPES).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
        <select aria-label="Reason" className={sel} value={f.reason} onChange={set("reason")}><option value="">Any reason</option><option value="auto">Closed automatically</option>{Object.entries(RESOLVE_REASONS).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
        <Button asChild variant="outline" size="sm" className="ml-auto"><a href={`/api/alerts/history.csv${qs ? `?${qs}` : ""}`} download data-testid="history-csv"><Download className="mr-1 h-3.5 w-3.5" />Export CSV</a></Button>
      </div>
      {h.loading ? <Loading /> : rows.length === 0 ? <Card><CardContent><EmptyState title="No resolved events">Nothing matches these filters. Events appear here once they are resolved.</EmptyState></CardContent></Card> : (
        <div className="overflow-x-auto rounded-lg border"><table className="w-full text-sm" data-testid="history-table">
          <thead className="bg-muted/50 text-left text-xs text-muted-foreground"><tr>
            <th className="p-2">Type</th><th className="p-2">Node / zone</th><th className="p-2">Started</th><th className="p-2">Suspected → confirmed → resolved</th><th className="p-2">Duration</th>
            <th className="p-2 text-right">Peak</th><th className="p-2 text-right">Water lost</th><th className="p-2">Resolved by</th><th className="p-2">Reason</th></tr></thead>
          <tbody>{rows.map((e: HistoryEvent) => (
            <tr key={e.id} className="cursor-pointer border-t align-top hover:bg-muted/30" data-testid={`history-row-${e.id}`} onClick={() => setOpen(e.id)}>
              <td className="p-2 whitespace-nowrap"><Pill tone={sevTone(e.severity) as never}>{e.typeLabel}</Pill>{e.demo && <span className="ml-1"><Pill tone="violet">demo</Pill></span>}</td>
              <td className="p-2">{e.zone?.name ?? e.node?.label ?? "—"}{e.zone && e.node ? <div className="text-xs text-muted-foreground">{e.node.label}</div> : null}</td>
              <td className="p-2 whitespace-nowrap text-xs">{fmtTime(e.startedAt)}</td>
              <td className="p-2 text-xs">{e.suspectedAt ? fmtClock(e.suspectedAt) : "—"} → {e.confirmedAt ? fmtClock(e.confirmedAt) : "—"} → {e.resolvedAt ? fmtClock(e.resolvedAt) : "—"}</td>
              <td className="p-2 whitespace-nowrap">{fmtDuration(e.durationS)}</td>
              <td className="p-2 text-right font-mono">{e.peakLpm === null ? "—" : `${e.peakLpm} L/min`}</td>
              <td className="p-2 text-right font-mono">{lit(e.lostL)}</td>
              <td className="p-2 text-xs">{e.resolvedBy}</td>
              <td className="p-2 text-xs">{e.reasonLabel ?? "—"}{e.note ? <div className="text-muted-foreground">{e.note}</div> : null}</td>
            </tr>))}</tbody></table></div>)}
      {open !== null && <EventDetail id={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
