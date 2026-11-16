// Small shared UI pieces: page header, pills/status tones, loading and empty states, connection banner.
import type { ReactNode } from "react";
import { Link } from "react-router";
import { AlertTriangle, Loader2, WifiOff } from "lucide-react";

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-10 justify-center text-sm text-muted-foreground" role="status">
      <Loader2 className="h-4 w-4 animate-spin" /> {label}
    </div>
  );
}

/** Shown when polling fails. If older data exists it is kept on screen and marked stale. */
export function ConnectionBanner({ error, stale, onRetry }: { error: string; stale?: boolean; onRetry?: () => void }) {
  if (!error) return null;
  return (
    <div role="alert" className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
      <WifiOff className="h-4 w-4 shrink-0" />
      <span className="flex-1 min-w-0">
        {stale ? "Connection lost — showing the last data received. Retrying automatically. " : "Cannot load data. Retrying automatically. "}
        <span className="opacity-70">({error})</span>
      </span>
      {onRetry && <button onClick={onRetry} className="text-xs font-semibold underline">Retry now</button>}
    </div>
  );
}

const TONES: Record<string, string> = {
  green: "bg-emerald-100 text-emerald-800 border-emerald-200",
  red: "bg-red-100 text-red-800 border-red-200",
  amber: "bg-amber-100 text-amber-800 border-amber-200",
  blue: "bg-blue-100 text-blue-800 border-blue-200",
  gray: "bg-slate-100 text-slate-700 border-slate-200",
  violet: "bg-violet-100 text-violet-800 border-violet-200",
};

export function Pill({ tone, children }: { tone: keyof typeof TONES; children: ReactNode }) {
  return <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px] font-semibold ${TONES[tone]}`}>{children}</span>;
}

export function deviceTone(state: string): keyof typeof TONES {
  return state === "online" ? "green" : state === "offline" ? "red" : "gray";   // out_of_service is grey on purpose
}

export function stateText(state: string): string {
  return state === "out_of_service" ? "out of service" : state;
}

export function leakTone(state?: string): keyof typeof TONES {
  switch (state) {
    case "CONFIRMED": return "red";
    case "SUSPECTED": return "amber";
    case "SENSOR_FAULT": return "gray";
    default: return "green";
  }
}

export function PageHeader({ icon, title, subtitle, right }: { icon?: ReactNode; title: string; subtitle?: string; right?: ReactNode }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
      <div>
        <div className="flex items-center gap-2">{icon}<h1 className="text-2xl font-bold tracking-tight">{title}</h1></div>
        {subtitle && <p className="text-sm text-muted-foreground">{subtitle}</p>}
      </div>
      {right}
    </div>
  );
}

export function Stat({ label, value, unit, hint, tone }: { label: string; value: ReactNode; unit?: string; hint?: ReactNode; tone?: string }) {
  return (
    <div className="rounded-xl border bg-card p-4">
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <div className={`mt-1 text-2xl font-bold font-mono ${tone ?? ""}`}>
        {value}{unit && <span className="ml-1 text-xs font-normal text-muted-foreground">{unit}</span>}
      </div>
      {hint && <div className="mt-1 text-xs text-muted-foreground">{hint}</div>}
    </div>
  );
}

/** One style for "nothing here": empty list, no data yet, nothing configured. `action` adds the next-step button. */
export function EmptyState({ children, title, action }: { children?: ReactNode; title?: string; action?: { to: string; label: string } }) {
  return (
    <div className="py-8 text-center text-sm text-muted-foreground flex flex-col items-center gap-2" data-testid="empty-state">
      <AlertTriangle className="h-5 w-5 opacity-40" />
      {title && <p className="font-semibold text-foreground">{title}</p>}
      {children}
      {action && <Link to={action.to} className="mt-1 inline-flex items-center rounded-md border px-3 py-1.5 text-xs font-semibold text-foreground hover:bg-muted">{action.label}</Link>}
    </div>
  );
}

export const VALVE_STATES = ["CLOSED (idle)", "Rain debounce", "First flush", "OPEN (collecting)", "CLOSED (tank full)", "CLOSED (unsafe water)", "FAULT (fail-safe)", "Rain ending"];
export const valveStateName = (s: number | null | undefined) => (s === null || s === undefined ? "—" : VALVE_STATES[s] ?? `state ${s}`);
export const fmtNum = (v: number | null | undefined, d = 2) => (v === null || v === undefined ? "—" : v.toFixed(d));
