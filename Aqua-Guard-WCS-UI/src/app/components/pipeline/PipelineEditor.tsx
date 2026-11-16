// Pipeline editor: draw the network, place nodes, validate and save a new version.
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { Background, Controls, ReactFlow, ReactFlowProvider, useReactFlow, useUpdateNodeInternals, type EdgeChange, type NodeChange } from "@xyflow/react";
import { toast } from "sonner";
import { ArrowLeft, MoreHorizontal, PanelLeft, PanelRight, Check, Download, Eye, History as HistoryIcon, Lock, Maximize2, Pencil, Redo2, Save, SlidersHorizontal, Undo2, Upload, Layers, Magnet as MagnetIcon, ZoomIn, ZoomOut } from "lucide-react";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Label } from "../ui/label";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "../ui/dropdown-menu";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "../ui/sheet";
import { ConnectionBanner, Loading, Pill } from "../common";
import { useAuth } from "../../lib/auth";
import { fmtTime, usePolling } from "../../lib/usePolling";
import { v2, type AvailableNode, type DocPart, type LiveZone, type NodeV2, type PipelineDoc, type PipelineFull, type ValidationSummary, type VersionItem } from "../../lib/v2";
import {
  SNAP_DIST, addMeter, addPart, applySnap, attachMeterAt, canRedo, canUndo, connect, detachPulledLinks, docToFlow, emptyDoc, findSnap, movePart, newHistory, partById, partLabels, partSeverities, patchPart, pushHistoryMerged, redo, removeLink,
  placeNodeOnPipe, removePart, rotatePart, undo, zoneOfParts, zoneTint, type History,
} from "./docModel";
import { CanvasCtx, nodeTypes, type CanvasCtxValue } from "./PartNode";
import { Legend_, LiveSummary, Palette, Properties, ValidationBar, ZoneDetail, ZoneList, itemKey, type PaletteItem } from "./PipelinePanels";

function useWide() {
  const q = "(min-width: 1024px)";
  const [wide, setWide] = useState(() => (typeof window !== "undefined" && window.matchMedia ? window.matchMedia(q).matches : true));
  useEffect(() => {
    if (!window.matchMedia) return;
    const m = window.matchMedia(q), on = () => setWide(m.matches);
    on(); m.addEventListener?.("change", on);
    return () => m.removeEventListener?.("change", on);
  }, []);
  return wide;
}

const typing = (t: EventTarget | null) => t instanceof HTMLElement && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);

function downloadJson(doc: PipelineDoc, name: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 2)], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url; a.download = `${name.replace(/[^\w.-]+/g, "_") || "pipeline"}.json`; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

interface Loaded { full: PipelineFull; versions: VersionItem[]; latest: number }

