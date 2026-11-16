// Texts and helpers that explain the leak states, rules, settings and demo mode in plain words.
import { useState } from "react";
import { FlaskConical } from "lucide-react";
import { toast } from "sonner";
import { Button } from "./ui/button";
import { Pill } from "./common";
import { fmtClock } from "../lib/usePolling";
import { v2, type LiveZone, type ZoneSettings } from "../lib/v2";

const RULE_NAMES: Record<string, string> = { balance: "Balance (in vs out)", night_flow: "Night flow", continuous_flow: "Continuous flow" };

/** 45 -> "45 min", 360 -> "6 h". */
export function minText(m: number): string {
  if (m < 120) return `${Math.round(m)} min`;
  const h = m / 60;
  return `${Math.abs(h - Math.round(h)) < 0.05 ? Math.round(h) : h.toFixed(1)} h`;
}

/** The values that are really in force for a zone, in plain words (local time of the site). */
export function settingsSentence(z: LiveZone): string {
  const s = z.settings;
  if (!s) return "";
  const thr = `max(${s.thresholdLpm} L/min, ${s.tolerancePct} %)`;
  return z.class === "strong" && s.xPlusLpm !== undefined
    ? `Balance D = Q in − Σ Q out: suspicious above +${s.xPlusLpm} L/min (or ${s.tolerancePct} % of the flow) · measurement inconsistency below −${s.xMinusLpm} · possible leak after ${s.tLeakS} s · confirmed after ${minText(s.confirmMin)} · resolved after ${minText(s.resolveMin)} normal · window ${s.windowS} s`
    : z.class === "strong"
    ? `Threshold ${thr} · suspected after ${minText(s.suspectMin)} · confirmed after ${minText(s.confirmMin)} · resolved after ${minText(s.resolveMin)} normal · bucket ${s.bucketS} s`
    : `Threshold ${thr} · night flow ${s.nightStart}-${s.nightEnd} (${s.tz}) · continuous flow longer than ${minText(s.continuousSuspectMin)} (confirmed at ${minText(s.continuousConfirmMin)}) · bucket ${s.bucketS} s`;
}

/** Why a zone with outlets does not alarm on a short imbalance, with the configured values. */
export function WeakWhy({ z }: { z: LiveZone }) {
  if (z.class !== "weak") return null;
  const s = z.settings;
  return (
    <p className="rounded-md border border-blue-200 bg-blue-50 p-2 text-xs text-blue-900" data-testid="weak-why">
      <b>The balance cannot prove a leak in this zone:</b> part of the water leaves through outlets that no node measures, so "in is larger than out" is normal use here. Leaks are found with the <b>duration</b> and <b>night-flow</b> rules instead: <b>night flow</b>{s ? ` (${s.nightStart}-${s.nightEnd})` : ""} and <b>continuous flow</b>{s ? ` longer than ${minText(s.continuousSuspectMin)}` : ""}.
    </p>
  );
}

/** One line per rule: how far it is from raising a suspicion, with a small bar when the progress is a number of minutes. */
export function RuleProgress({ z, compact = false }: { z: LiveZone; compact?: boolean }) {
  const rules = z.rules;
  if (rules.length === 0) return null;
  return (
    <ul className="space-y-1 text-xs" data-testid="rule-progress">
      {rules.map((r) => {
        const p = r.progress ?? { text: r.level ? r.reason : "ok", value: null, needed: null, unit: "min", counting: false };       // older backends send no progress
        const pct = p.unit === "min" && p.needed ? Math.min(100, Math.round(((p.value ?? 0) / p.needed) * 100)) : null;
        return (
          <li key={r.rule} data-testid={`rule-${r.rule}`} data-counting={p.counting ? "1" : "0"} className={compact ? "" : "rounded-md border p-1.5"}>
            <div className="flex items-start gap-1.5">
              <span className={`mt-1 inline-block h-2 w-2 shrink-0 rounded-full ${r.level > 1 ? "bg-red-600" : r.level === 1 || p.counting ? "bg-amber-500" : "bg-emerald-500"}`} />
              <span className="min-w-0"><b>{RULE_NAMES[r.rule] ?? r.rule}:</b> {p.text}</span>
            </div>
            {pct !== null && p.counting && <div className="ml-3.5 mt-1 h-1 overflow-hidden rounded bg-muted"><div className="h-full bg-amber-500" style={{ width: `${pct}%` }} /></div>}
          </li>
        );
      })}
    </ul>
  );
}

/** Admin toggle: suspect 1 min / confirm 3 min for one pipeline, switches itself off after an hour. Alerts raised meanwhile say "demo". */
export function DemoMode({ pipelineId, demo, demoUntil, admin, onChange }: { pipelineId: number; demo: boolean; demoUntil: number | null; admin: boolean; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const toggle = async () => {
    setBusy(true);
    try { await v2.setDemo(pipelineId, !demo); toast.success(demo ? "Demo mode off" : "Demo mode on for 1 hour"); onChange(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Could not change demo mode"); }
    finally { setBusy(false); }
  };
  return (
    <div className={`flex flex-wrap items-center gap-2 rounded-lg border p-3 text-sm ${demo ? "border-violet-300 bg-violet-50" : "bg-card"}`} data-testid="demo-mode" data-on={demo ? "1" : "0"}>
      <FlaskConical className="h-4 w-4 text-violet-700" />
      <div className="min-w-0 flex-1">
        <b>Demo mode</b> {demo ? <Pill tone="violet">ON{demoUntil ? ` until ${fmtClock(demoUntil)}` : ""}</Pill> : <Pill tone="gray">off</Pill>}
        <div className="text-xs text-muted-foreground">For a short water test only: leaks are suspected after 1 min and confirmed after 3 min (instead of the normal values). Switches itself off after 1 hour. Alerts raised meanwhile are tagged "demo".</div>
      </div>
      {admin && <Button size="sm" variant={demo ? "default" : "outline"} disabled={busy} onClick={toggle} data-testid="demo-toggle">{demo ? "Turn demo mode off" : "Turn demo mode on (1 h)"}</Button>}
    </div>
  );
}

export type { ZoneSettings };
