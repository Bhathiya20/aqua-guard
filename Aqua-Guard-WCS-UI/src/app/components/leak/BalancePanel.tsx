// Balance panel of a zone (Q in, sum Q out, D, state) and its threshold settings with the Learn dialog.
import { useEffect, useState } from "react";
import { FlaskConical } from "lucide-react";
import { toast } from "sonner";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { Pill } from "../common";
import { usePolling } from "../../lib/usePolling";
import { v2, type BalanceState, type LearnResult, type LiveZone, type ZoneThresholds } from "../../lib/v2";

export const BALANCE_TEXT: Record<BalanceState, string> = { normal: "Normal", suspicious: "Suspicious", possible_leak: "Possible leak", inconsistent: "Measurement inconsistency", paused: "Leak check paused" };
export const balanceTone = (s: BalanceState | null | undefined, status?: string): "green" | "amber" | "red" | "violet" | "gray" =>
  status === "confirmed" ? "red" : s === "possible_leak" || s === "suspicious" ? "amber" : s === "inconsistent" ? "violet" : s === "paused" ? "gray" : s === "normal" ? "green" : "gray";
const n2 = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v.toFixed(2));

/** WLDS state of a strong zone: chip, live D with its band, and the counter T_D "x s of T_leak". Never green while D is outside the band. */
export function BalanceState({ z }: { z: LiveZone }) {
  const w = z.wlds, s = z.balanceState;
  if (z.class !== "strong" || !w || !s) return null;
  const pct = Math.min(100, Math.round((w.tdS / Math.max(w.tLeakS, 1)) * 100));
  return (
    <div className="space-y-1.5 rounded-md border p-2" data-testid={`balance-state-${z.id}`} data-state={s}>
      <div className="flex flex-wrap items-center gap-2">
        <Pill tone={balanceTone(s, z.status)}>{z.status === "confirmed" ? "Confirmed leak" : BALANCE_TEXT[s]}</Pill>
        {s === "suspicious" && <span className="text-xs font-medium text-amber-800" data-testid={`td-${z.id}`}>{w.tdS} s of {w.tLeakS} s</span>}
        {s === "possible_leak" && <span className="text-xs text-amber-800">D above +{n2(w.xPlusLpm)} L/min for {w.tdS >= 120 ? `${Math.round(w.tdS / 60)} min` : `${w.tdS} s`}</span>}
        <span className="ml-auto font-mono text-xs" data-testid={`d-live-${z.id}`}>D = {w.dLpm === null ? "—" : `${w.dLpm >= 0 ? "+" : ""}${n2(w.dLpm)}`} L/min <span className="text-muted-foreground">(band −{n2(w.xMinusLpm)} … +{n2(w.xPlusLpm)})</span></span>
      </div>
      {s === "suspicious" && <div className="h-1.5 overflow-hidden rounded bg-muted"><div className="h-full bg-amber-500" style={{ width: `${pct}%` }} /></div>}
      {s === "inconsistent" && <p className="text-xs text-violet-900" data-testid={`inconsistent-${z.id}`}>Outflow is larger than inflow: check sensor / calibration / test. This is not a leak.</p>}
      {s === "paused" && <p className="text-xs text-muted-foreground">A meter of this zone has no complete data, so the balance is not judged (never treated as zero or as normal).</p>}
    </div>
  );
}

