// Usage building blocks: status chip, running-time line, live table per node and the profile editor form.
import { Plus, Trash2 } from "lucide-react";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Pill } from "../common";
import { fmtDuration, usePolling } from "../../lib/usePolling";
import { USAGE_TEXT, v2, type UsageLive, type UsagePresetInfo, type UsageProfile } from "../../lib/v2";

export const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Status chip of one node's duration check (never green for an unknown state). */
export function UsageChip({ u }: { u: UsageLive }) {
  const t = USAGE_TEXT[u.status] ?? USAGE_TEXT.UNKNOWN;
  return <span data-testid={`usage-chip-${u.number}`} data-status={u.status}><Pill tone={t.tone}>{u.allowed && u.status === "NORMAL" ? "Allowed long use" : t.text}</Pill></span>;
}

/** Live T_flow with the next limit: "flowing 1 min 20 s · next limit T_max in 40 s". */
export function UsageLine({ u }: { u: UsageLive }) {
  const what = (w: string) => (w === "T_lim" ? "prolonged-usage limit" : w === "T_max" ? "abnormal-usage limit" : w);
  return (
    <span className="text-xs text-muted-foreground" data-testid={`usage-line-${u.number}`}>
      {u.status === "UNKNOWN" ? "no recent data" : u.status === "NO_FLOW" || u.tFlowS === null ? "not flowing"
        : <>T_flow <b className="font-mono text-foreground">{fmtDuration(u.tFlowS)}</b>{u.next ? ` · ${what(u.next.what)} in ${fmtDuration(u.next.inS)}` : ""}</>}
    </span>
  );
}

/** Per-node usage table for the Water Monitoring and Leak Detection pages. */
export function UsageLiveTable({ title = "Usage per node" }: { title?: string }) {
  const live = usePolling(() => v2.usageLive(), 2000);
  const rows = live.data?.nodes ?? [];
  if (rows.length === 0) return null;
  return (
    <section className="space-y-2" data-testid="usage-live">
      <h2 className="text-lg font-semibold">{title}</h2>
      <p className="text-xs text-muted-foreground">Each flow node is checked on its own: how long the water has been running, against that node's limits. This is independent of the leak balance.</p>
      <div className="overflow-x-auto rounded-lg border"><table className="w-full text-sm">
        <thead className="bg-muted/50 text-left text-xs text-muted-foreground"><tr><th className="p-2">Node</th><th className="p-2">Usage profile</th><th className="p-2 text-right">Flow now</th><th className="p-2">Status</th><th className="p-2">Running time</th></tr></thead>
        <tbody>{rows.map((u) => (
          <tr key={u.nodeRef} className="border-t" data-testid={`usage-row-${u.number}`}>
            <td className="p-2 whitespace-nowrap font-medium">{u.label}</td>
            <td className="p-2 text-xs" title={u.summary}>{u.demo ? <Pill tone="violet">demo limits</Pill> : u.presetLabel}{u.custom && !u.demo ? <span className="text-muted-foreground"> (edited)</span> : null}</td>
            <td className="p-2 text-right font-mono">{u.flowLpm === null ? "—" : `${u.flowLpm.toFixed(2)} L/min`}</td>
            <td className="p-2"><UsageChip u={u} /></td>
            <td className="p-2"><UsageLine u={u} /></td>
          </tr>))}</tbody></table></div>
    </section>
  );
}

