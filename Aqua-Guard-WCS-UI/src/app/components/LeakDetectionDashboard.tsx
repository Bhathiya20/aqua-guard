// Leak Detection page: pipeline selector, zone diagram, balance state per zone, node table and usage status.
import { useState } from "react";
import { Link } from "react-router";
import { AlertTriangle, Settings, ShieldCheck } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { Button } from "./ui/button";
import { ConnectionBanner, EmptyState, Loading, PageHeader, Pill } from "./common";
import { fmtTime, usePolling } from "../lib/usePolling";
import { useActivePipeline } from "../lib/useActivePipeline";
import { leakCheck, v2, type LiveZone } from "../lib/v2";
import { STATUS_LABEL } from "./pipeline/PipelinePanels";
import { DemoMode, RuleProgress, WeakWhy } from "./LeakInfo";
import { DemoClockSwitch } from "./DemoClock";
import { Disclosure } from "./leak/Disclosure";
import { ZoneSettingsDialog } from "./leak/ZoneSettings";
import { useAuth } from "../lib/auth";
import { Comparison, NodeTable, PipelineSelector, ZoneDiagram } from "./leak/LeakParts";
import { UsageLiveTable } from "./usage/UsageParts";
import { BALANCE_TEXT, BalanceState, balanceTone } from "./leak/BalancePanel";

const CLASS_TEXT: Record<string, { text: string; tone: "green" | "blue" | "gray" }> = {
  strong: { text: "strong (in and out measured)", tone: "green" }, weak: { text: "weak (some branches unmeasured)", tone: "blue" }, not_monitored: { text: "not monitored", tone: "gray" },
};
const statusTone = (s: string) => (s === "confirmed" ? "red" : s === "suspected" || s === "calibrating" ? "amber" : s === "normal" ? "green" : "gray");
/** Chip of a zone: the WLDS balance state for strong zones (never green while D is out of range), the rule status otherwise. */
function zoneChip(z: LiveZone): { tone: string; text: string } {
  if (z.class === "strong" && z.balanceState && z.status !== "confirmed" && z.status !== "calibrating") return { tone: balanceTone(z.balanceState, z.status), text: BALANCE_TEXT[z.balanceState] };
  return { tone: statusTone(z.status), text: STATUS_LABEL[z.status] ?? z.status };
}

function ZoneCard({ z, pid, admin }: { z: LiveZone; pid: number; admin: boolean }) {
  const [more, setMore] = useState(false), [cfg, setCfg] = useState(false);
  const b = z.balance, cls = CLASS_TEXT[z.class] ?? CLASS_TEXT.not_monitored, chip = zoneChip(z);
  const summary = z.class === "not_monitored" ? (z.reason || "No inflow node: this part cannot be checked for leaks.")
    : b ? `In ${b.qIn.toFixed(2)} · out ${b.qOut.toFixed(2)} L/min` : z.reason;
  return (
    <Card data-testid={`leak-zone-${z.id}`} className={z.status === "confirmed" ? "border-red-300" : z.status === "suspected" || z.balanceState === "suspicious" ? "border-amber-300" : z.balanceState === "inconsistent" ? "border-violet-300" : ""}>
      <CardHeader className="pb-2">
        <div className="flex flex-wrap items-center justify-between gap-2"><CardTitle className="text-base">{z.name}</CardTitle><Pill tone={chip.tone as never}>{chip.text}</Pill></div>
        <CardDescription><Pill tone={cls.tone}>{cls.text}</Pill></CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-muted-foreground" data-testid={`zone-summary-${z.id}`}>{summary}</p>
        {z.nodesMissing.length > 0 && <p className="text-xs text-amber-700">No recent data from: {z.nodesMissing.join(", ")}.</p>}
        <Button size="sm" variant="outline" aria-expanded={more} onClick={() => setMore((m) => !m)} data-testid={`zone-more-${z.id}`}>{more ? "Fewer details" : "More details"}</Button>
        {more && (
          <div className="space-y-3 border-t pt-3" data-testid={`zone-details-${z.id}`}>
            {z.class !== "not_monitored" && (
              <>
                <BalanceState z={z} />
                <Comparison z={z} />
                <p className="text-[11px] text-muted-foreground">{z.reason}</p>
                <ZoneDiagram z={z} />
                <WeakWhy z={z} />
                <div>
                  <div className="mb-1 text-xs font-medium text-muted-foreground">Rules</div>
                  {z.rules.length === 0 ? <p className="text-xs text-muted-foreground">No rule has run yet.</p> : <RuleProgress z={z} />}
                </div>
              </>)}
            <div className="flex flex-wrap gap-2">
              <Button size="sm" onClick={() => setCfg(true)} data-testid={`zone-settings-btn-${z.id}`}><Settings className="mr-1 h-4 w-4" />Settings</Button>
              <Button asChild size="sm" variant="outline"><Link to={`/pipelines/${pid}?mode=live&zone=${encodeURIComponent(z.id)}`}>Show in pipeline Live view</Link></Button>
            </div>
          </div>)}
        {cfg && <ZoneSettingsDialog z={z} pid={pid} admin={admin} onClose={() => setCfg(false)} />}
      </CardContent>
    </Card>
  );
}

