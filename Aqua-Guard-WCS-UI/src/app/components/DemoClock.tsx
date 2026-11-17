// Demo clock (admin, demo only): banner on every page while a simulated day/night is on, and the Auto / Day / Night switch.
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { Clock, Moon, Sun } from "lucide-react";
import { toast } from "sonner";
import { Button } from "./ui/button";
import { Pill } from "./common";
import { fmtClock, usePolling } from "../lib/usePolling";
import { v2, type ClockMode, type DemoClock } from "../lib/v2";

/** "Demo clock: NIGHT (simulated) - ends at 14:05". Shown on every page while the clock is forced, for every user. */
export function ClockBanner() {
  const c = usePolling(() => v2.demoClock(), 10000);
  const d = c.data;
  useEffect(() => { const f = () => c.refresh(); window.addEventListener("demo-clock-changed", f); return () => window.removeEventListener("demo-clock-changed", f); }, [c.refresh]);       // the switch tells the banner at once
  if (!d?.forced) return null;
  return (
    <div className="border-b border-violet-300 bg-violet-100 px-4 py-1.5 text-xs text-violet-900" data-testid="clock-banner" data-mode={d.mode} role="status">
      <b>Demo clock: {d.mode.toUpperCase()} (simulated)</b>{d.until ? ` - switches itself off at ${fmtClock(d.until)}` : ""}. Leak rules read the time as {d.mode}; alerts raised meanwhile are tagged "demo". Real timestamps and readings are not changed. <Link className="underline font-medium" to="/leak-detection">Change</Link>
    </div>
  );
}

const MODES: Array<{ mode: ClockMode; label: string; icon: typeof Sun }> = [{ mode: "auto", label: "Auto (real time)", icon: Clock }, { mode: "day", label: "Force DAY", icon: Sun }, { mode: "night", label: "Force NIGHT", icon: Moon }];

/** Admin switch.  Users only see the state. */
export function DemoClockSwitch({ admin, onChange }: { admin: boolean; onChange?: () => void }) {
  const c = usePolling(() => v2.demoClock(), 5000);
  const [busy, setBusy] = useState(false);
  const d: DemoClock | null = c.data;
  const mode: ClockMode = d?.forced ? d.mode : "auto";
  const set = async (m: ClockMode) => {
    setBusy(true);
    try { await v2.setDemoClock(m); toast.success(m === "auto" ? "Demo clock off: real time" : `Demo clock: ${m.toUpperCase()} for 1 hour`); c.refresh(); window.dispatchEvent(new Event("demo-clock-changed")); onChange?.(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Could not change the demo clock"); }
    finally { setBusy(false); }
  };
  return (
    <div className={`flex flex-wrap items-center gap-2 rounded-lg border p-3 text-sm ${d?.forced ? "border-violet-300 bg-violet-50" : "bg-card"}`} data-testid="demo-clock" data-mode={mode}>
      <Clock className="h-4 w-4 text-violet-700" />
      <div className="min-w-0 flex-1">
        <b>Demo clock</b> {d?.forced ? <Pill tone="violet">{mode.toUpperCase()}{d.until ? ` until ${fmtClock(d.until)}` : ""}</Pill> : <Pill tone="gray">auto</Pill>}
        <div className="text-xs text-muted-foreground">For a demonstration only: make the night-flow and quiet-hours rules think it is day or night, so they can be shown at noon. Switches itself off after 1 hour. It never changes timestamps, readings or the gateway.</div>
      </div>
      {admin && (
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Demo clock">
          {MODES.map(({ mode: m, label, icon: Icon }) => (
            <Button key={m} size="sm" variant={mode === m ? "default" : "outline"} disabled={busy || mode === m} onClick={() => set(m)} data-testid={`clock-${m}`} aria-pressed={mode === m}><Icon className="mr-1 h-4 w-4" />{label}</Button>))}
        </div>)}
    </div>
  );
}
