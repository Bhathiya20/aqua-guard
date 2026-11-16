// Pipelines list and live view.
import { useRef, useState } from "react";
import { Link, Navigate, useNavigate, useParams, useSearchParams } from "react-router";
import { toast } from "sonner";
import { Copy, GitBranch, Pencil, Plus, Sparkles, Trash2, Upload } from "lucide-react";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { Input } from "../ui/input";
import { Label } from "../ui/label";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { AlertDialog, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "../ui/alert-dialog";
import { ConnectionBanner, EmptyState, Loading, PageHeader, Pill } from "../common";
import { useAuth } from "../../lib/auth";
import { fmtTime, usePolling } from "../../lib/usePolling";
import { v2, type PipelineDoc, type PipelineListItem } from "../../lib/v2";
import { PipelineEditor } from "./PipelineEditor";
import { PresetDialog } from "./PresetDialog";

export function PipelinesPage() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const nav = useNavigate();
  const list = usePolling(() => v2.pipelines(), 5000);
  const [newOpen, setNewOpen] = useState(false);
  const [presetOpen, setPresetOpen] = useState(false);
  const [name, setName] = useState("My house");
  const [del, setDel] = useState<PipelineListItem | null>(null);
  const [ren, setRen] = useState<PipelineListItem | null>(null);
  const [renName, setRenName] = useState("");
  const [busy, setBusy] = useState(false);
  const file = useRef<HTMLInputElement>(null);

  const create = async (doc?: PipelineDoc) => {
    setBusy(true);
    try {
      const r = await v2.createPipeline(doc ? { doc, name: doc.name } : { name: name.trim() || "New network" });
      if (doc) toast.success(r.valid ? "Imported and activated." : "Imported as a draft: it has errors. Open it to fix them.");
      setNewOpen(false);
      nav(`/pipelines/${r.id}`);
    } catch (e) { toast.error(e instanceof Error ? e.message : "Could not create the pipeline"); } finally { setBusy(false); }
  };
  const importFile = async (f?: File) => {
    if (!f) return;
    try {
      const j = JSON.parse(await f.text());
      if (!j || j.schema !== 1 || !Array.isArray(j.parts) || !Array.isArray(j.links)) throw new Error("This file is not an Aqua Guard pipeline (schema 1).");
      await create(j as PipelineDoc);
    } catch (e) { toast.error(e instanceof Error ? e.message : "Import failed"); }
  };
  const remove = async () => {
    if (!del) return;
    setBusy(true);
    try { await v2.deletePipeline(del.id); toast.success(`Deleted “${del.name}”`); setDel(null); list.refresh(); } catch (e) { toast.error(e instanceof Error ? e.message : "Delete failed"); } finally { setBusy(false); }
  };

  const items = list.data?.pipelines ?? [];
  const sameName = (n: string, except?: number) => items.some((p) => p.id !== except && p.name.trim().toLowerCase() === n.trim().toLowerCase());
  const dupNames = new Set(items.filter((p) => sameName(p.name, p.id)).map((p) => p.name.trim().toLowerCase()));
  const rename = async () => {
    if (!ren || !renName.trim()) return;
    setBusy(true);
    try { await v2.renamePipeline(ren.id, renName.trim()); toast.success("Renamed"); setRen(null); list.refresh(); } catch (e) { toast.error(e instanceof Error ? e.message : "Rename failed"); } finally { setBusy(false); }
  };
  const activate = async (p: PipelineListItem) => {
    const current = items.find((x) => x.activeVersion && x.id !== p.id);
    if (!window.confirm(current ? `Activate “${p.name}”? This switches off “${current.name}” (its design is kept; you can activate it again any time). Only one pipeline analyses the real nodes at a time.` : `Activate “${p.name}”?`)) return;
    setBusy(true);
    try {
      const vs = await v2.versions(p.id);
      const ok = vs.versions.find((x) => x.valid);                           // newest valid version (the list is newest first)
      if (!ok) { toast.error("This pipeline has no valid version to activate. Open it and fix the errors first."); return; }
      await v2.activate(p.id, ok.version);
      toast.success(`“${p.name}” is now active${current ? `; “${current.name}” was switched off` : ""}`);
      list.refresh();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Activate failed"); } finally { setBusy(false); }
  };
  const duplicate = async (p: PipelineListItem) => {
    setBusy(true);
    try {
      const full = await v2.pipeline(p.id);
      let n = `${p.name} (copy)`, i = 2;
      while (sameName(n)) n = `${p.name} (copy ${i++})`;
      const r = await v2.createPipeline({ name: n, doc: { ...full.doc, name: n }, note: `copy of “${p.name}”`, activate: false });
      toast.success(`Created “${n}” (not active)`);
      nav(`/pipelines/${r.id}`);
    } catch (e) { toast.error(e instanceof Error ? e.message : "Duplicate failed"); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-5">
      <PageHeader icon={<GitBranch className="h-6 w-6 text-blue-600" />} title="Pipelines" subtitle="Draw the water network and place your nodes on it. Leak detection works zone by zone on the active pipeline."
        right={admin ? <div className="flex gap-2"><Button variant="outline" size="sm" onClick={() => file.current?.click()}><Upload className="mr-1.5 h-3.5 w-3.5" />Import JSON</Button>
          <input ref={file} type="file" accept="application/json,.json" className="hidden" data-testid="import-pipeline" onChange={(e) => { void importFile(e.target.files?.[0]); e.target.value = ""; }} />
          <Button variant="outline" size="sm" onClick={() => setPresetOpen(true)} data-testid="new-from-preset"><Sparkles className="mr-1.5 h-3.5 w-3.5" />New from preset</Button>
          <Button size="sm" onClick={() => setNewOpen(true)}><Plus className="mr-1.5 h-3.5 w-3.5" />New pipeline</Button></div> : <Pill tone="gray">read only</Pill>} />
      <ConnectionBanner error={list.error} stale={list.stale} onRetry={list.refresh} />
      {list.loading ? <Loading label="Loading pipelines…" /> : items.length === 0 ? (
        <Card><CardContent><EmptyState>No pipeline yet.{admin ? " Create one and draw your pipes, or import a JSON file." : " An administrator has to draw it."}</EmptyState></CardContent></Card>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {items.map((p) => (
            <Card key={p.id} data-testid={`pipeline-${p.id}`} className={p.activeVersion ? "border-emerald-400 ring-1 ring-emerald-200" : ""}>
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-2"><CardTitle className="text-base">{p.name}</CardTitle>
                  {p.activeVersion ? <span className="flex flex-wrap items-center gap-1"><Pill tone="green">● ACTIVE v{p.activeVersion}</Pill>{p.demo && <Pill tone="violet">demo mode</Pill>}</span> : <Pill tone="gray">not active</Pill>}</div>
                {dupNames.has(p.name.trim().toLowerCase()) && <p className="text-xs text-amber-700" data-testid="dup-name">Another pipeline has the same name: rename one so they can be told apart.</p>}
                <CardDescription>{p.versions} version{p.versions === 1 ? "" : "s"} · updated {fmtTime(p.updatedAt)}</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-wrap gap-2">
                <Button asChild size="sm"><Link to={`/pipelines/${p.id}`}>{admin ? "Open / edit" : "Open"}</Link></Button>
                {admin && !p.activeVersion && <Button size="sm" variant="outline" disabled={busy} data-testid={`activate-${p.id}`} onClick={() => activate(p)}>Activate</Button>}
                {admin && <Button size="sm" variant="outline" onClick={() => { setRen(p); setRenName(p.name); }}><Pencil className="mr-1 h-3.5 w-3.5" />Rename</Button>}
                {admin && <Button size="sm" variant="outline" disabled={busy} onClick={() => duplicate(p)}><Copy className="mr-1 h-3.5 w-3.5" />Duplicate</Button>}
                {admin && <Button size="sm" variant="outline" className="text-red-700" onClick={() => setDel(p)}><Trash2 className="mr-1 h-3.5 w-3.5" />Delete</Button>}
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <PresetDialog open={presetOpen} onClose={() => setPresetOpen(false)} existingNames={items.map((p) => p.name)} />

      <Dialog open={newOpen} onOpenChange={setNewOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader><DialogTitle>New pipeline</DialogTitle><DialogDescription>You can rename it later. It starts empty.</DialogDescription></DialogHeader>
          <div className="grid gap-1.5"><Label htmlFor="pname">Name</Label><Input id="pname" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
            {sameName(name) && <p className="text-xs text-amber-700" data-testid="new-dup-name">A pipeline with this name already exists. You can still create it, but the two will be hard to tell apart.</p>}</div>
          <DialogFooter><Button variant="outline" onClick={() => setNewOpen(false)}>Cancel</Button><Button disabled={busy} onClick={() => create()}>Create</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!ren} onOpenChange={(o) => { if (!o) setRen(null); }}>
        <DialogContent className="max-w-sm">
          <DialogHeader><DialogTitle>Rename pipeline</DialogTitle><DialogDescription>Only the name changes: no new version is created.</DialogDescription></DialogHeader>
          <div className="grid gap-1.5"><Label htmlFor="rname">Name</Label><Input id="rname" value={renName} maxLength={80} onChange={(e) => setRenName(e.target.value)} />
            {ren && sameName(renName, ren.id) && <p className="text-xs text-amber-700">Another pipeline already has this name.</p>}</div>
          <DialogFooter><Button variant="outline" onClick={() => setRen(null)}>Cancel</Button><Button disabled={busy || !renName.trim() || renName.trim() === ren?.name} onClick={rename}>Rename</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={!!del} onOpenChange={(o) => { if (!o) setDel(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>Delete “{del?.name}”?</AlertDialogTitle>
            <AlertDialogDescription>The pipeline disappears from the list and leak detection for it stops. Its leak history stays in the database.</AlertDialogDescription></AlertDialogHeader>
          <AlertDialogFooter><Button variant="outline" onClick={() => setDel(null)}>Cancel</Button><Button variant="destructive" disabled={busy} onClick={remove}>Delete pipeline</Button></AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

export function PipelineRoute() {
  const { id } = useParams();
  const [q] = useSearchParams();
  const n = Number(id);
  if (!Number.isInteger(n) || n < 1) return <Navigate to="/pipelines" replace />;
  return <PipelineEditor pipelineId={n} initialMode={q.get("mode") === "live" ? "live" : "design"} initialZone={q.get("zone")} />;
}
