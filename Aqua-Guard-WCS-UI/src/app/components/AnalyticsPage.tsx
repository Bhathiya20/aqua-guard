// Analytics: usage charts, node comparison and the leak events table (links to the resolved history).
import { useState } from "react";
import { Link } from "react-router";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { BarChart3, Download } from "lucide-react";
import { Button } from "./ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { ConnectionBanner, EmptyState, Loading, PageHeader, Pill } from "./common";
import { fmtClock, fmtDayShort, fmtTime, usePolling } from "../lib/usePolling";
import { useActivePipeline } from "../lib/useActivePipeline";
import { nodeTitle, v2, type ZoneLeak } from "../lib/v2";

type Bucket = "hour" | "day" | "week" | "month";
const RANGES: Record<Bucket, { label: string; days: number; title: string }> = {
  hour: { label: "Hourly", days: 2, title: "last 48 hours" }, day: { label: "Daily", days: 30, title: "last 30 days" },
  week: { label: "Weekly", days: 112, title: "last 16 weeks" }, month: { label: "Monthly", days: 365, title: "last 12 months" },
};

const pad = (n: number) => String(n).padStart(2, "0");
const ymd = (d: Date) => `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
/** Python/SQLite %W: week of the year, Monday first, days before the first Monday are week 00. */
const weekKey = (d: Date) => { const y0 = Date.UTC(d.getUTCFullYear(), 0, 1), yday = Math.floor((d.getTime() - y0) / 864e5), wday = (d.getUTCDay() + 6) % 7; return `${d.getUTCFullYear()}-W${pad(Math.floor((yday + 7 - wday) / 7))}`; };
/** All bucket keys of the range, oldest first, exactly as the backend writes them (UTC), so empty periods can be drawn as 0. */
export function bucketKeys(bucket: Bucket, now: Date, days: number): string[] {
  const out: string[] = [];
  const push = (k: string) => { if (out[out.length - 1] !== k) out.push(k); };
  if (bucket === "hour") { const t = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), now.getUTCHours()); for (let h = days * 24 - 1; h >= 0; h--) { const d = new Date(t - h * 3600e3); push(`${ymd(d)} ${pad(d.getUTCHours())}:00:00`); } }
  else if (bucket === "day") { for (let i = days - 1; i >= 0; i--) push(ymd(new Date(now.getTime() - i * 864e5))); }
  else if (bucket === "week") { for (let i = Math.ceil(days / 7) - 1; i >= 0; i--) push(weekKey(new Date(now.getTime() - i * 7 * 864e5))); }
  else { for (let i = 11; i >= 0; i--) { const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - i, 1)); push(`${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}`); } }
  return out;
}
/** Short local label: "11:00", "Mon 15", "W46", "Nov". */
export function bucketLabel(bucket: Bucket, key: string): string {
  if (bucket === "hour") return fmtClock(key);
  if (bucket === "day") return fmtDayShort(`${key}T00:00:00Z`);
  if (bucket === "week") return `W${key.split("-W")[1]}`;
  return new Date(`${key}-01T00:00:00Z`).toLocaleDateString("en-GB", { timeZone: "UTC", month: "short" });
}
export function fillBuckets<T extends { bucket: string }>(bucket: Bucket, rows: T[], make: (key: string) => T, now = new Date(), days = RANGES[bucket].days): T[] {
  const by = new Map(rows.map((r) => [r.bucket, r]));
  return bucketKeys(bucket, now, days).map((k) => by.get(k) ?? make(k));
}

export function toCsv(rows: Array<Record<string, string | number | null | undefined>>): string {
  if (rows.length === 0) return "";
  const cols = Object.keys(rows[0]);
  const esc = (v: unknown) => { const s = v === null || v === undefined ? "" : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  return [cols.join(","), ...rows.map((r) => cols.map((c) => esc(r[c])).join(","))].join("\r\n") + "\r\n";
}
function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a"); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function AnalyticsPage() {
  const [bucket, setBucket] = useState<Bucket>("day");
  const [target, setTarget] = useState<string>("");              // "n:<ref>" or "z:<zoneId>"
  const nodes = usePolling(() => v2.nodes(), 15000);
  const ap = useActivePipeline(10000);
  const flow = (nodes.data?.nodes ?? []).filter((n) => n.type === "flow" && n.state !== "removed");
  const zones = ap.status.data?.zones.filter((z) => z.class !== "not_monitored") ?? [];

  const sel = target || (flow[0] ? `n:${flow[0].id}` : zones[0] ? `z:${zones[0].id}` : "");
  const isNode = sel.startsWith("n:");
  const key = sel.slice(2);
  const range = RANGES[bucket];
  const series = usePolling(async () => {
    if (!sel) return null;
    if (isNode) { const r = await v2.usage(Number(key), bucket, range.days); return { kind: "node" as const, rows: r.usage.map((u) => ({ bucket: u.bucket, liters: u.liters, low: u.lowConfidence })) }; }
    if (!ap.active) return null;
    const r = await v2.zoneUsage(ap.active.id, key, bucket, range.days);
    return { kind: "zone" as const, rows: r.usage.map((u) => ({ bucket: u.bucket, inL: u.inLiters, outL: u.outLiters, unL: u.unaccountedLiters, complete: u.completeBuckets, total: u.buckets })) };
  }, 20000, [sel, bucket, ap.active?.id]);
  const leaks = usePolling(() => (ap.active ? v2.zoneLeaks(ap.active.id) : Promise.resolve({ leaks: [] as ZoneLeak[] })), 20000, [ap.active?.id]);

  // alerts store the zone id ("zone:flow3"): show the zone's name; a zone that no longer exists is spelled out from its id
  const zoneLabel = (id: string) => ap.status.data?.zones.find((z) => z.id === id)?.name ?? id.replace(/^zone:flow(\d+)$/, "After Node $1").replace(/^zone:main$/, "Main supply").replace(/^zone:/, "");
  const label = isNode ? (() => { const n = flow.find((x) => String(x.id) === key); return n ? nodeTitle(n) : "node"; })() : zones.find((z) => z.id === key)?.name ?? key;
  const raw = series.data;
  const data = !raw ? raw : raw.kind === "node"
    ? { kind: "node" as const, rows: fillBuckets(bucket, raw.rows, (k) => ({ bucket: k, liters: 0, low: false })) }
    : { kind: "zone" as const, rows: fillBuckets(bucket, raw.rows, (k) => ({ bucket: k, inL: 0, outL: 0, unL: 0, complete: 0, total: 0 })) };
  const sumL = data ? (data.kind === "node" ? data.rows.reduce((a, r) => a + r.liters, 0) : data.rows.reduce((a, r) => a + r.inL, 0)) : 0;
  const empty = !sel;
  const tick = (k: string) => bucketLabel(bucket, k);
  const tipLabel = (k: string) => (bucket === "hour" ? fmtTime(k) : tick(k));

  const exportUsage = () => {
    if (!data) return;
    const rows = data.kind === "node" ? data.rows.map((r) => ({ period: r.bucket, liters: r.liters, low_confidence_time: r.low ? "yes" : "no" }))
      : data.rows.map((r) => ({ period: r.bucket, water_in_l: r.inL, water_out_l: r.outL, unaccounted_l: r.unL, complete_periods: r.complete, periods: r.total }));
    download(`usage-${isNode ? "node" : "zone"}-${key.replace(/[^\w-]+/g, "_")}-${bucket}.csv`, toCsv(rows));
  };
  const exportLeaks = () => download("leak-events.csv", toCsv((leaks.data?.leaks ?? []).map((l) => ({ id: l.id, zone: l.zone_id, pipeline_version: l.pipeline_version, state: l.active ? l.state : "resolved", started: l.created_at, cleared: l.cleared_at, message: l.message }))));

  return (
    <div className="space-y-5">
      <PageHeader icon={<BarChart3 className="h-6 w-6 text-blue-600" />} title="Analytics" subtitle="Usage per node and per zone, and the history of leak events."
        right={<div className="flex gap-2"><Button size="sm" variant="outline" disabled={!data || data.rows.length === 0} onClick={exportUsage}><Download className="mr-1.5 h-3.5 w-3.5" />Export usage CSV</Button>
          <Button size="sm" variant="outline" disabled={(leaks.data?.leaks.length ?? 0) === 0} onClick={exportLeaks}><Download className="mr-1.5 h-3.5 w-3.5" />Export leaks CSV</Button></div>} />
      <ConnectionBanner error={nodes.error || series.error} stale={nodes.stale || series.stale} onRetry={() => { nodes.refresh(); series.refresh(); }} />

      <div className="flex flex-wrap gap-2">
        <select aria-label="Node or zone" className="h-9 rounded-md border bg-input-background px-2 text-sm" value={sel} onChange={(e) => setTarget(e.target.value)}>
          {flow.length > 0 && <optgroup label="Nodes">{flow.map((n) => <option key={n.id} value={`n:${n.id}`}>{nodeTitle(n)}</option>)}</optgroup>}
          {zones.length > 0 && <optgroup label="Zones (active pipeline)">{zones.map((z) => <option key={z.id} value={`z:${z.id}`}>{z.name}</option>)}</optgroup>}
        </select>
        <div className="inline-flex rounded-md border p-0.5" role="group" aria-label="Period">
          {(Object.keys(RANGES) as Bucket[]).map((b) => <Button key={b} size="sm" className="h-8" variant={bucket === b ? "default" : "ghost"} onClick={() => setBucket(b)}>{RANGES[b].label}</Button>)}
        </div>
      </div>

      <Card>
        <CardHeader className="pb-2"><CardTitle className="text-base">{label}</CardTitle><CardDescription>{RANGES[bucket].label} - {range.title}{data ? ` · ${sumL >= 1000 ? `${(sumL / 1000).toFixed(2)} m³` : `${sumL.toFixed(1)} L`} ${data.kind === "zone" ? "water in" : "used"}` : ""}</CardDescription></CardHeader>
        <CardContent>
          {empty ? <EmptyState title="No data" action={{ to: "/nodes", label: "Go to Nodes" }}>No node or zone to show yet.</EmptyState> : series.loading ? <Loading /> : !data || data.rows.length === 0 ? <EmptyState>No data in this period.</EmptyState> : (
            <div className="h-72"><ResponsiveContainer width="100%" height="100%">
              {data.kind === "node" ? (
                <BarChart data={data.rows} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}><CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" /><XAxis dataKey="bucket" tick={{ fontSize: 10 }} tickFormatter={tick} interval="preserveStartEnd" minTickGap={16} /><YAxis tick={{ fontSize: 10 }} unit=" L" /><Tooltip labelFormatter={tipLabel} formatter={(v: number) => [`${v.toFixed(1)} L`, "Used"]} />
                  <Bar dataKey="liters" name="Used (L)" fill="#2563eb" radius={[3, 3, 0, 0]} maxBarSize={28} /></BarChart>
              ) : (
                <BarChart data={data.rows} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}><CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" /><XAxis dataKey="bucket" tick={{ fontSize: 10 }} tickFormatter={tick} interval="preserveStartEnd" minTickGap={16} /><YAxis tick={{ fontSize: 10 }} unit=" L" /><Tooltip labelFormatter={tipLabel} /><Legend />
                  <Bar dataKey="inL" name="Water in (L)" fill="#2563eb" radius={[3, 3, 0, 0]} maxBarSize={22} /><Bar dataKey="outL" name="Water out (L)" fill="#16a34a" radius={[3, 3, 0, 0]} maxBarSize={22} /><Bar dataKey="unL" name="Unaccounted (L)" fill="#dc2626" radius={[3, 3, 0, 0]} maxBarSize={22} /></BarChart>
              )}
            </ResponsiveContainer></div>
          )}
          {data && data.rows.length > 0 && <p className="mt-2 text-sm" data-testid="period-total">Total for the {range.title}: <b>{sumL >= 1000 ? `${(sumL / 1000).toFixed(2)} m³` : `${sumL.toFixed(1)} L`}</b>{data.kind === "zone" ? " water in" : ""} · {data.rows.length} {bucket === "hour" ? "hours" : bucket === "day" ? "days" : bucket === "week" ? "weeks" : "months"}, empty periods count as 0</p>}
          {data?.kind === "node" && data.rows.some((r) => r.low) && <p className="mt-2 text-xs text-amber-700">Some periods were stamped with the backend's clock because the gateway had no time (lower confidence).</p>}
        </CardContent>
      </Card>

      <section className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-lg font-semibold">Leak events</h2>
          <Link to="/alerts?tab=history" className="text-sm underline text-blue-700" data-testid="history-link">Resolved history: times, water lost, reasons, chart</Link></div>
        {!ap.active ? <Card><CardContent><EmptyState title="No active pipeline" action={{ to: "/pipelines", label: "Open Pipelines" }}>Leak history needs an active pipeline.</EmptyState></CardContent></Card> : (leaks.data?.leaks.length ?? 0) === 0 ? <Card><CardContent><EmptyState>No leak events recorded.</EmptyState></CardContent></Card> : (
          <div className="rounded-lg border overflow-x-auto"><table className="w-full text-sm" data-testid="leak-events">
            <thead className="bg-muted/50 text-left text-xs text-muted-foreground"><tr><th className="p-2">Started</th><th className="p-2">Zone</th><th className="p-2">State</th><th className="p-2">Version</th><th className="p-2">Cleared</th></tr></thead>
            <tbody>{leaks.data!.leaks.slice(0, 100).map((l) => (
              <tr key={l.id} className="border-t"><td className="p-2 whitespace-nowrap">{fmtTime(l.created_at)}</td><td className="p-2">{zoneLabel(l.zone_id)}</td>
                <td className="p-2"><Pill tone={l.active ? (l.state === "confirmed" ? "red" : "amber") : "green"}>{l.active ? l.state : "resolved"}</Pill></td><td className="p-2">v{l.pipeline_version}</td><td className="p-2 whitespace-nowrap">{l.cleared_at ? fmtTime(l.cleared_at) : "—"}</td></tr>))}</tbody>
          </table></div>
        )}
      </section>
    </div>
  );
}
