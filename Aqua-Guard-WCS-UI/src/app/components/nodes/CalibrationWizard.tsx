// Calibration wizard: known-volume runs, result and apply.
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { ArrowDown, ArrowLeft, ArrowUp, CheckCircle2, GripVertical, Loader2, Play, Plus, Ruler, Square, Trash2, X, XCircle } from "lucide-react";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "../ui/alert-dialog";
import { useAuth } from "../../lib/auth";
import { ApiError } from "../../lib/api";
import { usePolling } from "../../lib/usePolling";
import { v2, nodeTitle, type CalApplyResult, type CalNode, type CalSession, type NodeV2 } from "../../lib/v2";
import { reasonText } from "../../lib/useCommand";
import { EmptyState, Loading, PageHeader, Pill } from "../common";
import { CONNECT_NOTES, RigDiagram } from "./RigDiagram";

const STEPS = ["Select nodes", "Connect", "Purge air", "Runs", "Results"] as const;
const QUALITY_TONE = { good: "green", ok: "amber", poor: "red", unknown: "gray", none: "gray" } as const;
const fmt = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : v.toFixed(d));

function Stepper({ step, onGo, max }: { step: number; onGo: (n: number) => void; max: number }) {
  return (
    <ol className="flex flex-wrap gap-2 text-sm" aria-label="Calibration steps">
      {STEPS.map((s, i) => (
        <li key={s}>
          <button type="button" disabled={i + 1 > max} onClick={() => onGo(i + 1)} aria-current={step === i + 1 ? "step" : undefined} data-testid={`step-${i + 1}`}
            className={`rounded-full border px-3 py-1 ${step === i + 1 ? "border-blue-600 bg-blue-50 font-semibold text-blue-900" : i + 1 > max ? "text-muted-foreground opacity-60" : "hover:bg-muted"}`}>
            {i + 1}. {s}
          </button>
        </li>))}
    </ol>
  );
}

// ---------------------------------------------------------------- step 1: choose and order the nodes
function SelectStep({ nodes, onStart, busy }: { nodes: NodeV2[]; onStart: (ids: number[]) => void; busy: boolean }) {
  const eligible = nodes.filter((n) => n.type === "flow" && n.registryState === "paired" && n.state !== "out_of_service" && n.state !== "removed" && n.state !== "pending");
  const [order, setOrder] = useState<number[]>([]);
  const drag = useRef<number | null>(null);
  const byId = new Map(eligible.map((n) => [n.id, n]));
  const chosen = order.map((id) => byId.get(id)).filter(Boolean) as NodeV2[];
  const rest = eligible.filter((n) => !order.includes(n.id));
  const move = (i: number, d: number) => { const j = i + d; if (j < 0 || j >= order.length) return; const o = [...order]; [o[i], o[j]] = [o[j], o[i]]; setOrder(o); };
  const drop = (to: number) => { const from = drag.current; drag.current = null; if (from === null || from === to) return; const o = [...order]; const [x] = o.splice(from, 1); o.splice(to, 0, x); setOrder(o); };
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">Pick the flow nodes to calibrate together. They are connected in series (same water through every node), in the order below; the order only changes the diagram.</p>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <h3 className="text-sm font-semibold">Rig order (drag to reorder)</h3>
          {chosen.length === 0 ? <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">No node selected yet.</p> : (
            <ol className="space-y-1.5" data-testid="rig-order">
              {chosen.map((n, i) => (
                <li key={n.id} draggable onDragStart={() => { drag.current = i; }} onDragOver={(e) => e.preventDefault()} onDrop={() => drop(i)} data-testid={`chosen-${n.number}`}
                  className="flex items-center gap-2 rounded-md border bg-card px-2 py-1.5 text-sm">
                  <GripVertical className="h-4 w-4 cursor-grab text-muted-foreground" aria-hidden />
                  <span className="w-5 text-muted-foreground">{i + 1}.</span><span className="flex-1 truncate">{nodeTitle(n)}</span>
                  <Button size="icon" variant="ghost" className="h-7 w-7" aria-label={`Move ${n.label} up`} disabled={i === 0} onClick={() => move(i, -1)}><ArrowUp className="h-3.5 w-3.5" /></Button>
                  <Button size="icon" variant="ghost" className="h-7 w-7" aria-label={`Move ${n.label} down`} disabled={i === chosen.length - 1} onClick={() => move(i, 1)}><ArrowDown className="h-3.5 w-3.5" /></Button>
                  <Button size="icon" variant="ghost" className="h-7 w-7" aria-label={`Remove ${n.label}`} onClick={() => setOrder(order.filter((x) => x !== n.id))}><X className="h-3.5 w-3.5" /></Button>
                </li>))}
            </ol>)}
        </div>
        <div className="space-y-2">
          <h3 className="text-sm font-semibold">Available nodes</h3>
          {rest.length === 0 ? <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">{eligible.length === 0 ? "No paired flow node is available (nodes out of service are left out)." : "All nodes selected."}</p> : (
            <ul className="space-y-1.5">
              {rest.map((n) => (
                <li key={n.id} className="flex items-center gap-2 rounded-md border px-2 py-1.5 text-sm">
                  <span className="flex-1 truncate">{nodeTitle(n)}</span><Pill tone={n.state === "online" ? "green" : "amber"}>{n.state}</Pill>
                  <Button size="sm" variant="outline" aria-label={`Add ${n.label}`} onClick={() => setOrder([...order, n.id])}><Plus className="mr-1 h-3.5 w-3.5" />Add</Button>
                </li>))}
            </ul>)}
        </div>
      </div>
      {chosen.length > 0 && <RigDiagram labels={chosen.map((n) => n.label)} />}
      <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">While the session is open, <b>leak detection is paused</b> for the zones that use the selected nodes (a banner shows on every page). It resumes when you apply the result or cancel.</div>
      <div className="flex justify-end"><Button disabled={chosen.length === 0 || busy} onClick={() => onStart(order)} data-testid="start-session">Start calibration session</Button></div>
    </div>
  );
}

