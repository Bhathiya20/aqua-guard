import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import type { CmdState } from "../../lib/useCommand";

/** Shows a signed node command moving through sending -> sent -> ok | error. */
export function CommandStatus({ state }: { state: CmdState }) {
  if (state.phase === "idle") return null;
  const cls = state.phase === "ok" ? "border-emerald-200 bg-emerald-50 text-emerald-900" : state.phase === "error" ? "border-red-200 bg-red-50 text-red-900" : "border-blue-200 bg-blue-50 text-blue-900";
  const Icon = state.phase === "ok" ? CheckCircle2 : state.phase === "error" ? XCircle : Loader2;
  return (
    <div role="status" data-testid="command-status" data-phase={state.phase} className={`flex items-start gap-2 rounded-md border p-2.5 text-sm ${cls}`}>
      <Icon className={`h-4 w-4 mt-0.5 shrink-0 ${state.phase === "sending" || state.phase === "sent" ? "animate-spin" : ""}`} />
      <span className="min-w-0">
        <span className="font-semibold">{state.phase === "sending" ? "Sending" : state.phase === "sent" ? "Sent" : state.phase === "ok" ? "OK" : "Error"}</span>
        {" - "}{state.message}
      </span>
    </div>
  );
}
