// Water Monitoring page: live flow per node, today/7-day usage and running time per node.
import { UsageLiveTable } from "./usage/UsageParts";
import { Disclosure } from "./leak/Disclosure";
import { Link } from "react-router";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Gauge } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { ConnectionBanner, EmptyState, Loading, PageHeader, Pill, Stat, deviceTone } from "./common";
import { fmtClock, usePolling } from "../lib/usePolling";
import { useActivePipeline } from "../lib/useActivePipeline";
import { liters, lpm, nodeTitle, v2, type NodeV2 } from "../lib/v2";

function NodeChart({ node }: { node: NodeV2 }) {
  const u = usePolling(() => v2.usage(node.id, "hour", 1), 30000, [node.id]);
  const rows = (u.data?.usage ?? []).map((p) => ({ h: fmtClock(p.bucket), liters: p.liters }));
  if (u.loading) return <Loading label="Loading chart…" />;
  if (rows.length === 0) return <p className="py-6 text-center text-xs text-muted-foreground">No usage recorded in the last 24 hours.</p>;
  return (
    <div className="h-36"><ResponsiveContainer width="100%" height="100%">
      <BarChart data={rows} margin={{ top: 4, right: 4, left: -22, bottom: 0 }}><CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" /><XAxis dataKey="h" tick={{ fontSize: 10 }} /><YAxis tick={{ fontSize: 10 }} /><Tooltip formatter={(v: number) => [`${v.toFixed(1)} L`, "Used"]} /><Bar dataKey="liters" fill="#2563eb" radius={[3, 3, 0, 0]} /></BarChart>
    </ResponsiveContainer></div>
  );
}

export function WaterMonitoringDashboard() {
  const sum = usePolling(() => v2.summary(), 2000);
  const nodes = usePolling(() => v2.nodes(), 2000);
  const ap = useActivePipeline(3000);
  const flow = (nodes.data?.nodes ?? []).filter((n) => n.type === "flow" && n.state !== "removed");
  const t = sum.data?.total;
  const zones = ap.status.data?.zones ?? [];

  return (
    <div className="space-y-5">
      <PageHeader icon={<Gauge className="h-6 w-6 text-blue-600" />} title="Water Monitoring" subtitle="Flow and usage per node and per zone. Refreshes every 2 seconds." />
      <ConnectionBanner error={sum.error || nodes.error} stale={sum.stale || nodes.stale} onRetry={() => { sum.refresh(); nodes.refresh(); }} />

      <div className="grid gap-3 grid-cols-2 lg:grid-cols-4">
        <Stat label="Total flow now" value={t?.flowLpm != null ? t.flowLpm.toFixed(2) : "—"} unit="L/min" hint={t?.basis === "source_meter" ? `main supply (Node ${t.node})` : t?.basis === "sum_of_nodes" ? "sum of nodes" : "no data"} />
        <Stat label="Used today" value={liters(t?.todayLiters)} />
        <Stat label="Used in 7 days" value={liters(t?.weekLiters)} />
        <Stat label="Nodes online" value={sum.data ? `${sum.data.nodes.online} / ${sum.data.nodes.total}` : "—"} />
      </div>

      <Disclosure id="running" title="Running time per node" summary="how long water has been flowing"><UsageLiveTable title="" /></Disclosure>

      <section className="space-y-2">
        <h2 className="text-lg font-semibold">Nodes</h2>
        {nodes.loading ? <Loading /> : flow.length === 0 ? <Card><CardContent><EmptyState title="No flow node yet" action={{ to: "/nodes", label: "Go to Nodes" }}>No flow node is paired.</EmptyState></CardContent></Card> : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {flow.map((n) => (
              <Card key={n.id} data-testid={`wm-node-${n.number}`}>
                <CardHeader className="pb-2">
                  <div className="flex items-center justify-between gap-2"><CardTitle className="text-base">{nodeTitle(n)}</CardTitle><Pill tone={deviceTone(n.state)}>{n.state}</Pill></div>
                  <CardDescription>Flow now</CardDescription>
                </CardHeader>
                <CardContent className="space-y-2">
                  <div className="font-mono text-3xl font-bold">{lpm(n.live?.flowLpm)}<span className="ml-1 text-sm font-normal text-muted-foreground">L/min</span></div>
                  <div className="flex justify-between text-sm"><span className="text-muted-foreground">Today</span><span className="font-mono">{liters(n.live?.todayLiters)}</span></div>
                  <div className="flex justify-between text-sm"><span className="text-muted-foreground">7 days</span><span className="font-mono">{liters(n.live?.weekLiters)}</span></div>
                  <div className="text-xs font-medium text-muted-foreground pt-1">Last 24 hours (L per hour)</div>
                  <NodeChart node={n} />
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-lg font-semibold">Zones</h2>
        {ap.loading ? <Loading /> : !ap.active ? <Card><CardContent><EmptyState title="No active pipeline" action={{ to: "/pipelines", label: "Open Pipelines" }}>Without a pipeline there are no zones to show.</EmptyState></CardContent></Card> : (
          <div className="rounded-lg border overflow-x-auto"><table className="w-full text-sm">
            <thead className="bg-muted/50 text-left text-xs text-muted-foreground"><tr><th className="p-2">Zone</th><th className="p-2 text-right">Flow in (L/min)</th><th className="p-2 text-right">Flow out (L/min)</th><th className="p-2">Status</th></tr></thead>
            <tbody>{zones.map((z) => (
              <tr key={z.id} className="border-t"><td className="p-2"><Link className="underline" to={`/pipelines/${ap.active!.id}?mode=live&zone=${encodeURIComponent(z.id)}`}>{z.name}</Link></td>
                <td className="p-2 text-right font-mono">{z.balance ? z.balance.qIn.toFixed(2) : "—"}</td><td className="p-2 text-right font-mono">{z.balance ? z.balance.qOut.toFixed(2) : "—"}</td>
                <td className="p-2"><Pill tone={z.status === "confirmed" ? "red" : z.status === "suspected" ? "amber" : z.status === "normal" ? "green" : "gray"}>{z.status.replace("_", " ")}</Pill></td></tr>))}</tbody>
          </table></div>
        )}
      </section>
    </div>
  );
}