// ---------------------------------------------------------------- live table of the nodes (steps 3-4)
function LiveNodes({ s }: { s: CalSession }) {
  return (
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3" data-testid="live-nodes">
      {s.nodes.map((n) => (
        <div key={n.id} className="rounded-md border p-2.5 text-sm">
          <div className="flex items-center justify-between gap-2"><b>{n.label}</b><Pill tone={n.idle.ok ? "green" : "amber"}>{n.idle.ok ? "idle" : n.idle.reason}</Pill></div>
          <div className="mt-1 grid grid-cols-2 gap-x-2 text-xs text-muted-foreground">
            <span>Flow</span><span className="text-right font-mono text-foreground">{n.live.flowLpm === null ? "—" : `${n.live.flowLpm.toFixed(2)} L/min`}</span>
            <span>Counter</span><span className="text-right font-mono text-foreground">{n.live.cumPulses ?? "—"}</span>
            {n.pulsesInRun !== null && <><span>Pulses this run</span><span className="text-right font-mono text-foreground" data-testid={`inrun-${n.number}`}>{n.pulsesInRun}</span></>}
          </div>
        </div>))}
    </div>
  );
}

// ---------------------------------------------------------------- step 3: purge
function PurgeStep({ s, onNext }: { s: CalSession; onNext: () => void }) {
  const [left, setLeft] = useState<number | null>(null);
  useEffect(() => {
    if (left === null || left <= 0) return;
    const t = setTimeout(() => setLeft((x) => (x === null ? x : x - 1)), 1000);
    return () => clearTimeout(t);
  }, [left]);
  return (
    <div className="space-y-4">
      <p className="text-sm">Open the tap and let water run through the whole rig for about <b>10 seconds</b> so that no air stays in the sensors or hoses. Then close the tap and wait until every node shows <b>idle</b>.</p>
      <LiveNodes s={s} />
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="outline" onClick={() => setLeft(10)} disabled={left !== null && left > 0} data-testid="purge-go">Start 10 s timer</Button>
        {left !== null && <span className="text-lg font-mono" data-testid="purge-left" aria-live="polite">{left > 0 ? `${left} s` : "Done - close the tap"}</span>}
        <Button className="ml-auto" onClick={onNext} disabled={!s.nodes.every((n) => n.idle.ok)} data-testid="purge-next">{s.nodes.every((n) => n.idle.ok) ? "Continue to runs" : "Waiting for all nodes to be idle…"}</Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- step 4: runs
function RunsStep({ s, admin, set }: { s: CalSession; admin: boolean; set: (s: CalSession) => void }) {
  const [busy, setBusy] = useState(false);
  const [vol, setVol] = useState<Record<number, string>>({});
  const act = async (fn: () => Promise<CalSession>) => {
    setBusy(true);
    try { set(await fn()); } catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); } finally { setBusy(false); }
  };
  const active = s.runs.find((r) => r.id === s.activeRunId);
  const finished = s.runs.filter((r) => r.status === "done" || r.status === "invalid");
  const plan = ["slow", "medium", "fast", "slow", "fast"][s.runs.length] ?? "any";
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">Recommended: {s.recommended.minRuns}-{s.recommended.maxRuns} runs of at least {s.recommended.minLiters} L each, at a slow, a medium and a fast flow. Close the tap fully between runs and keep the container steady.</p>
      <LiveNodes s={s} />
      {admin && s.status === "running" && (
        <div className="rounded-md border p-3 space-y-2" data-testid="run-controls">
          {!active && <>
            <div className="flex flex-wrap items-center gap-3">
              <Button onClick={() => act(() => v2.calibrationStart(s.id))} disabled={!s.canStart || busy} data-testid="run-start"><Play className="mr-1.5 h-4 w-4" />Start run {s.runs.length ? Math.max(...s.runs.map((r) => r.idx)) + 1 : 1}</Button>
              <span className="text-sm text-muted-foreground">Next pour: <b>{plan}</b></span>
            </div>
            {!s.canStart && <p className="text-xs text-amber-800" data-testid="start-blocked">Start is enabled when every node is idle: {s.nodes.filter((n) => !n.idle.ok).map((n) => `${n.label} (${n.idle.reason})`).join(", ") || "…"}.</p>}
          </>}
          {active?.status === "active" && <div className="flex flex-wrap items-center gap-3">
            <Pill tone="blue">run {active.idx} in progress</Pill><span className="text-sm">Pour the water now, then close the tap and press Stop.</span>
            <Button variant="destructive" onClick={() => act(() => v2.calibrationStop(s.id, active.id))} disabled={busy} data-testid="run-stop"><Square className="mr-1.5 h-4 w-4" />Stop</Button>
          </div>}
          {active?.status === "stopping" && <div className="flex items-center gap-2 text-sm" role="status" data-testid="run-stopping"><Loader2 className="h-4 w-4 animate-spin" />Waiting for {active.waitingFor.join(", ") || "the nodes"} to report a steady counter (two new readings without pulses; timeout 60 s)…</div>}
        </div>)}
      {finished.length > 0 && (
        <div className="space-y-2" data-testid="run-list">
          {finished.map((r) => (
            <div key={r.id} className={`flex flex-wrap items-center gap-2 rounded-md border p-2.5 text-sm ${r.status === "invalid" ? "border-red-300 bg-red-50" : ""}`}>
              <b>Run {r.idx}</b>
              {r.status === "invalid" ? <><Pill tone="red">invalid</Pill><span className="min-w-0 flex-1 text-red-900">{r.reason}</span></> : <>
                {Object.entries(r.cells).filter(([, c]) => !c.valid).map(([id, c]) => <span key={id} className="text-xs text-red-700">{s.nodes.find((n) => String(n.id) === id)?.label}: {c.reason}</span>)}
                {r.volumeL !== null ? <Pill tone="green">{r.volumeL} L entered</Pill> : <Pill tone="amber">volume needed</Pill>}
                {admin && s.status === "running" && <span className="ml-auto flex items-center gap-1.5">
                  <label className="sr-only" htmlFor={`vol-${r.id}`}>Measured volume of run {r.idx} (L)</label>
                  <Input id={`vol-${r.id}`} className="h-8 w-28" inputMode="decimal" placeholder="measured L" value={vol[r.id] ?? (r.volumeL?.toString() ?? "")} onChange={(e) => setVol({ ...vol, [r.id]: e.target.value })} />
                  <Button size="sm" disabled={busy || !(Number(vol[r.id]) > 0)} onClick={() => act(() => v2.calibrationVolume(s.id, r.id, Number(vol[r.id])))}>Save</Button></span>}
              </>}
              {admin && s.status === "running" && <Button size="icon" variant="ghost" className={`h-7 w-7 ${r.status === "invalid" ? "ml-auto" : ""}`} aria-label={`Delete run ${r.idx}`} onClick={() => act(() => v2.calibrationDeleteRun(s.id, r.id))}><Trash2 className="h-3.5 w-3.5" /></Button>}
            </div>))}
        </div>)}
    </div>
  );
}

