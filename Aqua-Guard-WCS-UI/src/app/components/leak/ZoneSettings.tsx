// Settings of one zone (any class): balance thresholds and time rules (inherited from the global quiet hours or overridden), in one dialog.
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "../ui/dialog";
import { Pill } from "../common";
import { usePolling } from "../../lib/usePolling";
import { v2, type LiveZone } from "../../lib/v2";
import { BalanceSettings } from "./BalancePanel";
import { settingsSentence } from "../LeakInfo";

type F = { qs: string; qe: string; sus: string; con: string };
type K = "quietStart" | "quietEnd" | "continuousSuspectH" | "continuousConfirmH";

/** Quiet hours and continuous-flow limits of a zone. A value that is not overridden is inherited and labelled "default". */
export function ZoneTimeSettings({ z, pid, admin }: { z: LiveZone; pid: number; admin: boolean }) {
  const cur = usePolling(() => v2.zoneTime(pid, z.id), 15000, [pid, z.id]);
  const t = cur.data;
  const [f, setF] = useState<F>({ qs: "", qe: "", sus: "", con: "" });
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (t) setF({ qs: t.quietStart, qe: t.quietEnd, sus: String(t.continuousSuspectH), con: String(t.continuousConfirmH) }); }, [t?.quietStart, t?.quietEnd, t?.continuousSuspectH, t?.continuousConfirmH]);
  const save = async () => {
    setBusy(true);
    try {
      await v2.setZoneTime(pid, z.id, { quiet_start: f.qs, quiet_end: f.qe, continuous_suspect_h: Number(f.sus), continuous_confirm_h: Number(f.con) });
      toast.success("Time rules saved"); cur.refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Could not save"); } finally { setBusy(false); }
  };
  const reset = async () => {
    setBusy(true);
    try { await v2.resetZoneTime(pid, z.id); toast.success("Time rules are back to the defaults"); cur.refresh(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Could not reset"); } finally { setBusy(false); }
  };
  const tag = (k: K) => (t?.source[k] === "zone" ? <Pill tone="blue">overridden</Pill> : <Pill tone="gray">default</Pill>);
  const row = (k: K, label: string, field: keyof F, type: string, unit?: string) => (
    <label className="flex flex-wrap items-center gap-2 text-sm"><span className="w-52 text-muted-foreground">{label}</span>
      <Input className="h-8 w-28" type={type} step={type === "number" ? "0.5" : undefined} min={type === "number" ? "0.05" : undefined} value={f[field]} disabled={!admin || busy} aria-label={label} onChange={(e) => setF({ ...f, [field]: e.target.value })} />
      {unit && <span className="text-xs text-muted-foreground">{unit}</span>}{tag(k)}</label>);
  return (
    <div className="space-y-2 rounded-md border p-3" data-testid={`time-settings-${z.id}`} data-overridden={t?.overridden ? "1" : "0"}>
      <div className="flex flex-wrap items-center gap-2 text-sm font-medium">Time rules {t?.overridden ? <Pill tone="blue">this zone overrides the defaults</Pill> : <Pill tone="gray">all values inherited</Pill>}</div>
      <p className="text-xs text-muted-foreground">Night flow is looked for during the quiet hours. The quiet hours are shared with the node usage profiles ({t ? `${t.defaults.quietStart}-${t.defaults.quietEnd}` : "…"}); change them for everything on the <Link className="underline" to="/usage-settings">Usage settings</Link> page, or override them for this zone only.</p>
      {row("quietStart", "Quiet hours start", "qs", "time")}
      {row("quietEnd", "Quiet hours end", "qe", "time")}
      {row("continuousSuspectH", "Continuous flow: suspected after", "sus", "number", "h")}
      {row("continuousConfirmH", "Continuous flow: confirmed after", "con", "number", "h")}
      {admin && (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" disabled={busy || !t} onClick={save} data-testid={`save-time-${z.id}`}>Save</Button>
          <Button size="sm" variant="outline" disabled={busy || !t?.overridden} onClick={reset} data-testid={`reset-time-${z.id}`}>Reset to defaults</Button>
        </div>)}
    </div>
  );
}

export function ZoneSettingsDialog({ z, pid, admin, onClose }: { z: LiveZone; pid: number; admin: boolean; onClose: () => void }) {
  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-h-[90vh] max-w-xl overflow-y-auto" data-testid={`zone-settings-dialog-${z.id}`}>
        <DialogHeader><DialogTitle>Settings · {z.name}</DialogTitle>
          <DialogDescription>{admin ? "Changes apply to this zone only." : "Only an administrator can change these values."}</DialogDescription></DialogHeader>
        {z.class === "strong" ? <BalanceSettings z={z} pid={pid} admin={admin} /> : (
          <p className="rounded-md border border-blue-200 bg-blue-50 p-2 text-xs text-blue-900" data-testid="no-balance-why">
            {z.class === "weak" ? "No balance thresholds here: part of the water leaves through outlets that no node measures, so in-minus-out is not a leak signal. This zone uses the time rules below instead." : "No balance thresholds here: this part has no inflow node, so it cannot be checked."}
          </p>)}
        <ZoneTimeSettings z={z} pid={pid} admin={admin} />
        {z.settings && <p className="text-[11px] text-muted-foreground" data-testid="zone-settings">In force now: {settingsSentence(z)}</p>}
      </DialogContent>
    </Dialog>
  );
}
