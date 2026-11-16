import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Label } from "../ui/label";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { Loading } from "../common";
import { v2, nodeTitle, type NodeV2, type PresetInfo } from "../../lib/v2";

/** "New from preset": pick a ready-made network, say which nodes fill its slots, get an INACTIVE pipeline (never activated by this dialog). */
export function PresetDialog({ open, onClose, existingNames }: { open: boolean; onClose: () => void; existingNames: string[] }) {
  const nav = useNavigate();
  const [presets, setPresets] = useState<PresetInfo[] | null>(null);
  const [nodes, setNodes] = useState<NodeV2[]>([]);
  const [sel, setSel] = useState<string>("");
  const [slots, setSlots] = useState<Array<number | "">>([]);
  const [count, setCount] = useState(2);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    v2.pipelinePresets().then((r) => { setPresets(r.presets); if (!sel && r.presets[0]) pick(r.presets[0]); }).catch(() => setPresets([]));
    v2.nodes().then((r) => setNodes(r.nodes.filter((n) => n.type === "flow" && n.registryState === "paired" && n.state !== "removed"))).catch(() => setNodes([]));
  }, [open]);   // eslint-disable-line react-hooks/exhaustive-deps

  const cur = presets?.find((p) => p.id === sel);
  const pick = (p: PresetInfo) => { setSel(p.id); setName(p.name); setCount(p.minNodes === p.maxNodes ? p.maxNodes : Math.min(2, p.maxNodes)); setSlots([]); };
  const n = cur ? (cur.minNodes === cur.maxNodes ? cur.maxNodes : count) : 0;
  const chosen = slots.slice(0, n).filter((x): x is number => x !== "" && x !== undefined);
  const dupSlot = new Set(chosen).size !== chosen.length;
  const dupName = existingNames.some((x) => x.trim().toLowerCase() === name.trim().toLowerCase());

  const create = async () => {
    if (!cur) return;
    setBusy(true);
    try {
      const r = await v2.createFromPreset({ preset: cur.id, nodeIds: chosen, name: name.trim() || cur.name });
      toast.success(`Created “${name.trim() || cur.name}” (not active)${r.missingNodes ? ` - ${r.missingNodes} node slot${r.missingNodes === 1 ? "" : "s"} still empty` : ""}`);
      onClose();
      nav(`/pipelines/${r.id}`);
    } catch (e) { toast.error(e instanceof Error ? e.message : "Could not create the pipeline"); } finally { setBusy(false); }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto" data-testid="preset-dialog">
        <DialogHeader><DialogTitle>New pipeline from a preset</DialogTitle><DialogDescription>A ready-made network for a test set-up. It is created <b>not active</b>: activate it from the list when it is built.</DialogDescription></DialogHeader>
        {presets === null ? <Loading label="Loading presets…" /> : (
          <div className="grid gap-4 md:grid-cols-[210px_1fr]">
            <div role="radiogroup" aria-label="Preset" className="space-y-1.5">
              {presets.map((p) => (
                <button key={p.id} type="button" role="radio" aria-checked={sel === p.id} onClick={() => pick(p)} data-testid={`preset-${p.id}`}
                  className={`w-full rounded-md border px-3 py-2 text-left text-sm ${sel === p.id ? "border-blue-600 bg-blue-50 font-semibold" : "hover:bg-muted"}`}>{p.name}</button>))}
            </div>
            {cur && (
              <div className="space-y-3 text-sm" data-testid="preset-details">
                <p>{cur.description}</p>
                <div><h4 className="font-semibold">How to set it up</h4><ol className="list-decimal pl-5 space-y-0.5 text-muted-foreground">{cur.physicalSetup.map((t) => <li key={t}>{t}</li>)}</ol></div>
                {cur.minNodes !== cur.maxNodes && (
                  <div className="flex items-center gap-2"><Label htmlFor="pcount">Number of nodes</Label>
                    <select id="pcount" className="rounded-md border bg-background px-2 py-1" value={count} onChange={(e) => setCount(Number(e.target.value))}>
                      {Array.from({ length: cur.maxNodes - cur.minNodes + 1 }, (_, i) => cur.minNodes + i).map((x) => <option key={x} value={x}>{x}</option>)}</select></div>)}
                <div className="space-y-1.5">
                  <h4 className="font-semibold">Nodes</h4>
                  {Array.from({ length: n }, (_, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <Label className="w-52 shrink-0 text-xs" htmlFor={`slot-${i}`}>{cur.slots[i] ?? `Node ${i + 1}`}</Label>
                      <select id={`slot-${i}`} className="min-w-0 flex-1 rounded-md border bg-background px-2 py-1" value={slots[i] ?? ""} onChange={(e) => { const v = [...slots]; v[i] = e.target.value === "" ? "" : Number(e.target.value); setSlots(v); }}>
                        <option value="">(leave empty for now)</option>
                        {nodes.map((x) => <option key={x.id} value={x.id}>{nodeTitle(x)}{x.state === "out_of_service" ? " - out of service" : ""}</option>)}
                      </select>
                    </div>))}
                  {dupSlot && <p className="text-xs text-red-700" role="alert">Each node can be used once.</p>}
                </div>
                <div className="grid gap-1.5"><Label htmlFor="prname">Name</Label><Input id="prname" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
                  {dupName && <p className="text-xs text-amber-700" data-testid="preset-dup-name">A pipeline with this name already exists; rename one so they can be told apart.</p>}</div>
              </div>)}
          </div>)}
        <DialogFooter><Button variant="outline" onClick={onClose}>Cancel</Button><Button disabled={busy || !cur || dupSlot} onClick={create} data-testid="preset-create">Create pipeline</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
