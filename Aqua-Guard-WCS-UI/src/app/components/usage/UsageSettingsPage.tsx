// Usage settings page: global default profile, live status and the audit of changes.
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Gauge } from "lucide-react";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { ConnectionBanner, Loading, PageHeader, Pill } from "../common";
import { useAuth } from "../../lib/auth";
import { fmtDuration, fmtTime, usePolling } from "../../lib/usePolling";
import { v2, type UsageProfile } from "../../lib/v2";
import { UsageEditor, UsageLiveTable } from "./UsageParts";

/** One page for the GLOBAL default usage profile (every node without its own settings uses it), the presets, and the audit of every change. */
export function UsageSettingsPage() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const q = usePolling(() => v2.usageDefaults(), 10000);
  const audit = usePolling(() => v2.usageAudit(30), 10000);
  const [draft, setDraft] = useState<UsageProfile | null>(null);
  const [busy, setBusy] = useState(false);
  const d = q.data;
  useEffect(() => { if (d && !draft) setDraft(d.profile); }, [d, draft]);
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try { await fn(); toast.success(ok); setDraft(null); q.refresh(); audit.refresh(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-5">
      <PageHeader icon={<Gauge className="h-6 w-6 text-blue-600" />} title="Usage settings" subtitle="How long water may run on a node before it is abnormal. These defaults apply to every node without its own settings." />
      <ConnectionBanner error={q.error} stale={q.stale} onRetry={q.refresh} />
      {!d || !draft ? <Loading /> : (
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-base">Default profile {d.custom ? <Pill tone="blue">edited</Pill> : <Pill tone="gray">factory values</Pill>}</CardTitle>
            <CardDescription>{d.updatedBy ? `Last changed by ${d.updatedBy}, ${fmtTime(d.updatedAt)}.` : "Not changed yet."} Per-node presets: {d.presets.filter((p) => p.tLimS && p.key !== "default").map((p) => `${p.label} ${fmtDuration(p.tLimS)} / ${fmtDuration(p.tMaxS)}`).join(" · ")}. Open a node's “…” menu → Usage settings to change one node.</CardDescription></CardHeader>
          <CardContent className="space-y-4">
            <UsageEditor value={draft} onChange={setDraft} presets={d.presets} disabled={!admin || busy} withPreset={false} />
            {admin ? (
              <div className="flex gap-2">
                <Button disabled={busy} onClick={() => run(() => v2.setUsageDefaults(draft), "Default profile saved")} data-testid="defaults-save">Save defaults</Button>
                <Button variant="outline" disabled={busy || !d.custom} onClick={() => run(() => v2.resetUsageDefaults(), "Factory values restored")}>Restore factory values</Button>
              </div>) : <p className="text-xs text-muted-foreground">Only an administrator can change the defaults.</p>}
          </CardContent>
        </Card>)}
      <UsageLiveTable title="Live status of every node" />
      <section className="space-y-2">
        <h2 className="text-lg font-semibold">Changes</h2>
        {(audit.data?.audit.length ?? 0) === 0 ? <p className="text-sm text-muted-foreground">No change has been made yet.</p> : (
          <div className="overflow-x-auto rounded-lg border" data-testid="usage-audit"><table className="w-full text-sm">
            <thead className="bg-muted/50 text-left text-xs text-muted-foreground"><tr><th className="p-2">When</th><th className="p-2">Who</th><th className="p-2">What</th></tr></thead>
            <tbody>{audit.data!.audit.map((a, i) => {
              let what = a.action;
              try { const j = JSON.parse(a.detail); what = `${a.action.replace("usage_", "")}${j.node ? ` · ${j.node}` : ""}${j.on !== undefined ? ` · demo ${j.on ? "on" : "off"}` : ""}`; } catch { /* detail was cut off: show the action only */ }
              return <tr key={i} className="border-t"><td className="p-2 whitespace-nowrap text-xs">{fmtTime(a.ts)}</td><td className="p-2 text-xs">{a.user ?? "—"}</td><td className="p-2 text-xs">{what}</td></tr>;
            })}</tbody></table></div>)}
      </section>
    </div>
  );
}
