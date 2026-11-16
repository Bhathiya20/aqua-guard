// Top status strip: gateway, nodes online and the overall leak state (never green when unknown).
import { Link } from "react-router";
import { CheckCircle2, Circle } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { Pill } from "./common";
import { usePolling } from "../lib/usePolling";
import { useActivePipeline } from "../lib/useActivePipeline";
import { leakCheck, v2, type LeakCheck, type SummaryV2 } from "../lib/v2";

export type SetupStep = { key: string; label: string; done: boolean; to: string };

/** gateway online -> nodes added -> pipeline drawn -> activated -> leak check running */
export function setupSteps(sum: SummaryV2 | null | undefined, pipelines: number, active: boolean, lc: LeakCheck): SetupStep[] {
  return [
    { key: "gateway", label: "Gateway online", done: sum?.gateway?.state === "online", to: "/nodes" },
    { key: "nodes", label: "Nodes added", done: (sum?.nodes.total ?? 0) > 0, to: "/nodes" },
    { key: "pipeline", label: "Pipeline drawn", done: pipelines > 0, to: "/pipelines" },
    { key: "active", label: "Pipeline activated", done: active, to: "/pipelines" },
    { key: "check", label: "Leak check running", done: active && (lc.state === "ok" || lc.state === "watching" || lc.state === "suspected" || lc.state === "confirmed"), to: "/leak-detection" },
  ];
}

const LEAK_TONE: Record<LeakCheck["state"], "green" | "amber" | "red" | "gray" | "violet"> = { ok: "green", watching: "amber", paused: "amber", suspected: "amber", inconsistent: "violet", confirmed: "red", none: "gray" };

/** One line on every page: gateway, nodes online and the honest leak-check state. */
export function StatusStrip() {
  const s = usePolling(() => v2.summary(), 5000);
  const ap = useActivePipeline(6000);
  const d = s.data;
  const lc = leakCheck(ap.status.data?.zones);
  const gwOk = d?.gateway?.state === "online";
  const leakText = !ap.active ? "no pipeline active" : lc.state === "ok" ? "no leak" : lc.state === "watching" ? "watching" : lc.state === "paused" ? "paused (no data)" : lc.state === "none" ? "nothing monitored" : lc.headline.toLowerCase();
  const cal = usePolling(() => v2.calibrationActive(), 5000);
  return (<>
    {cal.data?.active && (
      <div className="border-b border-amber-300 bg-amber-100 px-4 py-1.5 text-xs text-amber-900" data-testid="calibration-banner" role="status">
        <b>Calibration in progress</b> - leak detection is paused for {(cal.data.nodes ?? []).map((n) => n.label).join(", ") || "the selected nodes"}. <Link className="underline font-medium" to="/nodes/calibrate">Open the calibration wizard</Link>
      </div>)}
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b bg-card/70 px-4 py-1.5 text-xs" data-testid="status-strip" role="status">
      <span className="inline-flex items-center gap-1.5">Gateway <Pill tone={!d ? "gray" : gwOk ? "green" : "red"}>{!d ? "…" : d.gateway ? d.gateway.state : "none"}</Pill></span>
      <span className="inline-flex items-center gap-1.5">Nodes <Pill tone={!d ? "gray" : d.nodes.total === 0 ? "gray" : d.nodes.online === d.nodes.total ? "green" : "amber"}>{d ? `${d.nodes.online} / ${d.nodes.total} online` : "…"}</Pill></span>
      <Link to="/leak-detection" className="inline-flex items-center gap-1.5">Leak check <Pill tone={ap.active ? LEAK_TONE[lc.state] : "gray"}>{leakText}</Pill></Link>
    </div></>
  );
}

export function SetupProgress({ sum, pipelines, active, lc }: { sum: SummaryV2 | null | undefined; pipelines: number; active: boolean; lc: LeakCheck }) {
  const steps = setupSteps(sum, pipelines, active, lc);
  const done = steps.filter((x) => x.done).length;
  const next = steps.find((x) => !x.done);
  if (!next) return null;                                              // everything is set up: nothing to show
  return (
    <Card data-testid="setup-progress">
      <CardHeader className="pb-2"><CardTitle className="text-base">Setup progress · {done} of {steps.length}</CardTitle><CardDescription>Next: <Link className="underline" to={next.to}>{next.label}</Link></CardDescription></CardHeader>
      <CardContent>
        <ol className="grid gap-2 sm:grid-cols-5">
          {steps.map((x) => (
            <li key={x.key} data-testid={`step-${x.key}`} data-done={x.done ? "1" : "0"} className={`flex items-center gap-2 rounded-md border p-2 text-sm ${x.done ? "border-emerald-200 bg-emerald-50 text-emerald-900" : x === next ? "border-blue-300 bg-blue-50" : "text-muted-foreground"}`}>
              {x.done ? <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" /> : <Circle className="h-4 w-4 shrink-0" />}<span>{x.label}</span>
            </li>))}
        </ol>
      </CardContent>
    </Card>
  );
}
