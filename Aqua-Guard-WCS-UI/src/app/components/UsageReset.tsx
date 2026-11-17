// Demo "Reset usage": admin button + dialog, and the small note shown on the pages that are affected.
import { useState } from "react";
import { RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "./ui/dialog";
import { fmtClock, usePolling } from "../lib/usePolling";
import { useAuth } from "../lib/auth";
import { v2 } from "../lib/v2";

const CHANGED = "usage-reset-changed";

function useUsageReset() {
  const s = usePolling(() => v2.usageReset(), 5000);
  const { refresh } = s;
  const onChanged = () => { refresh(); window.dispatchEvent(new Event(CHANGED)); };
  return { state: s.data, refresh, onChanged };
}

/** "usage reset at 14:05 (demo)": for the pages that keep showing the full real data (Analytics, History). */
export function UsageResetNote({ full = false }: { full?: boolean }) {
  const { state } = useUsageReset();
  if (!state?.active) return null;
  return (
    <p className="text-xs text-violet-800" data-testid="usage-reset-note">
      Usage reset at {fmtClock(state.since)} (demo){full ? ": this page still shows the full real data. Only the Overview and Water Monitoring totals count from the reset." : "."}
    </p>
  );
}

/** Admin-only.  Shows the note for everybody else (nothing for users when no reset is active). */
export function UsageResetControl() {
  const { user } = useAuth();
  const { state, onChanged } = useUsageReset();
  const [open, setOpen] = useState(false);
  const [clear, setClear] = useState(false);
  const [busy, setBusy] = useState(false);
  const admin = user?.role === "admin";
  const run = async (f: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try { await f(); toast.success(ok); setOpen(false); setClear(false); onChanged(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Could not change the usage reset"); }
    finally { setBusy(false); }
  };
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="usage-reset">
      {state?.active && <span className="text-xs text-violet-800" data-testid="usage-reset-note">Usage reset at {fmtClock(state.since)} (demo){state.until ? `, switches itself off at ${fmtClock(state.until)}` : ""}. Analytics and History keep the full data.</span>}
      {admin && (state?.active ? <>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => run(() => v2.undoUsageReset(), "Real totals are shown again")} data-testid="usage-undo">Undo reset</Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => run(() => v2.extendUsageReset(), "Extended by 24 hours")} data-testid="usage-extend">Extend 24 h</Button></>
        : <Button size="sm" variant="outline" onClick={() => setOpen(true)} data-testid="usage-reset-btn"><RotateCcw className="mr-1 h-3.5 w-3.5" />Reset usage (demo)</Button>)}
      {admin && open && (
        <Dialog open onOpenChange={(o) => { if (!o && !busy) setOpen(false); }}>
          <DialogContent data-testid="usage-reset-dialog">
            <DialogHeader><DialogTitle>Reset usage (demo)?</DialogTitle>
              <DialogDescription>“Used today”, “Used in 7 days”, the per-node Today / 7 days and the running times start from 0 now and count up. Nothing is deleted: samples, history, alerts and leak events stay, and Analytics and History keep the full data. It switches itself off after 24 hours; you can undo it at any time.</DialogDescription></DialogHeader>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={clear} onChange={(e) => setClear(e.target.checked)} data-testid="usage-reset-clear" />also clear running-time timers and active usage alerts (resolved as “Test”)</label>
            <DialogFooter><Button variant="outline" disabled={busy} onClick={() => setOpen(false)}>Cancel</Button>
              <Button disabled={busy} onClick={() => run(() => v2.usageResetNow(clear), "Usage reset: counting from now")} data-testid="usage-reset-confirm">Reset usage</Button></DialogFooter>
          </DialogContent>
        </Dialog>)}
    </div>
  );
}
