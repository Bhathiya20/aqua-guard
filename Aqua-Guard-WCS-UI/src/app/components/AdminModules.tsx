import { Link } from "react-router";
import { AlertTriangle, ArrowRight, CloudRain, GitBranch, Radio } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { Button } from "./ui/button";
import { Pill } from "./common";
import { usePolling } from "../lib/usePolling";
import { useActivePipeline } from "../lib/useActivePipeline";
import { v2, type ZoneStatus } from "../lib/v2";
import { api } from "../lib/api";

const ZONE_ORDER: Array<[ZoneStatus, string, "green" | "amber" | "red" | "gray" | "blue"]> = [
  ["confirmed", "confirmed leak", "red"], ["suspected", "suspected leak", "amber"], ["incomplete", "incomplete data", "blue"], ["normal", "normal", "green"], ["not_monitored", "not monitored", "gray"],
];

function Line({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between gap-2"><span>{k}</span><span className="font-semibold text-slate-800 text-right">{v}</span></div>;
}

/** Admin dashboard module cards, all from the v2 data: active pipeline + zones by status, active leaks, nodes online, rainwater. Without a pipeline: "Not configured" - never "NORMAL". */
export function AdminModules() {
  const sum = usePolling(() => v2.summary(), 5000);
  const pipe = useActivePipeline(5000);
  const rain = usePolling(() => api.getRainwaterSummary().catch(() => null), 8000);
  const s = sum.data, r = rain.data;
  const configured = !!pipe.active;
  const zones = pipe.status.data?.zones ?? [];
  const counts = ZONE_ORDER.map(([k, label, tone]) => ({ k, label, tone, n: zones.filter((z) => z.status === k).length })).filter((x) => x.n > 0);
  const leaks = (s?.leaks.confirmed ?? 0) + (s?.leaks.suspected ?? 0);
  const rainNoData = !r || r.totalMeasurements === 0;

  return (
    <div>
      <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-3">System overview</h2>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4" data-testid="admin-modules">
        <Card className="shadow-2xs">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2"><GitBranch className="h-5 w-5 text-blue-600" /><CardTitle className="text-base font-semibold">Pipeline</CardTitle></div>
              {configured ? <Pill tone="green">active</Pill> : <Pill tone="gray">Not configured</Pill>}
            </div>
            <CardDescription className="text-xs">{configured ? pipe.active!.name : "No active pipeline"}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {configured ? (
              <div className="flex flex-wrap gap-1.5" data-testid="zones-by-status">{counts.length === 0 ? <span className="text-xs text-muted-foreground">No zones analysed yet.</span> : counts.map((c) => <Pill key={c.k} tone={c.tone}>{c.n} {c.label}</Pill>)}</div>
            ) : <p className="text-xs text-muted-foreground">Draw the pipes and place your nodes to start leak monitoring.</p>}
            <Button asChild variant="outline" size="sm" className="w-full justify-between text-xs"><Link to={configured ? `/pipelines/${pipe.active!.id}?mode=live` : "/pipelines"}><span>{configured ? "Open live view" : "Create a pipeline"}</span><ArrowRight className="h-3.5 w-3.5 ml-1" /></Link></Button>
          </CardContent>
        </Card>

        <Card className={`shadow-2xs ${leaks ? "border-red-300" : ""}`}>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2"><AlertTriangle className="h-5 w-5 text-amber-500" /><CardTitle className="text-base font-semibold">Active leaks</CardTitle></div>
              {!configured ? <Pill tone="gray">Not configured</Pill> : leaks ? <Pill tone="red">{leaks}</Pill> : <Pill tone="green">none</Pill>}
            </div>
            <CardDescription className="text-xs">Zone balance per pipeline section</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="text-xs space-y-1 text-muted-foreground">
              <Line k="Confirmed" v={configured ? s?.leaks.confirmed ?? "—" : "—"} />
              <Line k="Suspected" v={configured ? s?.leaks.suspected ?? "—" : "—"} />
              <Line k="Other alerts" v={s?.otherAlerts ?? "—"} />
            </div>
            <Button asChild variant="outline" size="sm" className="w-full justify-between text-xs"><Link to="/leak-detection"><span>Open leak detection</span><ArrowRight className="h-3.5 w-3.5 ml-1" /></Link></Button>
          </CardContent>
        </Card>

        <Card className="shadow-2xs">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2"><Radio className="h-5 w-5 text-blue-600" /><CardTitle className="text-base font-semibold">Nodes</CardTitle></div>
              <span className="text-xs font-mono font-bold text-blue-600">{s ? `${s.nodes.online} / ${s.nodes.total} online` : "—"}</span>
            </div>
            <CardDescription className="text-xs">{s?.gateway ? `Gateway ${s.gateway.gatewayId} is ${s.gateway.state}` : "No gateway has reported yet"}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="text-xs space-y-1 text-muted-foreground">
              <Line k="Offline" v={s?.nodes.offline ?? "—"} />
              <Line k="Gateways online" v={s?.gateways ? `${s.gateways.online} / ${s.gateways.total}` : "—"} />
            </div>
            <Button asChild variant="outline" size="sm" className="w-full justify-between text-xs"><Link to="/nodes"><span>Manage nodes</span><ArrowRight className="h-3.5 w-3.5 ml-1" /></Link></Button>
          </CardContent>
        </Card>

        <Card className="shadow-2xs">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2"><CloudRain className="h-5 w-5 text-blue-600" /><CardTitle className="text-base font-semibold">Rainwater</CardTitle></div>
              {rainNoData ? <Pill tone="gray">No data</Pill> : <span className="text-xs font-semibold text-blue-600">{r!.isRaining ? "Raining" : "No rain"}</span>}
            </div>
            <CardDescription className="text-xs">Rain, tank, quality and valve</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {rainNoData ? <p className="text-xs text-muted-foreground">The rainwater node has not sent a reading yet.</p> : (
              <div className="text-xs space-y-1 text-muted-foreground">
                <Line k="Tank level" v={r!.tankFillPercentage !== null ? `${r!.tankFillPercentage.toFixed(1)} %` : "Unavailable"} />
                <Line k="Valve" v={`${r!.valveOpen ? "open" : "closed"}${r!.dryRun ? " (dry-run)" : ""}`} />
              </div>
            )}
            <Button asChild variant="outline" size="sm" className="w-full justify-between text-xs"><Link to="/rainwater"><span>Open rainwater</span><ArrowRight className="h-3.5 w-3.5 ml-1" /></Link></Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