export function LeakDetectionDashboard() {
  const ap = useActivePipeline(3000);
  const leaks = usePolling(() => (ap.active ? v2.zoneLeaks(ap.active.id) : Promise.resolve({ leaks: [] })), 10000, [ap.active?.id]);
  const zones = ap.status.data?.zones ?? [];
  const lc = leakCheck(zones);
  const { user } = useAuth();
  const admin = user?.role === "admin";

  return (
    <div className="space-y-5">
      <PageHeader icon={<AlertTriangle className="h-6 w-6 text-blue-600" />} title="Leak Detection" subtitle="Each zone of your pipeline is checked on its own: water in must equal water out." />
      <ConnectionBanner error={ap.error} stale={ap.stale} onRetry={ap.refresh} />
      {!ap.loading && (ap.list.data?.pipelines.length ?? 0) > 0 && <PipelineSelector pipelines={ap.list.data!.pipelines} admin={admin} onChanged={ap.refresh} />}
      {ap.loading ? <Loading /> : !ap.active ? (
        <Card><CardContent><EmptyState title="Not configured" action={{ to: "/pipelines", label: "Open Pipelines" }}>No pipeline is active, so there is nothing to check. Draw your pipes and place the nodes on them.</EmptyState></CardContent></Card>
      ) : (
        <>
          <div data-testid="leak-overall" data-state={lc.state} className={`flex flex-wrap items-center gap-3 rounded-xl border p-4 ${lc.state === "confirmed" ? "border-red-300 bg-red-50" : lc.state === "suspected" || lc.state === "paused" ? "border-amber-300 bg-amber-50" : lc.state === "inconsistent" ? "border-violet-300 bg-violet-50" : lc.state === "watching" ? "border-2 border-amber-400 bg-card" : lc.state === "ok" ? "border-emerald-200 bg-emerald-50" : "bg-card"}`}>
            <ShieldCheck className="h-6 w-6" />
            <div className="min-w-0 flex-1">
              <div className="font-semibold">{lc.headline}</div>
              {lc.paused.length > 0 && <div className="text-sm text-amber-900" data-testid="leak-paused-detail">{lc.paused.map((z) => z.name).join(", ")}{lc.nodes.length ? ` · no recent data from ${lc.nodes.join(", ")}` : ""}</div>}
              {lc.note && <div className="text-sm text-slate-700" data-testid="leak-excluded-note">{lc.note}</div>}
              <div className="text-sm text-muted-foreground">Pipeline “{ap.active.name}” v{ap.active.activeVersion} · {ap.status.data?.coverage.text ?? ""}</div>
            </div>
          </div>
          {admin && (
            <Disclosure id="demo" title="Demo tools" summary="demo mode and demo clock (admin)">
              <DemoMode pipelineId={ap.active.id} demo={!!ap.status.data?.demo} demoUntil={ap.status.data?.demoUntil ?? null} admin={admin} onChange={ap.refresh} />
              <DemoClockSwitch admin={admin} onChange={ap.refresh} />
            </Disclosure>)}
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{zones.map((z) => <ZoneCard key={z.id} z={z} pid={ap.active!.id} admin={admin} />)}</div>
          <Disclosure id="nodes" title="Nodes on this pipeline" summary={`${(ap.status.data?.nodes ?? []).length} nodes`}>
            <NodeTable rows={ap.status.data?.nodes ?? []} />
            <p className="text-xs text-muted-foreground">Settings of a node (name, calibration, usage limits) are in its "…" menu on the <Link className="underline" to="/nodes">Nodes</Link> page.</p>
          </Disclosure>
          <Disclosure id="usage" title="Usage per node" summary="how long water has been running">
            <UsageLiveTable title="" />
          </Disclosure>
          {(ap.status.data?.suggestions.length ?? 0) > 0 && (
            <Disclosure id="suggestions" title="Suggestions to cover more pipework" summary={`${ap.status.data!.suggestions.length}`}>
              <ul className="list-disc pl-5 text-sm space-y-1">{ap.status.data!.suggestions.map((s, i) => <li key={i}>{String(s.text ?? "")}</li>)}</ul>
            </Disclosure>
          )}
          <Disclosure id="history" title="Leak history" summary={(leaks.data?.leaks.length ?? 0) === 0 ? "none recorded" : `${leaks.data!.leaks.length} recorded`}>
            {(leaks.data?.leaks.length ?? 0) === 0 ? <EmptyState>No leak has been recorded for this pipeline.</EmptyState> : (
              <div className="rounded-lg border overflow-x-auto"><table className="w-full text-sm">
                <thead className="bg-muted/50 text-left text-xs text-muted-foreground"><tr><th className="p-2">Started</th><th className="p-2">Zone</th><th className="p-2">State</th><th className="p-2">Version</th><th className="p-2">Cleared</th><th className="p-2">Message</th></tr></thead>
                <tbody>{leaks.data!.leaks.slice(0, 50).map((l) => (
                  <tr key={l.id} className="border-t align-top"><td className="p-2 whitespace-nowrap">{fmtTime(l.created_at)}</td><td className="p-2">{l.zone_id}</td>
                    <td className="p-2"><Pill tone={l.active ? (l.state === "confirmed" ? "red" : "amber") : "green"}>{l.active ? l.state : "resolved"}</Pill></td><td className="p-2">v{l.pipeline_version}</td><td className="p-2 whitespace-nowrap">{l.cleared_at ? fmtTime(l.cleared_at) : "—"}</td><td className="p-2 min-w-48">{l.message}</td></tr>))}</tbody>
              </table></div>
            )}
          </Disclosure>
        </>
      )}
    </div>
  );
}
