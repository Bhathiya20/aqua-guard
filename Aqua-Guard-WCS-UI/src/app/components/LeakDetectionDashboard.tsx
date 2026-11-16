// Leak Detection page: pipeline selector, zone diagram, balance state per zone, node table and usage status.
import { Link } from "react-router";
import { AlertTriangle, ShieldCheck } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { Button } from "./ui/button";
import { ConnectionBanner, EmptyState, Loading, PageHeader, Pill } from "./common";
import { fmtTime, usePolling } from "../lib/usePolling";
import { useActivePipeline } from "../lib/useActivePipeline";
import { leakCheck, v2, type LiveZone } from "../lib/v2";
import { STATUS_LABEL } from "./pipeline/PipelinePanels";
import { DemoMode, RuleProgress, WeakWhy, settingsSentence } from "./LeakInfo";
import { useAuth } from "../lib/auth";
import { Comparison, NodeTable, PipelineSelector, ZoneDiagram } from "./leak/LeakParts";
import { UsageLiveTable } from "./usage/UsageParts";
import { BALANCE_TEXT, BalanceSettings, BalanceState, balanceTone } from "./leak/BalancePanel";

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
  const b = z.balance, cls = CLASS_TEXT[z.class] ?? CLASS_TEXT.not_monitored, chip = zoneChip(z);
  return (
    <Card data-testid={`leak-zone-${z.id}`} className={z.status === "confirmed" ? "border-red-300" : z.status === "suspected" || z.balanceState === "suspicious" ? "border-amber-300" : z.balanceState === "inconsistent" ? "border-violet-300" : ""}>
      <CardHeader className="pb-2">
        <div className="flex flex-wrap items-center justify-between gap-2"><CardTitle className="text-base">{z.name}</CardTitle><Pill tone={chip.tone as never}>{chip.text}</Pill></div>
        <CardDescription><Pill tone={cls.tone}>{cls.text}</Pill></CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {z.class === "not_monitored" ? <p className="text-muted-foreground">{z.reason || "No inflow node: this part cannot be checked for leaks."}</p> : (
          <>
            <BalanceState z={z} />
            <Comparison z={z} />
            <p className="text-[11px] text-muted-foreground">{z.reason}</p>
            <ZoneDiagram z={z} />
            <WeakWhy z={z} />
            <div>
              <div className="mb-1 text-xs font-medium text-muted-foreground">Rules</div>
              {z.rules.length === 0 ? <p className="text-xs text-muted-foreground">No rule has run yet.</p> : <RuleProgress z={z} />}
              {z.settings && <p className="mt-1.5 text-[11px] text-muted-foreground" data-testid="zone-settings">{settingsSentence(z)}</p>}
            </div>
            <BalanceSettings z={z} pid={pid} admin={admin} />
            {z.nodesMissing.length > 0 && <p className="text-xs text-amber-700">No recent data from: {z.nodesMissing.join(", ")}.</p>}
          </>
        )}
        <Button asChild size="sm" variant="outline"><Link to={`/pipelines/${pid}?mode=live&zone=${encodeURIComponent(z.id)}`}>Show in pipeline Live view</Link></Button>
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

  return (
    <div className="space-y-5">
      <PageHeader icon={<AlertTriangle className="h-6 w-6 text-blue-600" />} title="Leak Detection" subtitle="Each zone of your pipeline is checked on its own: water in must equal water out." />
      <ConnectionBanner error={ap.error} stale={ap.stale} onRetry={ap.refresh} />
      {!ap.loading && (ap.list.data?.pipelines.length ?? 0) > 0 && <PipelineSelector pipelines={ap.list.data!.pipelines} admin={user?.role === "admin"} onChanged={ap.refresh} />}
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
          <DemoMode pipelineId={ap.active.id} demo={!!ap.status.data?.demo} demoUntil={ap.status.data?.demoUntil ?? null} admin={user?.role === "admin"} onChange={ap.refresh} />
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{zones.map((z) => <ZoneCard key={z.id} z={z} pid={ap.active!.id} admin={user?.role === "admin"} />)}</div>
          <section className="space-y-2">
            <h2 className="text-lg font-semibold">Nodes on this pipeline</h2>
            <NodeTable rows={ap.status.data?.nodes ?? []} />
          </section>
          <UsageLiveTable />
          {(ap.status.data?.suggestions.length ?? 0) > 0 && (
            <Card><CardHeader className="pb-2"><CardTitle className="text-base">Suggestions to cover more pipework</CardTitle></CardHeader>
              <CardContent><ul className="list-disc pl-5 text-sm space-y-1">{ap.status.data!.suggestions.map((s, i) => <li key={i}>{String(s.text ?? "")}</li>)}</ul></CardContent></Card>
          )}
          <section className="space-y-2">
            <h2 className="text-lg font-semibold">Leak history</h2>
            {(leaks.data?.leaks.length ?? 0) === 0 ? <Card><CardContent><EmptyState>No leak has been recorded for this pipeline.</EmptyState></CardContent></Card> : (
              <div className="rounded-lg border overflow-x-auto"><table className="w-full text-sm">
                <thead className="bg-muted/50 text-left text-xs text-muted-foreground"><tr><th className="p-2">Started</th><th className="p-2">Zone</th><th className="p-2">State</th><th className="p-2">Version</th><th className="p-2">Cleared</th><th className="p-2">Message</th></tr></thead>
                <tbody>{leaks.data!.leaks.slice(0, 50).map((l) => (
                  <tr key={l.id} className="border-t align-top"><td className="p-2 whitespace-nowrap">{fmtTime(l.created_at)}</td><td className="p-2">{l.zone_id}</td>
                    <td className="p-2"><Pill tone={l.active ? (l.state === "confirmed" ? "red" : "amber") : "green"}>{l.active ? l.state : "resolved"}</Pill></td><td className="p-2">v{l.pipeline_version}</td><td className="p-2 whitespace-nowrap">{l.cleared_at ? fmtTime(l.cleared_at) : "—"}</td><td className="p-2 min-w-48">{l.message}</td></tr>))}</tbody>
              </table></div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
