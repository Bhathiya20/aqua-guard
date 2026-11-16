// Alerts page: Active tab (grouped alerts, acknowledge, resolve with a reason) and History tab.
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { toast } from "sonner";
import { Bell, Check, CheckCheck } from "lucide-react";
import { Button } from "./ui/button";
import { Card, CardContent } from "./ui/card";
import { ConnectionBanner, EmptyState, Loading, PageHeader, Pill } from "./common";
import { useAuth } from "../lib/auth";
import { ageSince, fmtDuration, fmtTime, usePolling } from "../lib/usePolling";
import { v2, type AlertV2 } from "../lib/v2";
import { HistoryTab } from "./alerts/HistoryTab";
import { ResolveDialog } from "./alerts/ResolveDialog";

export const KIND_LABEL: Record<string, string> = {
  usage_prolonged: "Prolonged usage", usage_continuous: "Continuous abnormal usage", night_flow: "Night flow", measurement_inconsistency: "Measurement inconsistency",
  leak_suspected: "Leak suspected", leak_confirmed: "Leak confirmed", node_offline: "Node offline", low_battery: "Low battery", missing_packets: "Missing packets",
  gateway_offline: "Gateway offline", comm_degraded: "Communication degraded", sensor_offline: "Sensor offline (v1)", tank_full: "Tank full", unsafe_turbidity: "Unsafe turbidity", valve_fault: "Valve fault",
};
const sevTone = (s: string) => (s === "critical" ? "red" : s === "warning" ? "amber" : "blue");

/** Leak alerts are one row per event that moves suspected -> confirmed -> resolved; show that life cycle as text. */
export function alertState(a: AlertV2): { text: string; tone: "red" | "amber" | "green" | "gray" } {
  if (!a.active) return { text: a.kind.startsWith("leak") ? "resolved" : "cleared", tone: "green" };
  if (a.kind === "leak_confirmed") return { text: "confirmed", tone: "red" };
  if (a.kind === "leak_suspected") return { text: "suspected", tone: "amber" };
  return { text: "active", tone: "amber" };
}

