// Per-node "Usage settings" dialog: preset, limits, quiet hours, schedule, reset and the water-test demo.
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { FlaskConical } from "lucide-react";
import { Button } from "../ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { Loading, Pill } from "../common";
import { fmtClock, fmtTime, usePolling } from "../../lib/usePolling";
import { v2, type UsageProfile } from "../../lib/v2";
import { UsageChip, UsageEditor, UsageLine } from "./UsageParts";

/** Node "..." menu -> "Usage settings": preset, limits, quiet hours, allowed schedule, reset to default and the short-limit demo for a water test. */
export function NodeUsageDialog({ node, onClose, onDone }: { node: { id: number; number: number; name: string } | null; onClose: () => void; onDone: () => void }) {
  const ref = node?.id ?? 0;
  const q = usePolling(() => (node ? v2.nodeUsage(ref) : Promise.resolve(null)), 3000, [ref]);
  const [draft, setDraft] = useState<UsageProfile | null>(null);
  const [busy, setBusy] = useState(false);
  const d = q.data;
  useEffect(() => { setDraft(null); }, [ref]);
  useEffect(() => { if (d && !draft) setDraft(d.profile); }, [d, draft]);
  if (!node) return null;
  const run = async (fn: () => Promise<unknown>, ok: string, reload = true) => {
    setBusy(true);
    try { await fn(); toast.success(ok); if (reload) setDraft(null); q.refresh(); onDone(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); } finally { setBusy(false); }
  };
  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto" data-testid="usage-dialog">
        <DialogHeader><DialogTitle>Usage settings · Node {node.number}{node.name ? ` (${node.name})` : ""}</DialogTitle>
          <DialogDescription>How long water may run on this node before it counts as abnormal. A node without its own settings uses the default profile.</DialogDescription></DialogHeader>
        {!d || !draft ? <Loading /> : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              {d.live && <><UsageChip u={d.live} /><UsageLine u={d.live} /></>}
              {d.custom ? <Pill tone="blue">own settings</Pill> : <Pill tone="gray">uses the default profile</Pill>}
              {d.updatedBy && <span className="text-xs text-muted-foreground">last changed by {d.updatedBy}, {fmtTime(d.updatedAt)}</span>}
            </div>
            <UsageEditor value={draft} onChange={setDraft} presets={d.presets} disabled={busy} withPreset />
            <div className="rounded-lg border border-violet-300 bg-violet-50 p-3 text-sm" data-testid="usage-demo">
              <div className="mb-1 flex items-center gap-2 font-medium"><FlaskConical className="h-4 w-4 text-violet-700" />Demo for a water test {d.demo ? <Pill tone="violet">ON until {fmtClock(d.demo.until)}{d.demo.quiet ? " · quiet hours = now" : ""}</Pill> : <Pill tone="gray">off</Pill>}</div>
              <p className="mb-2 text-xs text-violet-950">Uses short limits (prolonged after 30 s, abnormal after 60 s) so you can test in minutes. Switches itself off after 1 hour. Alerts raised meanwhile are tagged "demo".</p>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="outline" disabled={busy} onClick={() => run(() => v2.setUsageDemo(ref, true, false), "Demo limits on for 1 hour", false)} data-testid="demo-limits">Demo limits (1 h)</Button>
                <Button size="sm" variant="outline" disabled={busy} onClick={() => run(() => v2.setUsageDemo(ref, true, true), "Demo: quiet hours = now for 1 hour", false)} data-testid="demo-night">Demo night flow (quiet hours = now)</Button>
                {d.demo && <Button size="sm" disabled={busy} onClick={() => run(() => v2.setUsageDemo(ref, false), "Demo off", false)}>Stop demo</Button>}
              </div>
            </div>
          </div>)}
        <DialogFooter className="gap-2">
          <Button variant="outline" disabled={busy || !d?.custom} onClick={() => run(() => v2.resetNodeUsage(ref), "Back to the default profile")} data-testid="usage-reset">Reset to default</Button>
          <Button variant="outline" disabled={busy} onClick={onClose}>Close</Button>
          <Button disabled={busy || !draft} onClick={() => draft && run(() => v2.setNodeUsage(ref, draft), "Usage settings saved")} data-testid="usage-save">Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