// ---------------------------------------------------------------- command progress (sent -> ok per node)
function ApplyProgress({ results }: { results: CalApplyResult[] }) {
  const [st, setSt] = useState<Record<number, string>>({});
  useEffect(() => {
    let alive = true;
    const t0 = Date.now();
    const tick = async () => {
      const next: Record<number, string> = {};
      for (const r of results) {
        if (!r.commandId) continue;
        try { const c = await v2.command(r.commandId); next[r.commandId] = c.status === "ok" ? "ok" : c.status === "sent" || c.status === "queued" ? "sent" : `error: ${reasonText(c.result, c.status)}`; } catch { next[r.commandId] = "sent"; }
      }
      if (!alive) return;
      setSt(next);
      if (Object.values(next).some((x) => x === "sent") && Date.now() - t0 < 30000) setTimeout(tick, 900);
    };
    tick();
    return () => { alive = false; };
  }, [results]);
  return (
    <ul className="space-y-1" data-testid="apply-progress">
      {results.map((r) => {
        const x = r.commandId ? st[r.commandId] ?? "sent" : "error";
        const ok = x === "ok";
        return (
          <li key={r.nodeId} className={`flex items-center gap-2 rounded-md border p-2 text-sm ${ok ? "border-emerald-200 bg-emerald-50" : r.error || x.startsWith("error") ? "border-red-200 bg-red-50" : "border-blue-200 bg-blue-50"}`} data-testid={`apply-${r.nodeId}`} data-phase={r.error ? "error" : x}>
            {ok ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : r.error || x.startsWith("error") ? <XCircle className="h-4 w-4 text-red-600" /> : <Loader2 className="h-4 w-4 animate-spin" />}
            <b>{r.label}</b><span>{r.error ? r.error : ok ? `sent → ok (${(r.valueX10 ?? 0) / 10} pulses/L)` : x === "sent" ? "sent → waiting for the node…" : x}</span>
          </li>);
      })}
    </ul>
  );
}

