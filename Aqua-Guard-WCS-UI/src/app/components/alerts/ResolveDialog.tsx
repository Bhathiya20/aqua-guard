// Dialog that asks for the reason (and an optional note) when an alert is resolved by hand.
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "../ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { RESOLVE_REASONS, v2 } from "../../lib/v2";

/** Resolving by hand asks WHY: the answer is what the History tab shows ("Fixed leak", "False alarm", ...). */
export function ResolveDialog({ ids, label, onClose, onDone }: { ids: number[]; label: string; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    try {
      for (const id of ids) await v2.resolve(id, reason, note);
      toast.success(ids.length > 1 ? `Resolved ${ids.length} alerts` : "Resolved: it is now in the History tab");
      onDone(); onClose();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Could not resolve"); } finally { setBusy(false); }
  };
  return (
    <Dialog open onOpenChange={(o) => { if (!o && !busy) onClose(); }}>
      <DialogContent data-testid="resolve-dialog">
        <DialogHeader><DialogTitle>Resolve {label}</DialogTitle>
          <DialogDescription>Say why it can be closed. If the cause is still there, the system raises a new alert on its next check.</DialogDescription></DialogHeader>
        <fieldset className="space-y-1.5" data-testid="resolve-reasons">
          <legend className="text-sm font-medium">Reason</legend>
          {Object.entries(RESOLVE_REASONS).map(([k, t]) => (
            <label key={k} className="flex items-center gap-2 text-sm"><input type="radio" name="reason" value={k} checked={reason === k} onChange={() => setReason(k)} />{t}</label>))}
        </fieldset>
        <label className="space-y-1 text-sm"><span className="font-medium">Note (optional)</span>
          <textarea className="w-full rounded-md border bg-input-background p-2 text-sm" rows={2} maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note" /></label>
        <DialogFooter><Button variant="outline" disabled={busy} onClick={onClose}>Cancel</Button><Button disabled={!reason || busy} onClick={go} data-testid="resolve-confirm">Resolve</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