// ---------------------------------------------------------------- x+ / x- / T_leak of one strong zone, with "Learn thresholds"
export function BalanceSettings({ z, pid, admin }: { z: LiveZone; pid: number; admin: boolean }) {
  const cur = usePolling(() => v2.zoneThresholds(pid, z.id), 15000, [pid, z.id, z.settings?.xPlusLpm, z.settings?.tLeakS]);
  const t = cur.data;
  const [f, setF] = useState({ xp: "", xm: "", tl: "" });
  const [busy, setBusy] = useState(false);
  const [learn, setLearn] = useState(false);
  useEffect(() => { if (t) setF({ xp: String(t.xPlusLpm), xm: String(t.xMinusLpm), tl: String(t.tLeakS) }); }, [t?.xPlusLpm, t?.xMinusLpm, t?.tLeakS]);
  if (z.class !== "strong") return null;
  const save = async () => {
    setBusy(true);
    try { await v2.setZoneThresholds(pid, z.id, { xPlusLpm: Number(f.xp), xMinusLpm: Number(f.xm), tLeakS: Number(f.tl), source: "manual" }); toast.success("Balance thresholds saved"); cur.refresh(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Could not save"); } finally { setBusy(false); }
  };
  const reset = async () => {
    setBusy(true);
    try { await v2.resetZoneThresholds(pid, z.id); toast.success("Back to the default thresholds"); cur.refresh(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Could not reset"); } finally { setBusy(false); }
  };
  const inp = (k: "xp" | "xm" | "tl", label: string, unit: string) => (
    <label className="flex items-center gap-1.5 text-xs"><span className="w-20 text-muted-foreground">{label}</span>
      <Input className="h-7 w-20" type="number" step="0.05" min="0" value={f[k]} disabled={!admin || busy} onChange={(e) => setF({ ...f, [k]: e.target.value })} aria-label={label} /><span className="text-muted-foreground">{unit}</span></label>);
  return (
    <div className="space-y-2 rounded-md border p-2" data-testid={`balance-settings-${z.id}`}>
      <div className="flex flex-wrap items-center gap-2 text-xs font-medium text-muted-foreground">Balance thresholds {t?.custom ? <Pill tone="blue">{t.source === "learned" ? "learned" : "custom"}</Pill> : <Pill tone="gray">default</Pill>}</div>
      <div className="flex flex-wrap gap-x-4 gap-y-1.5">{inp("xp", "x+  (D above)", "L/min")}{inp("xm", "x−  (D below −)", "L/min")}{inp("tl", "T_leak", "s")}</div>
      <p className="text-[11px] text-muted-foreground">The tolerance of the meters (10 % of the flow) still applies on top, so a high flow with its normal meter error is not a leak. Demo mode uses T_leak = {t?.defaults.demoTLeakS ?? 10} s.</p>
      {admin && (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" disabled={busy} onClick={save} data-testid={`save-thresholds-${z.id}`}>Save</Button>
          <Button size="sm" variant="outline" disabled={busy || !t?.custom} onClick={reset}>Reset to defaults</Button>
          <Button size="sm" disabled={busy} onClick={() => setLearn(true)} data-testid={`learn-open-${z.id}`}><FlaskConical className="mr-1 h-4 w-4" />Learn thresholds</Button>
        </div>)}
      {learn && <LearnDialog z={z} pid={pid} current={t ?? null} onClose={() => { setLearn(false); cur.refresh(); }} />}
    </div>
  );
}

// ---------------------------------------------------------------- guided no-leak test: x+/x- = mean D +- 3 SD
function LearnDialog({ z, pid, current, onClose }: { z: LiveZone; pid: number; current: ZoneThresholds | null; onClose: () => void }) {
  const st = usePolling(() => v2.learnState(pid, z.id), 2000, [pid, z.id]);
  const s = st.data?.session ?? null;
  const [busy, setBusy] = useState(false);
  const [edit, setEdit] = useState<{ xp: string; xm: string } | null>(null);
  const [touched, setTouched] = useState(false);
  const running = s?.status === "running", done = s?.status === "done" && !!s.result;
  const res: LearnResult | undefined = running ? s?.progress : done ? s?.result : undefined;
  useEffect(() => { if (done && s?.result && !touched) setEdit({ xp: String(s.result.xPlusLpm), xm: String(s.result.xMinusLpm) }); }, [done, s?.id]);
  const act = async (a: "start" | "stop" | "cancel") => {
    setBusy(true);
    try { await v2.learnAction(pid, z.id, a); setTouched(false); setEdit(null); st.refresh(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Could not do that"); } finally { setBusy(false); }
  };
  const accept = async () => {
    if (!edit || !s?.result) return;
    const unchanged = Number(edit.xp) === s.result.xPlusLpm && Number(edit.xm) === s.result.xMinusLpm;
    setBusy(true);
    try { await v2.setZoneThresholds(pid, z.id, { xPlusLpm: Number(edit.xp), xMinusLpm: Number(edit.xm), source: unchanged ? "learned" : "manual" }); toast.success("New thresholds are in force"); onClose(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Could not save"); } finally { setBusy(false); }
  };
  return (
    <Dialog open onOpenChange={(o) => { if (!o) { if (running) void v2.learnAction(pid, z.id, "cancel").catch(() => undefined); onClose(); } }}>
      <DialogContent className="max-w-lg" data-testid="learn-dialog">
        <DialogHeader><DialogTitle>Learn thresholds · {z.name}</DialogTitle>
          <DialogDescription>Run normal water with <b>no leak and no air</b> through every meter of this zone for at least 2 minutes (open the end tap). The system measures D = Q in − Σ Q out every {res?.windowS ?? 5} s and sets x+ and x− to the mean ± 3 standard deviations.</DialogDescription></DialogHeader>
        {!running && !done && <p className="text-sm" data-testid="learn-intro">Current: x+ {current?.xPlusLpm ?? "—"} · x− {current?.xMinusLpm ?? "—"} L/min. Press <b>Start</b>, then open the tap.</p>}
        {res && (
          <div className="space-y-2 text-sm" data-testid="learn-numbers">
            <div className="grid grid-cols-3 gap-2 text-center">
              <div className="rounded-md border p-2"><div className="text-[11px] text-muted-foreground">Windows</div><div className="font-mono font-semibold">{res.windows} / {res.totalWindows}</div></div>
              <div className="rounded-md border p-2"><div className="text-[11px] text-muted-foreground">Mean D</div><div className="font-mono font-semibold">{res.meanDLpm.toFixed(3)}</div></div>
              <div className="rounded-md border p-2"><div className="text-[11px] text-muted-foreground">SD of D</div><div className="font-mono font-semibold">{res.sdDLpm.toFixed(3)}</div></div>
            </div>
            <p className="text-xs text-muted-foreground">Mean flow {res.meanFlowLpm.toFixed(2)} L/min · learned x+ = max({res.floorLpm}, mean + 3 SD) = <b>{res.xPlusLpm}</b> · x− = max({res.floorLpm}, 3 SD − mean) = <b>{res.xMinusLpm}</b> L/min</p>
            {res.warnings.map((w) => <p key={w} className="rounded-md border border-amber-300 bg-amber-50 p-1.5 text-xs text-amber-900">{w}</p>)}
          </div>)}
        {done && edit && (
          <div className="flex flex-wrap items-center gap-3 text-sm" data-testid="learn-edit">
            <label className="flex items-center gap-1.5">x+ <Input className="h-8 w-24" type="number" step="0.05" value={edit.xp} onChange={(e) => { setTouched(true); setEdit({ ...edit, xp: e.target.value }); }} aria-label="x+ to use" /></label>
            <label className="flex items-center gap-1.5">x− <Input className="h-8 w-24" type="number" step="0.05" value={edit.xm} onChange={(e) => { setTouched(true); setEdit({ ...edit, xm: e.target.value }); }} aria-label="x- to use" /></label>
            <span className="text-xs text-muted-foreground">L/min · edit if you want, then accept</span>
          </div>)}
        <DialogFooter className="gap-2">
          {!running && !done && <Button disabled={busy} onClick={() => act("start")} data-testid="learn-start">Start test</Button>}
          {running && <><Button variant="outline" disabled={busy} onClick={() => act("cancel")}>Cancel</Button><Button disabled={busy} onClick={() => act("stop")} data-testid="learn-stop">Stop and compute</Button></>}
          {done && <><Button variant="outline" disabled={busy} onClick={() => act("start")}>Run again</Button><Button variant="outline" disabled={busy} onClick={onClose}>Discard</Button>
            <Button disabled={busy || !edit || !(Number(edit.xp) > 0) || !(Number(edit.xm) > 0) || !s?.result?.usable && !touched} onClick={accept} data-testid="learn-accept">Accept</Button></>}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
