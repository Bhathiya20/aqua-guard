// Overview: system status strip, totals and the most important alerts.
import { Link } from "react-router";
import { Activity, AlertTriangle, BatteryWarning, CloudRain, Droplets, GitBranch, Radio, Server } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { Button } from "./ui/button";
import { ConnectionBanner, EmptyState, Loading, PageHeader, Pill, Stat, deviceTone } from "./common";
import { fmtAge, usePolling } from "../lib/usePolling";
import { UsageResetControl } from "./UsageReset";
import { SetupProgress } from "./StatusStrip";
import { api } from "../lib/api";
import { valveStateName } from "./common";
import { useActivePipeline } from "../lib/useActivePipeline";
import { batteryText, leakCheck, liters, lpm, nodeTitle, v2 } from "../lib/v2";

export function OverviewPage() {
  const s = usePolling(() => v2.summary(), 2000);
  const nodes = usePolling(() => v2.nodes(), 2500);
  const alerts = usePolling(() => v2.alerts(true, 8), 5000);
  const ap = useActivePipeline(4000);
  const rain = usePolling(() => api.getRainwaterLive(), 5000);
  const rw = rain.data?.rainwater ?? null;
  const lc = leakCheck(ap.status.data?.zones);
  const d = s.data;

  if (s.loading) return <div className="space-y-5"><PageHeader icon={<Activity className="h-6 w-6 text-blue-600" />} title="Overview" /><Loading /></div>;
  const gw = d?.gateway ?? null;
  const total = d?.total;
  const leakCount = (d?.leaks.confirmed ?? 0) + (d?.leaks.suspected ?? 0);
  const flowNodes = (nodes.data?.nodes ?? []).filter((n) => n.type === "flow" && n.state !== "removed");

  return (
    <div className="space-y-5">
      <PageHeader icon={<Activity className="h-6 w-6 text-blue-600" />} title="Overview" subtitle="Live state of the whole water system." />
      <ConnectionBanner error={s.error} stale={s.stale} onRetry={s.refresh} />
      <UsageResetControl />
      <SetupProgress sum={d} pipelines={ap.list.data?.pipelines.length ?? 0} active={!!ap.active} lc={lc} />

      {!gw && <Card><CardContent><EmptyState title="No gateway" action={{ to: "/nodes", label: "Open Nodes" }}>No gateway has reported yet. Power the gateway; it appears here once it reaches the MQTT broker.</EmptyState></CardContent></Card>}

      <div className="grid gap-3 grid-cols-2 lg:grid-cols-4">
        <Stat label="Gateway" tone={gw?.state === "online" ? "text-emerald-600" : "text-red-600"} value={gw ? gw.state : "—"}
          hint={gw ? <span>{gw.timeSynced ? "time synced" : gw.timeSynced === false ? "time NOT synced" : "time unknown"} · {gw.heartbeatAgeS === null ? "no heartbeat" : fmtAge(Math.round(gw.heartbeatAgeS))}</span> : "no gateway"} />
        <Stat label="Nodes online" value={d ? `${d.nodes.online} / ${d.nodes.total}` : "—"} hint={!d ? undefined : d.nodes.offline > 0 ? <span className="text-red-600">{d.nodes.offline} offline</span> : d.nodes.total === 0 ? "no node paired" : d.nodes.online === 0 ? "none reporting (gateway offline or no data)" : d.nodes.online < d.nodes.total ? `${d.nodes.total - d.nodes.online} state unknown` : "all reporting"} tone={d && d.nodes.offline > 0 ? "text-amber-600" : ""} />
        <Stat label="Total flow now" value={total?.flowLpm != null ? total.flowLpm.toFixed(2) : "—"} unit="L/min"
          hint={total?.basis === "source_meter" ? `at the main supply (Node ${total.node})` : total?.basis === "sum_of_nodes" ? "sum of all nodes (no pipeline yet)" : "no flow node yet"} />
        <Stat label="Used today" value={total?.todayLiters != null ? liters(total.todayLiters) : "—"} hint={total?.weekLiters != null ? `${liters(total.weekLiters)} in 7 days` : undefined} />
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        <Card className={leakCount ? "border-red-300" : lc.state === "paused" ? "border-amber-300" : ""} data-testid="ov-leaks">
          <CardHeader className="pb-2"><CardTitle className="text-base flex items-center gap-2"><AlertTriangle className="h-4 w-4" />Leaks</CardTitle>
            <CardDescription>{d?.pipeline ? `Pipeline “${d.pipeline.name}”` : "No active pipeline"}</CardDescription></CardHeader>
          <CardContent className="space-y-2">
            {!d?.pipeline ? <p className="text-sm text-muted-foreground">Leak detection needs a pipeline with your nodes placed on it.</p>
              : leakCount === 0 && lc.state === "paused" ? <div className="space-y-1" data-testid="ov-leak-paused"><Pill tone="amber">leak check paused</Pill><p className="text-sm text-amber-800">{lc.headline}{lc.nodes.length ? `: ${lc.nodes.join(", ")}` : ""}</p></div>
              : leakCount === 0 && lc.state === "watching" ? <div className="space-y-1" data-testid="ov-leak-watching"><Pill tone="amber">watching</Pill><p className="text-sm text-amber-800">{lc.headline}</p></div>
              : leakCount === 0 && lc.state !== "ok" ? <div className="flex items-center gap-2"><Pill tone="gray">{lc.state === "none" ? "nothing monitored yet" : "checking…"}</Pill></div>
              : leakCount === 0 ? <div className="space-y-1"><div className="flex items-center gap-2"><Pill tone="green">no active leak</Pill></div>{lc.note && <p className="text-xs text-muted-foreground" data-testid="ov-leak-note">{lc.note}</p>}</div>
              : <div className="flex flex-wrap gap-2">{d.leaks.confirmed > 0 && <Pill tone="red">{d.leaks.confirmed} confirmed</Pill>}{d.leaks.suspected > 0 && <Pill tone="amber">{d.leaks.suspected} suspected</Pill>}</div>}
            <div className="flex flex-wrap gap-2">
              {d?.pipeline && <Button asChild size="sm"><Link to={`/pipelines/${d.pipeline.id}?mode=live`}><GitBranch className="mr-1 h-3.5 w-3.5" />Pipeline Live view</Link></Button>}
              {!d?.pipeline && <Button asChild size="sm" variant="outline"><Link to="/pipelines">Create a pipeline</Link></Button>}
              <Button asChild size="sm" variant="outline"><Link to="/leak-detection">Leak detection</Link></Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-base flex items-center gap-2"><BatteryWarning className="h-4 w-4" />Batteries</CardTitle><CardDescription>Low limit {(d?.battery.lowLimitMv ?? 0) / 1000} V</CardDescription></CardHeader>
          <CardContent className="space-y-1 text-sm">
            {d && d.battery.low.length === 0 && !d.battery.implausible?.length && <p className={d.battery.withBattery === 0 ? "text-muted-foreground" : "text-emerald-700"}>{d.battery.withBattery === 0 ? "No node reports a battery." : "All batteries are fine."}</p>}
            {d?.battery.implausible?.map((b) => <p key={`i${b.number}`} className="text-amber-700 font-medium" title="A 2S pack cannot read this low or high: check the divider wiring on GPIO3, or the node has no battery.">Node {b.number}{b.name ? ` (${b.name})` : ""}: {batteryText(b.mv)} is not a plausible battery reading</p>)}
            {d?.battery.low.map((b) => <p key={b.number} className="text-red-600 font-medium">Node {b.number}{b.name ? ` (${b.name})` : ""}: {batteryText(b.mv)}</p>)}
            {d && d.battery.noBattery > 0 && <p className="text-muted-foreground">{d.battery.noBattery} node{d.battery.noBattery === 1 ? "" : "s"} without a battery sensor ("No battery").</p>}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2"><div className="flex items-center justify-between"><CardTitle className="text-base flex items-center gap-2"><Server className="h-4 w-4" />Active alerts</CardTitle><Link to="/alerts" className="text-xs underline">all alerts</Link></div></CardHeader>
          <CardContent>
            {(alerts.data ?? []).length === 0 ? <p className="text-sm text-emerald-700">Nothing needs attention.</p> : (
              <ul className="space-y-1.5 text-sm">{alerts.data!.slice(0, 5).map((a) => (
                <li key={a.id} className="flex items-start gap-2"><Pill tone={a.severity === "critical" ? "red" : a.severity === "warning" ? "amber" : "blue"}>{a.severity}</Pill><span className="min-w-0 break-words">{a.message}</span></li>))}</ul>
            )}
          </CardContent>
        </Card>
      </div>

      {rw && (
        <Card data-testid="ov-rainwater">
          <CardHeader className="pb-2"><div className="flex items-center justify-between"><CardTitle className="text-base flex items-center gap-2"><CloudRain className="h-4 w-4" />Rainwater</CardTitle><Link to="/rainwater" className="text-xs underline">rainwater system</Link></div>
            <CardDescription>{rw.active ? "Rain node online" : "Rain node offline - last readings"}</CardDescription></CardHeader>
          <CardContent>
            <div className="grid gap-2 grid-cols-2 lg:grid-cols-4 text-sm">
              <div className="rounded-lg border p-3"><div className="text-xs text-muted-foreground">Tank level</div><div className="font-mono text-xl font-bold" data-testid="ov-rw-level">{rw.levelValid && rw.waterLevel !== null ? `${rw.waterLevel.toFixed(0)} %` : rw.outOfRange ? "Out of range" : "Sensor error"}</div></div>
              <div className="rounded-lg border p-3"><div className="text-xs text-muted-foreground">Rain</div><div className="text-xl font-bold">{rw.rain ? "Raining" : "Dry"}</div></div>
              <div className="rounded-lg border p-3"><div className="text-xs text-muted-foreground">Water quality</div><div className="text-xl font-bold">{!rw.turbidityValid ? "Sensor error" : rw.waterClear ? "Clear" : "Turbid"}</div></div>
              <div className="rounded-lg border p-3"><div className="text-xs text-muted-foreground">Valve</div><div className="text-xl font-bold">{rw.valveOpen ? "Open" : "Closed"}</div><div className="text-xs text-muted-foreground">{valveStateName(rw.valveState)}{rw.dryRun ? " · dry-run (relay not energised)" : ""}</div></div>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader className="pb-2"><div className="flex items-center justify-between"><CardTitle className="text-base flex items-center gap-2"><Droplets className="h-4 w-4" />Flow by node</CardTitle><Link to="/water-monitoring" className="text-xs underline">water monitoring</Link></div></CardHeader>
        <CardContent>
          {nodes.loading ? <Loading /> : flowNodes.length === 0 ? <EmptyState title="No flow node yet" action={{ to: "/nodes", label: "Go to Nodes" }}>No flow node is paired.</EmptyState> : (
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              {flowNodes.map((n) => (
                <div key={n.id} className="rounded-lg border p-3" data-testid={`ov-node-${n.number}`}>
                  <div className="flex items-center justify-between gap-2"><span className="font-medium truncate">{nodeTitle(n)}</span><Pill tone={deviceTone(n.state)}>{n.state}</Pill></div>
                  <div className="mt-1 font-mono text-xl font-bold">{lpm(n.live?.flowLpm)}<span className="ml-1 text-xs font-normal text-muted-foreground">L/min</span></div>
                  <div className="text-xs text-muted-foreground">today {liters(n.live?.todayLiters)} · <span className="inline-flex items-center gap-1"><Radio className="h-3 w-3" />{batteryText(n.batteryMv)}</span></div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