// ---------------------------------------------------------------- step 5: results
function ResultsStep({ s, admin, set, onDone }: { s: CalSession; admin: boolean; set: (s: CalSession) => void; onDone: () => void }) {
  const [median, setMedian] = useState(false);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [applied, setApplied] = useState<CalApplyResult[] | null>(null);
  const [busy, setBusy] = useState(false);
  const method = median ? "median" : "weighted";
  const r = s.results;
  const apply = async (conf: boolean) => {
    setBusy(true);
    try {
      const out = await v2.calibrationApply(s.id, { method, confirm: conf });
      setApplied(out.results);
      try { set(await v2.calibration(s.id)); } catch { /* the banner clears on the next poll */ }
    } catch (e) {
      const err = e as ApiError;
      if (err.status === 409 && err.data?.code === "needs_confirm") setConfirm(err.data.error);
      else toast.error(err.message);
    } finally { setBusy(false); }
  };
  const warnByRun = (id: number) => r.warnings.filter((w) => w.runId === id);
  const canApply = admin && r.validRuns > 0 && s.nodes.every((n) => s.proposals[String(n.id)]?.[method].value !== null) && !(applied?.every((x) => x.commandId));
  return (
    <div className="space-y-4">
      <div className="overflow-x-auto rounded-md border">
        <table className="w-full text-sm" data-testid="results-runs">
          <thead className="bg-muted/50 text-left"><tr><th className="p-2">Run</th><th className="p-2">Volume</th><th className="p-2">Node</th><th className="p-2 text-right">Pulses</th><th className="p-2 text-right">Pulses/L</th><th className="p-2 text-right">Flow</th></tr></thead>
          <tbody>
            {r.runs.length === 0 && <tr><td className="p-3 text-muted-foreground" colSpan={6}>No runs yet.</td></tr>}
            {r.runs.map((run) => s.nodes.map((n, i) => {
              const c = run.nodes[String(n.id)];
              return (
                <tr key={`${run.id}-${n.id}`} className={`border-t ${run.status === "invalid" || (c && !c.valid) ? "bg-red-50/60" : ""} ${warnByRun(run.id).some((w) => w.nodeId === n.id) ? "bg-amber-50/70" : ""}`}>
                  {i === 0 && <td className="p-2 align-top" rowSpan={s.nodes.length}>{run.idx}{run.status === "invalid" && <Pill tone="red">invalid</Pill>}</td>}
                  {i === 0 && <td className="p-2 align-top" rowSpan={s.nodes.length}>{run.volumeL === null ? "—" : `${run.volumeL} L`}</td>}
                  <td className="p-2">{n.label}</td><td className="p-2 text-right font-mono">{c?.pulses ?? "—"}</td>
                  <td className="p-2 text-right font-mono">{fmt(c?.ppl, 1)}</td><td className="p-2 text-right font-mono">{c?.flowLpm ? `${c.flowLpm.toFixed(2)} L/min` : "—"}</td>
                </tr>);
            }))}
          </tbody>
        </table>
      </div>
      {r.warnings.length > 0 && (
        <ul className="space-y-1.5" data-testid="cal-warnings">
          {r.warnings.map((w, i) => <li key={i} className={`rounded-md border p-2 text-sm ${w.severity === "warning" ? "border-amber-300 bg-amber-50 text-amber-900" : "border-blue-200 bg-blue-50 text-blue-900"}`}>{w.message}</li>)}
        </ul>)}
      <div className="overflow-x-auto rounded-md border">
        <table className="w-full text-sm" data-testid="results-nodes">
          <thead className="bg-muted/50 text-left"><tr><th className="p-2">Node</th><th className="p-2 text-right">Runs</th><th className="p-2 text-right">Weighted</th><th className="p-2 text-right">Median</th><th className="p-2 text-right">Spread</th><th className="p-2">Quality</th><th className="p-2 text-right">Current</th><th className="p-2 text-right">New value</th></tr></thead>
          <tbody>
            {r.nodes.map((n) => {
              const p = s.proposals[String(n.id)]?.[method];
              return (
                <tr key={n.id} className="border-t" data-testid={`stat-${n.id}`}>
                  <td className="p-2">{n.label}</td><td className="p-2 text-right">{n.runs}</td><td className="p-2 text-right font-mono">{fmt(n.weightedPpl, 1)}</td><td className="p-2 text-right font-mono">{fmt(n.medianPpl, 1)}</td>
                  <td className="p-2 text-right font-mono">{n.spreadPct === null ? "—" : `${n.spreadPct.toFixed(1)} %`}</td><td className="p-2"><Pill tone={QUALITY_TONE[n.quality]}>{n.quality === "none" ? "no data" : n.quality}</Pill></td>
                  <td className="p-2 text-right font-mono">{n.current}</td>
                  <td className={`p-2 text-right font-mono ${p?.verdict === "refuse" ? "text-red-700" : p?.verdict === "confirm" ? "text-amber-700" : ""}`} title={p?.message}>{p?.value ?? "—"}{p?.verdict === "refuse" ? " ✕" : p?.verdict === "confirm" ? " !" : ""}</td>
                </tr>);
            })}
          </tbody>
        </table>
      </div>
      {s.nodes.some((n) => s.proposals[String(n.id)]?.[method].verdict === "refuse" && s.proposals[String(n.id)][method].message) && (
        <p className="text-sm text-red-700" data-testid="refuse-note">{s.nodes.map((n) => s.proposals[String(n.id)]?.[method]).filter((p) => p?.verdict === "refuse" && p.message).map((p) => p!.message).join(" ")}</p>)}
      {admin && s.status !== "cancelled" && (
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={median} onChange={(e) => setMedian(e.target.checked)} data-testid="use-median" />Use the median of the runs (default: volume-weighted, sum of pulses / sum of litres)</label>
          <Button className="ml-auto" disabled={!canApply || busy} onClick={() => apply(false)} data-testid="apply"><Ruler className="mr-1.5 h-4 w-4" />Calibrate</Button>
        </div>)}
      {!admin && <p className="text-sm text-muted-foreground">Read-only: only administrators can run or apply a calibration.</p>}
      {applied && <ApplyProgress results={applied} />}
      {s.status !== "running" && <div className="flex justify-end"><Button variant="outline" onClick={onDone}>Close</Button></div>}
      <AlertDialog open={confirm !== null} onOpenChange={(o) => { if (!o) setConfirm(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>Large change - apply anyway?</AlertDialogTitle><AlertDialogDescription>{confirm}</AlertDialogDescription></AlertDialogHeader>
          <AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel><AlertDialogAction onClick={() => { setConfirm(null); apply(true); }}>Apply anyway</AlertDialogAction></AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

// ---------------------------------------------------------------- the page
export function CalibrationWizard() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const nodes = usePolling(() => v2.nodes(), 3000);
  const act = usePolling(() => v2.calibrationActive(), 4000);
  const [sid, setSid] = useState<number | null>(null);
  const [step, setStep] = useState(1);
  const [busy, setBusy] = useState(false);
  const [cancel, setCancel] = useState(false);
  const [sess, setSess] = useState<CalSession | null>(null);
  const resumed = useRef(false);
  useEffect(() => {
    if (!resumed.current && act.data) {
      resumed.current = true;
      if (act.data.active && act.data.sessionId) { setSid(act.data.sessionId); setStep(4); }
    }
  }, [act.data]);
  const poll = usePolling(() => (sid ? v2.calibration(sid) : Promise.resolve(null)), 1500, [sid]);
  const s: CalSession | null = sess && sess.id === sid ? sess : null;           // one source of truth: poll results and action results both land here
  useEffect(() => { if (poll.data && poll.data.id === sid) setSess(poll.data); }, [poll.data, sid]);

  const start = async (ids: number[]) => {
    setBusy(true);
    try { const c = await v2.calibrationCreate(ids); setSid(c.id); setSess(c); setStep(2); act.refresh(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Could not start the session"); }
    finally { setBusy(false); }
  };
  const close = () => { setSid(null); setSess(null); setStep(1); act.refresh(); };
  const doCancel = async () => { if (!s) return; try { await v2.calibrationCancel(s.id); toast.success("Calibration cancelled - leak detection resumes"); } catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); } close(); };

  const running = s?.status === "running";
  const maxStep = !s ? 1 : 5;
  const labels = s ? s.nodes.map((n: CalNode) => n.label) : [];

  return (
    <div className="space-y-5">
      <PageHeader icon={<Ruler className="h-6 w-6 text-blue-600" />} title="Calibrate nodes" subtitle="Measure pulses per litre with a known volume, in series, and send the result to the nodes."
        right={<Link to="/nodes" className="inline-flex items-center gap-1 text-sm underline"><ArrowLeft className="h-4 w-4" />Back to Nodes</Link>} />
      {!admin && !s && <Card><CardContent><EmptyState title="Administrators only">Only an administrator can run a calibration. If one is running, its progress shows here.</EmptyState></CardContent></Card>}
      {(admin || s) && (
        <Card>
          <CardHeader className="pb-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <CardTitle className="text-base">{s ? `Session ${s.id}` : "New calibration session"}{s && <> <Pill tone={s.status === "running" ? "blue" : s.status === "done" ? "green" : "gray"}>{s.status === "running" ? "running - leak detection paused" : s.status === "done" ? "applied" : "cancelled"}</Pill></>}</CardTitle>
              {admin && running && <Button size="sm" variant="outline" onClick={() => setCancel(true)} data-testid="cancel-session">Cancel session</Button>}
            </div>
            <CardDescription><Stepper step={step} onGo={setStep} max={maxStep} /></CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {step === 1 && (nodes.loading ? <Loading label="Loading nodes…" /> : <SelectStep nodes={nodes.data?.nodes ?? []} onStart={start} busy={busy} />)}
            {s && step === 2 && (
              <div className="space-y-3">
                <RigDiagram labels={labels} />
                <ul className="list-disc space-y-1 pl-5 text-sm">{CONNECT_NOTES.map((t) => <li key={t}>{t}</li>)}</ul>
                <p className="text-sm text-muted-foreground">A pipeline named “Calibration rig” was created for reference (Pipeline page). It is <b>not</b> active, so it never takes part in leak detection.</p>
                <div className="flex justify-end"><Button onClick={() => setStep(3)} data-testid="connected">Connected - continue</Button></div>
              </div>)}
            {s && step === 3 && <PurgeStep s={s} onNext={() => setStep(4)} />}
            {s && step === 4 && <>
              <RunsStep s={s} admin={admin} set={setSess} />
              <div className="flex justify-between"><Button variant="outline" onClick={() => setStep(3)}>Back</Button><Button onClick={() => setStep(5)} disabled={s.results.validRuns === 0} data-testid="to-results">Results ({s.results.validRuns} valid run{s.results.validRuns === 1 ? "" : "s"})</Button></div>
            </>}
            {s && step === 5 && <ResultsStep s={s} admin={admin} set={setSess} onDone={close} />}
          </CardContent>
        </Card>)}
      <AlertDialog open={cancel} onOpenChange={setCancel}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>Cancel this calibration?</AlertDialogTitle><AlertDialogDescription>Nothing is sent to the nodes. Leak detection resumes for their zones.</AlertDialogDescription></AlertDialogHeader>
          <AlertDialogFooter><AlertDialogCancel>Keep going</AlertDialogCancel><AlertDialogAction onClick={doCancel}>Cancel calibration</AlertDialogAction></AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

