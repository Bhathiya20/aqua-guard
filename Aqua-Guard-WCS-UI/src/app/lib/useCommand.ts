import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";
import { v2, type CommandAck } from "./v2";

export type CmdPhase = "idle" | "sending" | "sent" | "ok" | "error";
export interface CmdState { phase: CmdPhase; message: string; commandId: number | null }

const REASONS: Record<string, string> = {
  tls_required: "Adding or replacing a node from the web app needs an encrypted (TLS) MQTT link, which is not set up. Use the gateway page instead.",
  command_key_missing: "The site command key is not configured on the backend.",
  gateway_offline: "The gateway is offline.",
  gateway_time_unsynced: "The gateway has no time yet. Wait for it to sync.",
  mqtt_down: "The backend is not connected to MQTT.",
  duplicate_mac: "This node is already registered.",
  timeout: "The gateway did not answer in time.",
  bad_calibration: "Pulses per litre must be between 10 and 6000.",
};
export const reasonText = (code: string | null | undefined, fallback = "") => (code && REASONS[code]) || fallback || code || "Failed";

/** Runs one signed node command and follows /api/commands/{id} until the gateway answers: sent -> ok | error. */
export function useCommand(intervalMs = 800, maxMs = 25000) {
  const [state, setState] = useState<CmdState>({ phase: "idle", message: "", commandId: null });
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  const reset = useCallback(() => setState({ phase: "idle", message: "", commandId: null }), []);

  const run = useCallback(async (start: () => Promise<CommandAck>): Promise<boolean> => {
    setState({ phase: "sending", message: "Sending to the gateway…", commandId: null });
    let ack: CommandAck;
    try {
      ack = await start();
    } catch (e) {
      const err = e as ApiError;
      const d = err.data || {};
      if (d.commandId) ack = { commandId: d.commandId, status: d.status, result: d.result, number: d.number ?? null };
      else { setState({ phase: "error", message: reasonText(d.code, err.message), commandId: null }); return false; }
    }
    const finish = (s: string, r: string | null) => {
      const ok = s === "ok";
      if (alive.current) setState({ phase: ok ? "ok" : "error", message: ok ? "Done" : reasonText(r, s === "timeout" ? REASONS.timeout : `Command ${s}`), commandId: ack.commandId });
      return ok;
    };
    if (ack.status !== "sent" && ack.status !== "queued") return finish(ack.status, ack.result);
    if (alive.current) setState({ phase: "sent", message: "Sent - waiting for the gateway…", commandId: ack.commandId });
    const t0 = Date.now();
    while (alive.current && Date.now() - t0 < maxMs) {
      await new Promise((r) => setTimeout(r, intervalMs));
      try {
        const c = await v2.command(ack.commandId);
        if (c.status !== "sent" && c.status !== "queued") return finish(c.status, c.result);
      } catch { /* keep polling: a lost response is not a failed command */ }
    }
    return finish("timeout", "timeout");
  }, [intervalMs, maxMs]);

  return { state, run, reset };
}