const HM = /^([01]\d|2[0-3]):[0-5]\d$/;
export function UsageEditor({ value, onChange, presets, disabled, withPreset }: { value: UsageProfile; onChange: (p: UsageProfile) => void; presets: UsagePresetInfo[]; disabled?: boolean; withPreset: boolean }) {
  const set = (patch: Partial<UsageProfile>) => onChange({ ...value, ...patch });
  const limit = (k: "tLimS" | "tMaxS") => (e: { target: { value: string } }) => set({ [k]: Number(e.target.value), ...(withPreset && value.preset !== "custom" ? { preset: "custom" } : {}) });
  const pick = (key: string) => {
    const pr = presets.find((x) => x.key === key);
    onChange({ ...value, preset: key, ...(pr?.tLimS ? { tLimS: pr.tLimS, tMaxS: pr.tMaxS! } : {}) });
  };
  const num = (k: keyof UsageProfile, label: string, unit: string, step = "1") => (
    <label className="space-y-1 text-sm"><span className="block text-xs text-muted-foreground">{label}</span>
      <span className="flex items-center gap-1.5"><Input className="h-8 w-28" type="number" step={step} min="0" value={String(value[k])} disabled={disabled} aria-label={label}
        onChange={k === "tLimS" || k === "tMaxS" ? limit(k) : (e) => set({ [k]: Number(e.target.value) } as never)} /><span className="text-xs text-muted-foreground">{unit}</span></span></label>);
  const sch = value.schedule;
  const upd = (i: number, patch: Partial<UsageProfile["schedule"][number]>) => set({ schedule: sch.map((e, j) => (j === i ? { ...e, ...patch } : e)) });
  return (
    <div className="space-y-4" data-testid="usage-editor">
      {withPreset && (
        <label className="space-y-1 text-sm"><span className="block text-xs text-muted-foreground">Preset</span>
          <select className="h-9 rounded-md border bg-input-background px-2 text-sm" value={value.preset} disabled={disabled} aria-label="Preset" onChange={(e) => pick(e.target.value)}>
            {presets.map((p) => <option key={p.key} value={p.key}>{p.label}{p.tLimS ? ` (${fmtDuration(p.tLimS)} / ${fmtDuration(p.tMaxS)})` : ""}</option>)}</select></label>)}
      <div className="grid gap-3 sm:grid-cols-2">
        {num("tLimS", "T_lim: prolonged usage after", "s")}{num("tMaxS", "T_max: continuous abnormal usage after", "s")}
        {num("flowOnLpm", "Flow ON above", "L/min", "0.05")}{num("flowOffLpm", "Flow OFF below", "L/min", "0.05")}{num("noFlowS", "No-flow confirm time", "s")}
      </div>
      <p className="text-xs text-muted-foreground">T_lim = {fmtDuration(value.tLimS)}, T_max = {fmtDuration(value.tMaxS)}. A pause shorter than the no-flow confirm time does not restart the timer.</p>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="space-y-1 text-sm"><span className="block text-xs text-muted-foreground">Quiet hours from</span><Input className="h-8 w-28" type="time" value={value.quietStart} disabled={disabled} aria-label="Quiet hours start" onChange={(e) => HM.test(e.target.value) && set({ quietStart: e.target.value })} /></label>
        <label className="space-y-1 text-sm"><span className="block text-xs text-muted-foreground">to</span><Input className="h-8 w-28" type="time" value={value.quietEnd} disabled={disabled} aria-label="Quiet hours end" onChange={(e) => HM.test(e.target.value) && set({ quietEnd: e.target.value })} /></label>
        {num("quietConfirmS", "Night-flow confirm time", "s")}
      </div>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={value.allow24h} disabled={disabled} onChange={(e) => set({ allow24h: e.target.checked })} aria-label="24 h use allowed" />24 h use allowed (no night-flow alert for this node)</label>
      <div className="space-y-2" data-testid="usage-schedule">
        <div className="flex items-center justify-between"><span className="text-sm font-medium">Allowed long-use schedule (optional)</span>
          {!disabled && <Button size="sm" variant="outline" type="button" onClick={() => set({ schedule: [...sch, { days: [0, 1, 2, 3, 4], start: "08:00", end: "17:00" }] })}><Plus className="mr-1 h-3.5 w-3.5" />Add</Button>}</div>
        <p className="text-xs text-muted-foreground">While a schedule is active (site time, same day), long use gives no alert. Example: a laboratory on weekdays 08:00–17:00.</p>
        {sch.map((e, i) => (
          <div key={i} className="flex flex-wrap items-center gap-2 rounded-md border p-2" data-testid={`schedule-${i}`}>
            {DAYS.map((d, di) => (
              <label key={d} className="flex items-center gap-1 text-xs"><input type="checkbox" checked={e.days.includes(di)} disabled={disabled} aria-label={`${d} ${i + 1}`}
                onChange={(ev) => upd(i, { days: ev.target.checked ? [...e.days, di].sort() : e.days.filter((x) => x !== di) })} />{d}</label>))}
            <Input className="h-8 w-28" type="time" value={e.start} disabled={disabled} aria-label={`Start ${i + 1}`} onChange={(ev) => HM.test(ev.target.value) && upd(i, { start: ev.target.value })} />
            <span className="text-xs">to</span>
            <Input className="h-8 w-28" type="time" value={e.end} disabled={disabled} aria-label={`End ${i + 1}`} onChange={(ev) => HM.test(ev.target.value) && upd(i, { end: ev.target.value })} />
            {!disabled && <Button size="icon" variant="ghost" type="button" aria-label={`Remove schedule ${i + 1}`} onClick={() => set({ schedule: sch.filter((_, j) => j !== i) })}><Trash2 className="h-4 w-4" /></Button>}
          </div>))}
      </div>
    </div>
  );
}