export function AlertsPage() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const alerts = usePolling(() => v2.alerts(false, 400), 3000);
  const nodes = usePolling(() => v2.nodes(), 15000);
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") === "history" ? "history" : "active";
  const [resolving, setResolving] = useState<{ ids: number[]; label: string } | null>(null);
  const [status, setStatus] = useState<"active" | "resolved" | "all">("active");
  const [kind, setKind] = useState("all");
  const [sev, setSev] = useState("all");
  const [busy, setBusy] = useState<number | string | null>(null);
  const [openG, setOpenG] = useState<Record<string, boolean>>({});

  const nodeBy = useMemo(() => new Map((nodes.data?.nodes ?? []).map((n) => [n.id, n])), [nodes.data]);
  const all = alerts.data ?? [];
  const kinds = useMemo(() => Array.from(new Set(all.map((a) => a.kind))).sort(), [all]);
  const rows = all.filter((a) => (status === "all" || (status === "active") === a.active) && (kind === "all" || a.kind === kind) && (sev === "all" || a.severity === sev));

  const act = async (a: AlertV2, what: "ack" | "resolve") => {
    if (what === "resolve") { setResolving({ ids: [a.id], label: KIND_LABEL[a.kind] ?? a.kind }); return; }       // manual resolve asks for the reason
    setBusy(a.id);
    try { await v2.ack(a.id); toast.success("Acknowledged"); alerts.refresh(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); } finally { setBusy(null); }
  };
  const actAll = async (g: AlertV2[], what: "ack" | "resolve") => {
    const todo = g.filter((a) => a.active && (what === "ack" ? !a.acknowledgedBy : true));
    if (what === "resolve") { setResolving({ ids: todo.map((a) => a.id), label: `${todo.length} alerts` }); return; }
    setBusy(`g:${g[0].kind}`);
    try { for (const a of todo) await v2.ack(a.id); toast.success(`Acknowledged ${todo.length}`); alerts.refresh(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); } finally { setBusy(null); }
  };
  const groups = useMemo(() => {
    const m = new Map<string, AlertV2[]>();
    for (const a of rows) { const k = `${a.kind}|${a.active ? 1 : 0}`; m.set(k, [...(m.get(k) ?? []), a]); }
    return Array.from(m.entries()).map(([k, list]) => ({ k, list: [...list].sort((x, y) => (y.createdAt > x.createdAt ? 1 : -1)) }));
  }, [rows]);
  const since = (a: AlertV2) => (a.active ? `active for ${fmtDuration(ageSince(a.createdAt))}` : a.clearedAt ? `lasted ${fmtDuration((Date.parse(a.clearedAt.replace(" ", "T") + "Z") - Date.parse(a.createdAt.replace(" ", "T") + "Z")) / 1000)}` : "");
  const nodeText = (a: AlertV2) => { const n = a.nodeRef ? nodeBy.get(a.nodeRef) : undefined; return n ? `Node ${n.number}${n.name ? ` (${n.name})` : ""}` : a.nodeRef ? `node #${a.nodeRef}` : "—"; };

  const sel = "h-9 rounded-md border bg-input-background px-2 text-sm";
  return (
    <div className="space-y-4">
      <PageHeader icon={<Bell className="h-6 w-6 text-blue-600" />} title="Alerts" subtitle="Leaks, offline nodes, batteries, lost packets and the gateway." />
      <ConnectionBanner error={alerts.error} stale={alerts.stale} onRetry={alerts.refresh} />
      <div role="tablist" aria-label="Alerts view" className="flex gap-1 border-b" data-testid="alerts-tabs">
        {(["active", "history"] as const).map((t) => (
          <button key={t} type="button" role="tab" aria-selected={tab === t} data-testid={`tab-${t}`} onClick={() => setSp(t === "active" ? {} : { tab: t })}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium ${tab === t ? "border-blue-600 text-blue-700" : "border-transparent text-muted-foreground hover:text-foreground"}`}>{t === "active" ? "Active" : "History"}</button>))}
      </div>
      {tab === "history" ? <HistoryTab initialNode={sp.get("node") ?? ""} initialZone={sp.get("zone") ?? ""} /> : (<>
      <div className="flex flex-wrap gap-2" data-testid="alert-filters">
        <select aria-label="Status" className={sel} value={status} onChange={(e) => setStatus(e.target.value as typeof status)}><option value="active">Active</option><option value="resolved">Resolved / cleared</option><option value="all">All</option></select>
        <select aria-label="Type" className={sel} value={kind} onChange={(e) => setKind(e.target.value)}><option value="all">All types</option>{kinds.map((k) => <option key={k} value={k}>{KIND_LABEL[k] ?? k}</option>)}</select>
        <select aria-label="Severity" className={sel} value={sev} onChange={(e) => setSev(e.target.value)}><option value="all">All severities</option><option value="critical">Critical</option><option value="warning">Warning</option><option value="info">Info</option></select>
        <span className="self-center text-sm text-muted-foreground">{rows.length} shown</span>
      </div>

      {alerts.loading ? <Loading /> : rows.length === 0 ? <Card><CardContent><EmptyState title={status === "active" ? "No active alerts" : "No matching alerts"} action={{ to: "/", label: "Back to Overview" }}>{status === "active" ? "Everything is fine." : "No alerts match these filters."}</EmptyState></CardContent></Card> : (
        <ul className="space-y-2">
          {groups.map(({ k, list }) => {
            const open = list.length === 1 || openG[k];
            const head = list[0];
            const many = list.length > 1;
            return (
              <li key={k} data-testid={`alert-group-${head.kind}`} className="space-y-2">
                {many && (
                  <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/40 px-3 py-2 text-sm">
                    <Pill tone={sevTone(head.severity) as never}>{head.severity}</Pill><b>{KIND_LABEL[head.kind] ?? head.kind}</b><span className="rounded-full bg-background px-2 text-xs" data-testid="group-count">{list.length} alerts</span>
                    <button type="button" className="text-xs underline" onClick={() => setOpenG((o) => ({ ...o, [k]: !o[k] }))}>{open ? "hide" : "show all"}</button>
                    {admin && head.active && (
                      <span className="ml-auto flex gap-1.5">
                        {list.some((a) => !a.acknowledgedBy) && <Button size="sm" variant="outline" disabled={busy === `g:${head.kind}`} onClick={() => actAll(list, "ack")}><Check className="mr-1 h-3.5 w-3.5" />Acknowledge all</Button>}
                        <Button size="sm" variant="outline" disabled={busy === `g:${head.kind}`} onClick={() => actAll(list, "resolve")}><CheckCheck className="mr-1 h-3.5 w-3.5" />Resolve all</Button>
                      </span>)}
                  </div>)}
                {(open ? list : list.slice(0, 1)).map((a) => {
                  const st = alertState(a);
                  return (
                    <div key={a.id} data-testid={`alert-${a.id}`} className="rounded-lg border bg-card p-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <Pill tone={sevTone(a.severity) as never}>{a.severity}</Pill><b className="text-sm">{KIND_LABEL[a.kind] ?? a.kind}</b><Pill tone={st.tone}>{st.text}</Pill>
                        {a.demo && <Pill tone="violet">demo</Pill>}
                        {a.acknowledgedBy && <Pill tone="gray">acknowledged</Pill>}
                        <span className="ml-auto text-xs text-muted-foreground">{fmtTime(a.createdAt)}{a.clearedAt ? ` → ${fmtTime(a.clearedAt)}` : ""} · {since(a)}</span>
                      </div>
                      <p className="mt-1.5 text-sm break-words">{a.message}</p>
                      <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                        {a.zone && <span>Zone: <b>{a.zone}</b></span>}
                        {a.pipelineId && <span>Pipeline #{a.pipelineId}{a.pipelineVersion ? ` v${a.pipelineVersion}` : ""}</span>}
                        {a.nodeRef && <span>{nodeText(a)}</span>}
                        {a.pipelineId && a.zone && <Link className="underline" to={`/pipelines/${a.pipelineId}?mode=live&zone=${encodeURIComponent(a.zone)}`}>Show in Live view</Link>}
                        {admin && a.active && (
                          <span className="ml-auto flex gap-1.5">
                            {!a.acknowledgedBy && <Button size="sm" variant="outline" disabled={busy === a.id} onClick={() => act(a, "ack")}><Check className="mr-1 h-3.5 w-3.5" />Acknowledge</Button>}
                            <Button size="sm" variant="outline" disabled={busy === a.id} onClick={() => act(a, "resolve")} data-testid={`resolve-${a.id}`}><CheckCheck className="mr-1 h-3.5 w-3.5" />Resolve</Button>
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
                {!open && many && <p className="px-1 text-xs text-muted-foreground">+ {list.length - 1} more of the same type (newest shown)</p>}
              </li>
            );
          })}
        </ul>
      )}
      </>)}
      {resolving && <ResolveDialog ids={resolving.ids} label={resolving.label} onClose={() => setResolving(null)} onDone={alerts.refresh} />}
    </div>
  );
}