function Editor({ pipelineId, initialMode = "design", initialZone = null }: { pipelineId: number; initialMode?: "design" | "live"; initialZone?: string | null }) {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const wide = useWide();
  const rf = useReactFlow();
  const updateInternals = useUpdateNodeInternals();
  const fileRef = useRef<HTMLInputElement>(null);

  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [loadErr, setLoadErr] = useState("");
  const [hist, setHist] = useState<History>(() => newHistory(emptyDoc()));
  const [draft, setDraft] = useState<PipelineDoc | null>(null);          // while dragging
  const doc = draft ?? hist.present;
  const docRef = useRef(doc); docRef.current = doc;
  const [name, setName] = useState("");
  const [savedJson, setSavedJson] = useState("");
  const [savedVersion, setSavedVersion] = useState<{ version: number; valid: boolean } | null>(null);
  const [mode, setMode] = useState<"design" | "live">(initialMode);
  const [sel, setSel] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<string | null>(null);
  const [pending, setPending] = useState<PaletteItem | null>(null);
  const [partsOpen, setPartsOpen] = useState(false);
  const [propsOpen, setPropsOpen] = useState(false);
  // collapsible side panels (wide screens); remembered per browser
  const [leftOpen, setLeftOpen] = useState<boolean>(() => { try { return localStorage.getItem("aqua.panelL") !== "closed"; } catch { return true; } });
  const [rightOpen, setRightOpen] = useState<boolean>(() => { try { return localStorage.getItem("aqua.panelR") !== "closed"; } catch { return true; } });
  const togglePanel = (side: "L" | "R") => {
    const next = side === "L" ? !leftOpen : !rightOpen;
    (side === "L" ? setLeftOpen : setRightOpen)(next);
    try { localStorage.setItem(`aqua.panel${side}`, next ? "open" : "closed"); } catch { /* storage unavailable */ }
  };
  const [renameId, setRenameId] = useState<string | null>(null);
  const [renameText, setRenameText] = useState("");
  const [saveOpen, setSaveOpen] = useState(false);
  const [histOpen, setHistOpen] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [zoneSel, setZoneSel] = useState<string | null>(initialZone);
  const [liveDoc, setLiveDoc] = useState<PipelineDoc | null>(null);
  const [magnet, setMagnet] = useState<boolean>(() => { try { return localStorage.getItem("aqua.magnet") !== "off"; } catch { return true; } });
  const [snapHint, setSnapHint] = useState<CanvasCtxValue["snapHint"]>(null);
  const altRef = useRef(false);                                   // Alt held = magnet temporarily off
  const dragStart = useRef<PipelineDoc | null>(null);              // the document when the current drag began
  const lastRemove = useRef(0);
  const toggleMagnet = () => setMagnet((m) => { const n = !m; try { localStorage.setItem("aqua.magnet", n ? "on" : "off"); } catch { /* private mode */ } return n; });

  const editable = isAdmin && mode === "design";
  const nodesApi = usePolling(() => v2.nodes(), 4000);
  const nodeInfo = useMemo(() => new Map<number, NodeV2>((nodesApi.data?.nodes ?? []).map((n) => [n.number, n])), [nodesApi.data]);

  // ---- load the newest saved version (a draft newer than the active one is what the user last worked on)
  const load = useCallback(async (version?: number) => {
    setLoadErr("");
    try {
      const vs = await v2.versions(pipelineId);
      const latest = vs.versions[0]?.version;
      const full = await v2.pipeline(pipelineId, version ?? latest);
      setLoaded({ full, versions: vs.versions, latest: latest ?? full.version });
      setHist(newHistory(full.doc));
      setDraft(null); setName(full.name); setSavedJson(JSON.stringify(full.doc));
      setSavedVersion({ version: full.version, valid: full.savedValid });
      setSel(null);
    } catch (e) { setLoadErr(e instanceof Error ? e.message : "Could not load the pipeline"); }
  }, [pipelineId]);
  useEffect(() => { void load(); }, [load]);
  // Fit the view once when a saved design has been loaded. (A permanent `fitView` prop re-centred the canvas whenever the first part appeared, which pushed later drops out of sight.)
  useEffect(() => {
    if (!loaded || loaded.full.doc.parts.length === 0) return;
    const t = setTimeout(() => { void rf.fitView({ padding: 0.2, maxZoom: 1 }); }, 60);
    return () => clearTimeout(t);
  }, [loaded, rf]);
  useEffect(() => {
    const on = (e: KeyboardEvent) => { if (e.key === "Alt") altRef.current = e.type === "keydown"; };
    const off = () => { altRef.current = false; };
    window.addEventListener("keydown", on); window.addEventListener("keyup", on); window.addEventListener("blur", off);
    return () => { window.removeEventListener("keydown", on); window.removeEventListener("keyup", on); window.removeEventListener("blur", off); };
  }, []);

  const available: AvailableNode[] = loaded?.full.availableNodes ?? [];
  // nodes that are paired but the loaded version already uses are not in availableNodes; ones placed in the editor are filtered in the palette
  const fullAvailable = useMemo(() => {
    const seen = new Set(available.map((n) => n.number));
    const extra = (nodesApi.data?.nodes ?? []).filter((n) => n.type === "flow" && n.registryState === "paired" && !seen.has(n.number))
      .map((n): AvailableNode => ({ type: "flow", number: n.number, name: n.name, label: `Node ${n.number}${n.name ? ` - ${n.name}` : ""}`, nodeRef: n.id }));
    return [...available, ...extra];
  }, [available, nodesApi.data]);

  const dirty = JSON.stringify(hist.present) !== savedJson || (loaded ? name !== loaded.full.name : false);

  // ---- editing helpers
  // docRef is updated at once, so two commits in the same event (Delete = remove links + remove part) build on each other instead of on a stale document.
  const commit = useCallback((next: PipelineDoc, merge = false) => { docRef.current = next; setHist((h) => pushHistoryMerged(h, next, merge)); setDraft(null); setSnapHint(null); }, []);
  const magnetOn = (alt?: boolean) => magnet && !(alt ?? altRef.current);
  /** Snap a part to a compatible free port next to it (moves it so the ports touch and links them); says why when it refuses. */
  const snapPart = useCallback((d: PipelineDoc, id: string): PipelineDoc => {
    const r = findSnap(d, id, SNAP_DIST);
    if (r.hit) { toast.success("Connected"); return applySnap(d, r.hit); }
    if (r.refused) toast.error(r.refused);
    return d;
  }, []);
  const place = useCallback((it: PaletteItem, x: number, y: number, alt?: boolean) => {
    const d = docRef.current, mg = magnetOn(alt);
    if ("node" in it) { const r = addMeter(d, it.node, x - 14, y - 14, mg ? SNAP_DIST : 14); commit(r.doc); setSel(r.id); }
    else {
      const r = addPart(d, it.kind, x - 20, y - 10, it.outletType ? { outletType: it.outletType } : {});
      commit(mg && r.doc !== d ? snapPart(r.doc, r.id) : r.doc); setSel(r.id);
    }
    setPending(null);
  }, [commit, magnet, snapPart]);

  const onPick = (it: PaletteItem) => {
    if (!editable) return;
    setPending((p) => (p && itemKey(p) === itemKey(it) ? null : it));
    if (!wide) setPartsOpen(false);
  };

  // ---- canvas wiring
  const [validation, setValidation] = useState<ValidationSummary | null>(null);
  const [checking, setChecking] = useState(false);
  const [valErr, setValErr] = useState("");

  const status = usePolling(() => (mode === "live" && loaded?.full.activeVersion ? v2.pipelineStatus(pipelineId) : Promise.resolve(null)), 2000, [mode, pipelineId, loaded?.full.activeVersion]);
  useEffect(() => {
    if (mode !== "live" || !loaded?.full.activeVersion) return;
    let alive = true;
    v2.pipeline(pipelineId, loaded.full.activeVersion).then((f) => { if (alive) setLiveDoc(f.doc); }).catch(() => {});
    return () => { alive = false; };
  }, [mode, pipelineId, loaded?.full.activeVersion, loaded?.versions.length]);

  const shownDoc = mode === "live" ? (liveDoc ?? doc) : doc;
  const shown = useMemo(() => docToFlow(shownDoc), [shownDoc]);
  // Safari/WebKit sometimes draws a new link before the handles of a just-added part were measured: re-measure after every change of the link set.
  const linkKey = shown.edges.map((e) => e.id).join(",") + "|" + shown.nodes.map((n) => `${n.id}:${n.width}x${n.height}`).join(",");
  useEffect(() => {
    const ids = shown.nodes.map((n) => n.id);
    const r1 = requestAnimationFrame(() => updateInternals(ids));
    const t = setTimeout(() => updateInternals(ids), 120);
    return () => { cancelAnimationFrame(r1); clearTimeout(t); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [linkKey, updateInternals]);
  const zones = status.data?.zones ?? [];
  const zmap = useMemo(() => zoneOfParts(zones), [zones]);
  const selectedZone = zones.find((z) => z.id === zoneSel) ?? null;

  useEffect(() => {
    if (mode !== "design") return;
    setChecking(true);
    const t = setTimeout(async () => {
      try { setValidation(await v2.validate(doc)); setValErr(""); } catch (e) { setValErr(e instanceof Error ? e.message : "failed"); } finally { setChecking(false); }
    }, 350);
    return () => clearTimeout(t);
  }, [doc, mode]);

  const labels = useMemo(() => partLabels(shownDoc), [shownDoc]);
  const zoneTints = useMemo(() => Object.fromEntries(zones.map((z) => [z.id, zoneTint(zones, z.id)])), [zones]);
  const zoneTag = useMemo(() => {
    const m: Record<string, LiveZone> = {};
    for (const z of zones) { const anchor = z.parts.find((id) => zmap.parts[id]?.id === z.id && !m[id]); if (anchor) m[anchor] = z; }
    return mode === "live" ? m : {};
  }, [zones, zmap, mode]);
  const startRename = useCallback((id: string) => { setRenameId(id); setRenameText(partById(docRef.current, id)?.label ?? ""); }, []);
  const ctx: CanvasCtxValue = useMemo(() => ({
    labels, zoneTints, zoneTag, rename: startRename,
    mode, severities: mode === "design" ? partSeverities(validation?.messages ?? [], doc) : {}, zoneOfPart: zmap.parts, pipeSplit: zmap.pipeSplit,
    live: Object.fromEntries(Object.entries(status.data?.live ?? {}).map(([k, v]) => [k, { flowLpm: v.flowLpm, fresh: v.fresh }])), highlight, pendingPort: null, snapHint, readOnly: !editable,
  }), [mode, validation, doc, zmap, status.data, highlight, editable, snapHint, labels, zoneTints, zoneTag, startRename]);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    if (!editable) return;
    let d = docRef.current, moved = false, removed = false, movedId = "";
    for (const c of changes) {
      if (c.type === "position" && c.position && c.dragging) {
        const p = partById(d, c.id);
        if (!p) continue;
        d = p.kind === "node" ? patchPart(d, c.id, { pipe: null, x: Math.round(c.position.x), y: Math.round(c.position.y) }) : movePart(d, c.id, Math.round(c.position.x), Math.round(c.position.y));
        moved = true; movedId = c.id;
      } else if (c.type === "remove") { d = removePart(d, c.id); removed = true; }
      else if (c.type === "select" && c.selected) setSel(c.id);
    }
    if (removed) { const now = Date.now(); commit(d, now - lastRemove.current < 400); lastRemove.current = now; setSel(null); }
    else if (moved) {
      docRef.current = d; setDraft(d);
      const hit = movedId && magnetOn() ? findSnap(d, movedId, SNAP_DIST).hit : null;                 // glow the two ports that will join on drop
      setSnapHint((h) => (hit ? (h && h.a.part === hit.mine.part && h.a.port === hit.mine.port && h.b.part === hit.other.part && h.b.port === hit.other.port ? h : { a: hit.mine, b: hit.other }) : h ? null : h));
    }
  }, [editable, commit, magnet]);

  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    if (!editable) return;
    let d = docRef.current, changed = false;
    for (const c of changes) if (c.type === "remove") { d = removeLink(d, c.id); changed = true; }
    if (changed) { const now = Date.now(); commit(d, now - lastRemove.current < 400); lastRemove.current = now; }
  }, [editable, commit]);

  const onConnect = useCallback((c: { source: string; target: string; sourceHandle: string | null; targetHandle: string | null }) => {
    const r = connect(docRef.current, { part: c.source, port: c.sourceHandle ?? "out" }, { part: c.target, port: c.targetHandle ?? "in" });
    if (!r.result.ok) toast.error(r.result.reason ?? "Cannot connect these ports");
    else { commit(r.doc); requestAnimationFrame(() => updateInternals([c.source, c.target])); }
  }, [commit, updateInternals]);

  const onDragStop = useCallback((id: string, alt: boolean) => {
    let d = docRef.current;
    const m = partById(d, id), mg = magnetOn(alt);
    if (m && m.kind === "node") d = attachMeterAt(d, m.id, mg ? SNAP_DIST : 14).doc;     // dropped on a pipe: the sensor snaps onto it (pipe + position, arrow along the flow)
    else if (m && mg) {
      if (dragStart.current) d = detachPulledLinks(dragStart.current, d, id, SNAP_DIST);   // pulled clearly away: unplug; tiny moves keep their links
      d = snapPart(d, id);
    }
    dragStart.current = null;
    commit(d);
    requestAnimationFrame(() => updateInternals([id, ...d.links.filter((l) => l.from.part === id || l.to.part === id).flatMap((l) => [l.from.part, l.to.part])]));
  }, [commit, magnet, snapPart, updateInternals]);

  const onPaneClick = useCallback((e: React.MouseEvent) => {
    setSel(null);
    if (pending && editable) { const p = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY }); place(pending, p.x, p.y, e.altKey); }
  }, [pending, editable, rf, place]);

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const raw = e.dataTransfer.getData("application/x-aqua");
    if (!raw || !editable) return;
    const p = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY });
    place(JSON.parse(raw) as PaletteItem, p.x, p.y, e.altKey);
  };

  const focusPart = useCallback((id: string) => {
    setSel(id); setHighlight(id);
    rf.fitView({ nodes: [{ id }], duration: 300, maxZoom: 1.3, padding: 0.6 });
    setTimeout(() => setHighlight((h) => (h === id ? null : h)), 2500);
  }, [rf]);

  // ---- keyboard: undo / redo
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (!editable || typing(e.target)) return;
      const mod = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
      if (mod && k === "z" && !e.shiftKey) { e.preventDefault(); setHist(undo); }
      else if (mod && (k === "y" || (k === "z" && e.shiftKey))) { e.preventDefault(); setHist(redo); }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [editable]);

  // ---- save / activate / import / export
  const save = async () => {
    setBusy(true);
    try {
      const body = { ...hist.present, name };
      const r = await v2.savePipeline(pipelineId, { doc: body, name, note: note.trim() || undefined, activate: false });
      toast.success(`Saved version ${r.version}${r.valid ? " (valid)" : " as a draft - it has errors and cannot be activated yet"}`);
      setSaveOpen(false); setNote("");
      await load(r.version);
    } catch (e) { toast.error(e instanceof Error ? e.message : "Save failed"); } finally { setBusy(false); }
  };
  const activate = async (version: number) => {
    setBusy(true);
    try { await v2.activate(pipelineId, version); toast.success(`Version ${version} is now active`); await load(version); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Activation failed"); } finally { setBusy(false); }
  };
  const onImport = async (f: File | undefined) => {
    if (!f) return;
    try {
      const j = JSON.parse(await f.text());
      if (!j || j.schema !== 1 || !Array.isArray(j.parts) || !Array.isArray(j.links)) throw new Error("This file is not an Aqua Guard pipeline (schema 1).");
      commit({ ...j, name: j.name ?? name } as PipelineDoc); setSel(null);
      toast.success("Imported into the editor. Check it, then save a new version.");
    } catch (e) { toast.error(e instanceof Error ? e.message : "Import failed"); }
  };

  if (loadErr) return <div className="space-y-3"><ConnectionBanner error={loadErr} onRetry={() => load()} /><Link to="/pipelines" className="text-sm underline">Back to pipelines</Link></div>;
  if (!loaded) return <Loading label="Loading pipeline…" />;

  const full = loaded.full;
  const activeV = full.activeVersion;
  const canActivate = isAdmin && !dirty && !!savedVersion?.valid && savedVersion.version !== activeV;
  const selPart = sel ? partById(doc, sel) ?? null : null;
  const propsEl = <Properties part={selPart} doc={doc} info={selPart?.kind === "node" ? nodeInfo.get(selPart.number ?? -1) : undefined} disabled={!editable}
    onPatch={(p) => selPart && commit(patchPart(doc, selPart.id, p))} onRotate={() => selPart && commit(rotatePart(doc, selPart.id))} onDelete={() => { if (selPart) { commit(removePart(doc, selPart.id)); setSel(null); } }} />;
  const paletteEl = <Palette doc={doc} available={fullAvailable} pending={pending} disabled={!editable} onPick={onPick} />;

  return (
    <div className="flex flex-col gap-2 lg:h-[calc(100dvh-1.5rem)]" data-testid="editor-root">
      {/* toolbar */}
      <div className="flex flex-wrap items-center gap-2" data-testid="toolbar">
        <Button asChild variant="ghost" size="sm"><Link to="/pipelines"><ArrowLeft className="mr-1 h-4 w-4" />Pipelines</Link></Button>
        {wide && <Button size="icon" variant="outline" className="h-8 w-8" aria-label={leftOpen ? "Hide parts panel" : "Show parts panel"} aria-pressed={leftOpen} data-testid="toggle-left" title={leftOpen ? "Hide parts panel" : "Show parts panel"} onClick={() => togglePanel("L")}><PanelLeft className="h-4 w-4" /></Button>}
        <Input aria-label="Pipeline name" className="h-8 w-44 sm:w-56" value={name} maxLength={80} disabled={!editable} onChange={(e) => setName(e.target.value)} />
        <div className="inline-flex rounded-md border p-0.5" role="group" aria-label="View mode">
          <Button size="sm" variant={mode === "design" ? "default" : "ghost"} className="h-7" onClick={() => setMode("design")}><Pencil className="mr-1 h-3.5 w-3.5" />Design</Button>
          <Button size="sm" variant={mode === "live" ? "default" : "ghost"} className="h-7" disabled={!activeV} title={activeV ? "Colour every zone by its leak state" : "Save and activate a valid version first"} onClick={() => setMode("live")}><Eye className="mr-1 h-3.5 w-3.5" />Live</Button>
        </div>
        {mode === "live" && <Pill tone="blue"><Lock className="h-3 w-3" />editing locked · active v{activeV}</Pill>}
        {!isAdmin && mode === "design" && <Pill tone="gray"><Lock className="h-3 w-3" />read only</Pill>}
        {dirty && editable && <Pill tone="amber">unsaved changes</Pill>}
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          {mode === "design" && (<>
            <Button size="sm" variant={magnet ? "default" : "outline"} className="h-8" aria-label="Magnet" aria-pressed={magnet} data-testid="magnet-toggle" disabled={!editable}
              title={magnet ? "Magnet ON: parts snap to nearby free ports and connect (hold Alt to drag freely). Click to turn off." : "Magnet OFF: connect by dragging from one port dot to another. Click to turn on."} onClick={toggleMagnet}>
              <MagnetIcon className="mr-1 h-3.5 w-3.5" />Magnet {magnet ? "on" : "off"}</Button>
            <Button size="icon" variant="outline" className="h-8 w-8" aria-label="Undo" title="Undo (Ctrl+Z)" disabled={!editable || !canUndo(hist)} onClick={() => setHist(undo)}><Undo2 className="h-4 w-4" /></Button>
            <Button size="icon" variant="outline" className="h-8 w-8" aria-label="Redo" title="Redo (Ctrl+Y)" disabled={!editable || !canRedo(hist)} onClick={() => setHist(redo)}><Redo2 className="h-4 w-4" /></Button>
          </>)}
          {wide && <Button size="icon" variant="outline" className="h-8 w-8" aria-label={rightOpen ? "Hide properties panel" : "Show properties panel"} aria-pressed={rightOpen} data-testid="toggle-right" title={rightOpen ? "Hide properties panel" : "Show properties panel"} onClick={() => togglePanel("R")}><PanelRight className="h-4 w-4" /></Button>}
          <DropdownMenu>
            <DropdownMenuTrigger asChild><Button size="icon" variant="outline" className="h-8 w-8" aria-label="More actions" data-testid="toolbar-more"><MoreHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => rf.zoomIn({ duration: 150 })}><ZoomIn className="mr-2 h-3.5 w-3.5" />Zoom in</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => rf.zoomOut({ duration: 150 })}><ZoomOut className="mr-2 h-3.5 w-3.5" />Zoom out</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => rf.fitView({ duration: 200, padding: 0.15 })}><Maximize2 className="mr-2 h-3.5 w-3.5" />Fit to screen</DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => downloadJson(mode === "live" && liveDoc ? liveDoc : hist.present, name)}><Download className="mr-2 h-3.5 w-3.5" />Export JSON</DropdownMenuItem>
              {editable && <DropdownMenuItem onSelect={() => fileRef.current?.click()}><Upload className="mr-2 h-3.5 w-3.5" />Import JSON</DropdownMenuItem>}
              <DropdownMenuItem onSelect={() => setHistOpen(true)}><HistoryIcon className="mr-2 h-3.5 w-3.5" />Versions</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          {editable && <input ref={fileRef} type="file" accept="application/json,.json" className="hidden" data-testid="import-input" onChange={(e) => { void onImport(e.target.files?.[0]); e.target.value = ""; }} />}
          {isAdmin && mode === "design" && <Button size="sm" disabled={busy || (!dirty && !!savedVersion)} onClick={() => setSaveOpen(true)}><Save className="mr-1 h-3.5 w-3.5" />Save new version</Button>}
          {isAdmin && mode === "design" && <Button size="sm" variant="secondary" disabled={busy || !canActivate} title={canActivate ? "" : dirty ? "Save first" : savedVersion?.valid ? "This version is already active" : "Only a valid saved version can be activated"} onClick={() => savedVersion && activate(savedVersion.version)}><Check className="mr-1 h-3.5 w-3.5" />Activate</Button>}
        </div>
      </div>
      <p className="text-xs text-muted-foreground">Version {full.version}{full.note ? ` - ${full.note}` : ""} · saved {fmtTime(full.createdAt)} by {full.createdBy} · active: {activeV ? `v${activeV}` : "none"}{activeV && savedVersion && savedVersion.version !== activeV ? " (this view is a different version)" : ""}</p>
      {pending && editable && <div className="rounded-md border border-blue-300 bg-blue-50 px-3 py-1.5 text-sm text-blue-900" data-testid="pending-hint">Tap the canvas to place: <b>{pending.label}</b> <button className="ml-2 underline" onClick={() => setPending(null)}>cancel</button></div>}

      <div className="grid gap-3 lg:flex-1 lg:min-h-0" style={wide ? { gridTemplateColumns: `${leftOpen ? "230px" : ""} minmax(0,1fr) ${rightOpen ? "290px" : ""}`.trim() } : undefined}>
        {wide && leftOpen && <aside className="rounded-lg border bg-card p-3 overflow-y-auto min-h-0" data-testid="panel-left">{mode === "design" ? paletteEl : (
          <div className="space-y-2"><h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Zones</h3>{zones.length === 0 ? <p className="text-xs text-muted-foreground">Waiting for the first analysis…</p> : <ZoneList zones={zones} selected={zoneSel} onSelect={setZoneSel} />}</div>)}</aside>}

        <div className="min-w-0 flex flex-col gap-2 lg:min-h-0">
          <div className="relative h-[58vh] min-h-[360px] lg:h-auto lg:flex-1 lg:min-h-[300px] rounded-lg border bg-white overflow-hidden" onDrop={onDrop} onDragOver={(e) => { if (editable) e.preventDefault(); }} data-testid="canvas" data-mode={mode}>
            <CanvasCtx.Provider value={ctx}>
              <ReactFlow nodes={shown.nodes.map((n) => ({ ...n, selected: n.id === sel, draggable: editable }))} edges={shown.edges} nodeTypes={nodeTypes as never}
                onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect as never} onPaneClick={onPaneClick} onNodeDragStart={() => { dragStart.current = docRef.current; }} onNodeDrag={(e) => { altRef.current = e.altKey; }} onNodeDragStop={(e, n) => onDragStop(n.id, e.altKey || altRef.current)}
                onNodeClick={(e, n) => { if (pending && editable) { const q = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY }); place(pending, q.x, q.y, e.altKey); return; } setSel(n.id); if (mode === "live") { const z = zmap.parts[n.id]; if (z) setZoneSel(z.id); } if (!wide && mode === "design") setPropsOpen(true); }}
                snapToGrid snapGrid={[20, 20]} nodesDraggable={editable} nodesConnectable={editable} elementsSelectable connectOnClick deleteKeyCode={editable ? ["Delete", "Backspace"] : null}
                minZoom={0.15} maxZoom={2.5} proOptions={{ hideAttribution: false }}>
                <Background gap={20} />
                <Controls showInteractive={false} showZoom={false} showFitView={false} />
              </ReactFlow>
            </CanvasCtx.Provider>
            {shown.nodes.length === 0 && <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-6 text-center text-sm text-muted-foreground">{editable ? "Empty network. Pick a part from the list, then place it here. Start with the Main supply." : "This pipeline has no parts yet."}</div>}
            {!wide && editable && mode === "design" && (
              <div className="absolute bottom-2 left-2 flex gap-2">
                <Button size="sm" onClick={() => setPartsOpen(true)}><Layers className="mr-1 h-4 w-4" />Parts</Button>
                <Button size="sm" variant="secondary" onClick={() => setPropsOpen(true)}><SlidersHorizontal className="mr-1 h-4 w-4" />Properties</Button>
              </div>
            )}
          </div>
          <div className="lg:max-h-[26vh] lg:overflow-y-auto lg:shrink-0">
          {mode === "design" ? (
            <ValidationBar doc={doc} messages={validation?.messages ?? []} valid={validation ? validation.valid : null} partial={validation?.partial} coverage={validation?.coverage} suggestions={validation?.suggestions} checking={checking} error={valErr} onPick={focusPart} freeNodes={fullAvailable} onPlace={(n, pipeId) => { const r = placeNodeOnPipe(doc, n, pipeId); if (r) { commit(r.doc); focusPart(r.id); toast.success(`Node ${n.number} placed on ${labels[pipeId] ?? pipeId}`); } else toast.error("Cannot place the node there"); }} />
          ) : (
            <div className="space-y-2 rounded-lg border bg-card p-3">
              <Legend_ />
              {status.error && <ConnectionBanner error={status.error} stale={status.stale} onRetry={status.refresh} />}
              {status.data?.coverage && <p className="text-xs text-muted-foreground">{status.data.coverage.text}</p>}
              {!wide && <ZoneList zones={zones} selected={zoneSel} onSelect={setZoneSel} />}
            </div>
          )}
          </div>
        </div>

        {wide && rightOpen && <aside className="rounded-lg border bg-card p-3 overflow-y-auto min-h-0" data-testid="panel-right">{mode === "design" ? propsEl : selectedZone ? <div className="space-y-2"><Button size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={() => setZoneSel(null)}>← All zones</Button><ZoneDetail pipelineId={pipelineId} zone={selectedZone} /></div> : <LiveSummary zones={zones} tints={zoneTints} onSelect={setZoneSel} />}</aside>}
      </div>

      {!wide && mode === "live" && selectedZone && <div className="rounded-lg border bg-card p-3"><ZoneDetail pipelineId={pipelineId} zone={selectedZone} /></div>}

      {/* mobile bottom sheets */}
      <Sheet open={!wide && partsOpen} onOpenChange={setPartsOpen}><SheetContent side="bottom" className="max-h-[75vh] overflow-y-auto p-4"><SheetHeader className="p-0 pb-2"><SheetTitle>Parts</SheetTitle></SheetHeader>{paletteEl}</SheetContent></Sheet>
      <Sheet open={!wide && propsOpen && mode === "design"} onOpenChange={setPropsOpen}><SheetContent side="bottom" className="max-h-[75vh] overflow-y-auto p-4"><SheetHeader className="p-0 pb-2"><SheetTitle>Properties</SheetTitle></SheetHeader>{propsEl}</SheetContent></Sheet>

      {/* rename a part (double-click on the canvas) */}
      <Dialog open={renameId !== null} onOpenChange={(o) => { if (!o) setRenameId(null); }}>
        <DialogContent className="max-w-sm">
          <DialogHeader><DialogTitle>Rename part</DialogTitle><DialogDescription>The name is shown on the canvas and in the suggestions. Leave it empty for the default name.</DialogDescription></DialogHeader>
          <div className="grid gap-1.5"><Label htmlFor="part-name">Name</Label>
            <Input id="part-name" autoFocus value={renameText} maxLength={40} placeholder={renameId ? partLabels({ ...doc, parts: doc.parts.map((p) => (p.id === renameId ? { ...p, label: "" } : p)) })[renameId] : ""}
              onChange={(e) => setRenameText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && renameId) { commit(patchPart(doc, renameId, { label: renameText.trim() || undefined })); setRenameId(null); } }} /></div>
          <DialogFooter><Button variant="outline" onClick={() => setRenameId(null)}>Cancel</Button>
            <Button onClick={() => { if (renameId) commit(patchPart(doc, renameId, { label: renameText.trim() || undefined })); setRenameId(null); }}>Rename</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      {/* save dialog */}
      <Dialog open={saveOpen} onOpenChange={setSaveOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader><DialogTitle>Save new version</DialogTitle><DialogDescription>Every save is a new version; old versions are kept. A version with errors is stored as a draft and cannot be activated.</DialogDescription></DialogHeader>
          <div className="grid gap-1.5"><Label htmlFor="note">What changed? (optional)</Label><Input id="note" value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} placeholder="e.g. added Node 3 on the kitchen branch" /></div>
          {validation && !validation.valid && <p className="rounded-md border border-amber-300 bg-amber-50 p-2 text-sm text-amber-900">The design has {validation.messages.filter((m) => m.severity === "error").length} error(s). It will be saved as a draft.</p>}
          <DialogFooter><Button variant="outline" onClick={() => setSaveOpen(false)}>Cancel</Button><Button disabled={busy} onClick={save}>Save</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      {/* version history */}
      <Dialog open={histOpen} onOpenChange={setHistOpen}>
        <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto">
          <DialogHeader><DialogTitle>Version history</DialogTitle><DialogDescription>Open an old version to continue from it (saving creates a new version). Alerts remember the version that was active when they were raised.</DialogDescription></DialogHeader>
          <ul className="divide-y rounded-md border text-sm">
            {loaded.versions.map((v) => (
              <li key={v.version} className="flex flex-wrap items-center gap-2 p-2.5">
                <b>v{v.version}</b>{v.active && <Pill tone="green">active</Pill>}{!v.valid && <Pill tone="amber">draft (errors)</Pill>}
                <span className="min-w-0 flex-1 text-xs text-muted-foreground">{v.note || "—"} · {fmtTime(v.createdAt)} · {v.createdBy}</span>
                <Button size="sm" variant="outline" disabled={dirty && v.version !== full.version} title={dirty ? "Save or discard your changes first" : ""} onClick={() => { setHistOpen(false); void load(v.version); }}>Open</Button>
                {isAdmin && v.valid && !v.active && <Button size="sm" variant="secondary" disabled={busy || dirty} onClick={() => { setHistOpen(false); void activate(v.version); }}>Activate</Button>}
              </li>))}
          </ul>
          <DialogFooter><Button variant="outline" onClick={() => setHistOpen(false)}>Close</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export function PipelineEditor(props: { pipelineId: number; initialMode?: "design" | "live"; initialZone?: string | null }) {
  return <ReactFlowProvider><Editor {...props} /></ReactFlowProvider>;
}
