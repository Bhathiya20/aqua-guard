// Dialogs of the Nodes page: add, rename, replace, remove, calibrate, TX power, link trend.
import { useEffect, useState, type ReactNode } from "react";
import { ExternalLink, Info } from "lucide-react";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Label } from "../ui/label";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { AlertDialog, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "../ui/alert-dialog";
import { fmtTime } from "../../lib/usePolling";
import { v2, nodeTitle, rssiText, weakLinkReasons, WEAK_LOSS_PCT, WEAK_RSSI_DBM, type CalHistory, type GatewayV2, type LinkPoint, type NodeV2 } from "../../lib/v2";
import { QR_ERROR_TEXT, QrError, formatCode, normalizeCode, normalizeMac, parseQr, type ParsedQr } from "../../lib/qr";
import { useCommand } from "../../lib/useCommand";
import { CommandStatus } from "./CommandStatus";
import { QrScanner } from "./QrScanner";

export const gatewayAddUrl = (gw: GatewayV2 | null | undefined) => (gw?.ip ? `http://${gw.ip}/add` : null);

/** Shown instead of the add/replace form while the MQTT link has no TLS: the pairing code must never travel in clear text. */
export function TlsRequired({ gateway, what }: { gateway: GatewayV2 | null | undefined; what: string }) {
  const url = gatewayAddUrl(gateway);
  return (
    <div data-testid="tls-required" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 space-y-2">
      <p className="flex items-start gap-2"><Info className="h-4 w-4 mt-0.5 shrink-0" /><span><b>{what} is not available from the web app yet.</b> The pairing code would travel unencrypted over the MQTT broker, so the backend refuses to send it.</span></p>
      <p>Add nodes from the gateway page: {url ? <a className="font-mono underline inline-flex items-center gap-1" href={url} target="_blank" rel="noreferrer">{url}<ExternalLink className="h-3 w-3" /></a> : <span className="font-mono">http://&lt;gateway ip&gt;/add</span>}</p>
      <p className="text-xs opacity-80">Renaming, calibrating and removing nodes work normally.</p>
    </div>
  );
}

function Shell({ open, onClose, title, description, children }: { open: boolean; onClose: () => void; title: string; description?: string; children: ReactNode }) {
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-md max-h-[90vh] overflow-y-auto">
        <DialogHeader><DialogTitle>{title}</DialogTitle>{description && <DialogDescription>{description}</DialogDescription>}</DialogHeader>
        {children}
      </DialogContent>
    </Dialog>
  );
}

export function RenameDialog({ node, onClose, onDone }: { node: NodeV2 | null; onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState("");
  const { state, run, reset } = useCommand();
  useEffect(() => { setName(node?.name ?? ""); reset(); }, [node?.id]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!node) return null;
  const busy = state.phase === "sending" || state.phase === "sent";
  return (
    <Shell open onClose={onClose} title={`Rename ${nodeTitle(node)}`} description="The name is stored on the gateway (up to 23 characters).">
      <div className="space-y-2"><Label htmlFor="rename">Name</Label><Input id="rename" value={name} maxLength={23} onChange={(e) => setName(e.target.value)} disabled={busy} /></div>
      <CommandStatus state={state} />
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>{state.phase === "ok" ? "Close" : "Cancel"}</Button>
        <Button disabled={busy || !name.trim() || name.trim() === node.name || state.phase === "ok"} onClick={async () => { if (await run(() => v2.patchNode(node.id, { name: name.trim() }))) onDone(); }}>Rename</Button>
      </DialogFooter>
    </Shell>
  );
}

export function CalibrateDialog({ node, onClose, onDone }: { node: NodeV2 | null; onClose: () => void; onDone: () => void }) {
  const [ppl, setPpl] = useState("450");
  const { state, run, reset } = useCommand();
  useEffect(() => { setPpl(String(node?.pulsesPerLiter ?? 450)); reset(); }, [node?.id]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!node) return null;
  const v = Number(ppl);
  const bad = !Number.isFinite(v) || v < 10 || v > 6000;
  const busy = state.phase === "sending" || state.phase === "sent";
  return (
    <Shell open onClose={onClose} title={`Calibrate ${nodeTitle(node)}`} description="Pulses per litre of the flow sensor (a YF-S201 is about 450). Measure with a known volume, e.g. fill a 10 L bucket and divide the pulse count by 10.">
      <div className="space-y-2"><Label htmlFor="ppl">Pulses per litre</Label><Input id="ppl" inputMode="decimal" value={ppl} onChange={(e) => setPpl(e.target.value)} disabled={busy} aria-invalid={bad} />
        {bad && <p className="text-xs text-red-600">Enter a number between 10 and 6000.</p>}</div>
      <CommandStatus state={state} />
      <CalibrationHistory node={node} disabled={busy} onReverted={onDone} />
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>{state.phase === "ok" ? "Close" : "Cancel"}</Button>
        <Button disabled={busy || bad || state.phase === "ok"} onClick={async () => { if (await run(() => v2.patchNode(node.id, { pulsesPerLiter: v }))) onDone(); }}>Save calibration</Button>
      </DialogFooter>
    </Shell>
  );
}

/** Past calibration values of one node (wizard, manual fine-tune, revert) with a button to go back to the value before a change. */
export function CalibrationHistory({ node, disabled, onReverted }: { node: NodeV2; disabled?: boolean; onReverted: () => void }) {
  const [rows, setRows] = useState<CalHistory[] | null>(null);
  const { state, run } = useCommand();
  const load = () => v2.calibrationHistory(node.id).then((r) => setRows(r.history)).catch(() => setRows([]));
  useEffect(() => { setRows(null); load(); }, [node.id]);   // eslint-disable-line react-hooks/exhaustive-deps
  const SRC = { wizard: "Wizard", manual: "Manual", revert: "Revert" } as const;
  if (rows === null || rows.length === 0) return rows === null ? null : <p className="text-xs text-muted-foreground" data-testid="cal-history-empty">No calibration changes recorded yet.</p>;
  const busy = state.phase === "sending" || state.phase === "sent";
  return (
    <div className="space-y-1.5" data-testid="cal-history">
      <h4 className="text-sm font-medium">Calibration history</h4>
      <div className="max-h-48 overflow-y-auto rounded-md border text-xs">
        <table className="w-full"><thead className="bg-muted/50 text-left"><tr><th className="p-1.5">Date</th><th className="p-1.5">Source</th><th className="p-1.5 text-right">Runs</th><th className="p-1.5 text-right">Spread</th><th className="p-1.5 text-right">Old → new</th><th /></tr></thead>
          <tbody>{rows.map((h) => (
            <tr key={h.id} className="border-t">
              <td className="p-1.5 whitespace-nowrap">{fmtTime(h.at)}</td><td className="p-1.5">{SRC[h.source]}</td><td className="p-1.5 text-right">{h.runs ?? "—"}</td>
              <td className="p-1.5 text-right">{h.spreadPct === null ? "—" : `${h.spreadPct.toFixed(1)} %`}</td><td className="p-1.5 text-right font-mono whitespace-nowrap">{h.oldPpl} → {h.newPpl}</td>
              <td className="p-1.5 text-right"><Button size="sm" variant="ghost" className="h-6 px-2 text-xs" disabled={busy || disabled || h.commandStatus === "error"} aria-label={`Revert to ${h.oldPpl}`}
                onClick={async () => { if (await run(() => v2.calibrationRevert(node.id, h.id))) { onReverted(); load(); } }}>Revert to {h.oldPpl}</Button></td>
            </tr>))}</tbody></table>
      </div>
      <CommandStatus state={state} />
    </div>
  );
}

export const TX_LEVELS = [8.5, 11, 13, 15] as const;
export const txText = (n: { txPowerDbm?: number }) => `${n.txPowerDbm ?? 8.5} dBm`;

/** Per-node radio transmit power. Shows the node's current link quality so the level can be tuned against it. */
export function TxPowerDialog({ node, onClose, onDone }: { node: NodeV2 | null; onClose: () => void; onDone: () => void }) {
  const [lvl, setLvl] = useState<number>(8.5);
  const { state, run, reset } = useCommand();
  useEffect(() => { setLvl(node?.txPowerDbm ?? 8.5); reset(); }, [node?.id]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!node) return null;
  const busy = state.phase === "sending" || state.phase === "sent";
  const cur = node.txPowerDbm ?? 8.5;
  const weak = weakLinkReasons(node);
  const l = node.live;
  return (
    <Shell open onClose={onClose} title={`TX power of ${nodeTitle(node)}`} description="Transmit power of the node's radio, pushed by the gateway. Higher reaches further but uses more current; change one node at a time and watch the missing readings.">
      <div className="rounded-md border p-3 text-sm space-y-0.5" data-testid="tx-link-now">
        <div className="flex justify-between"><span className="text-muted-foreground">Current TX power</span><span className="font-mono">{cur} dBm</span></div>
        <div className="flex justify-between"><span className="text-muted-foreground">Missing readings (24 h)</span><span className={`font-mono ${l && l.lossPct24h > WEAK_LOSS_PCT ? "text-red-600 font-semibold" : ""}`}>{l ? `${l.lossPct24h.toFixed(1)} % (${l.missedPackets24h})` : "—"}</span></div>
        {l && (l.gatewayOutagePackets24h ?? 0) > 0 && <div className="flex justify-between"><span className="text-muted-foreground">Missing while gateway offline (not counted)</span><span className="font-mono">{l.gatewayOutagePackets24h}</span></div>}
        <div className="flex justify-between"><span className="text-muted-foreground">RSSI</span><span className="font-mono">{rssiText(node.rssi)}</span></div>
        {weak.length > 0 && <p className="pt-1 text-xs text-amber-800">Weak link: {weak.join("; ")}. A higher TX power may help.</p>}
      </div>
      <fieldset className="space-y-1.5" disabled={busy}>
        <legend className="text-sm font-medium mb-1">TX power</legend>
        {TX_LEVELS.map((x) => (
          <label key={x} className="flex items-center gap-2 text-sm"><input type="radio" name="txpwr" value={x} checked={lvl === x} onChange={() => setLvl(x)} />{x} dBm{x === 8.5 ? " (default)" : ""}</label>
        ))}
      </fieldset>
      <CommandStatus state={state} />
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>{state.phase === "ok" ? "Close" : "Cancel"}</Button>
        <Button disabled={busy || lvl === cur || state.phase === "ok"} onClick={async () => { if (await run(() => v2.patchNode(node.id, { txPowerDbm: lvl as 8.5 | 11 | 13 | 15 }))) onDone(); }}>Set TX power</Button>
      </DialogFooter>
    </Shell>
  );
}

/** In-page confirmation (no window.confirm). */
export function RemoveDialog({ node, onClose, onDone }: { node: NodeV2 | null; onClose: () => void; onDone: () => void }) {
  const { state, run, reset } = useCommand();
  useEffect(() => { reset(); }, [node?.id]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!node) return null;
  const busy = state.phase === "sending" || state.phase === "sent";
  return (
    <AlertDialog open onOpenChange={(o) => { if (!o && !busy) onClose(); }}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Remove {nodeTitle(node)}?</AlertDialogTitle>
          <AlertDialogDescription>The gateway forgets this node ({node.mac}). Its history stays, but a pipeline design that uses it becomes invalid until the node is replaced or taken out of the design.</AlertDialogDescription>
        </AlertDialogHeader>
        <CommandStatus state={state} />
        <AlertDialogFooter>
          <Button variant="outline" disabled={busy} onClick={onClose}>{state.phase === "ok" ? "Close" : "Cancel"}</Button>
          <Button variant="destructive" disabled={busy || state.phase === "ok"} onClick={async () => { if (await run(() => v2.removeNode(node.id))) onDone(); }}>Remove node</Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

type Pair = { mac: string; code: string; type: "flow" | "rain" };

/** Pairing details entry shared by Add and Replace: camera QR or manual MAC + code. The code lives only in this component's state. */
function PairFields({ value, onChange, disabled, askType }: { value: Pair; onChange: (v: Pair) => void; disabled?: boolean; askType?: boolean }) {
  const [err, setErr] = useState("");
  const fromText = (t: string) => {
    try { const p: ParsedQr = parseQr(t); onChange({ mac: p.mac, code: formatCode(p.code), type: p.type }); setErr(""); }
    catch (e) { setErr(e instanceof QrError ? QR_ERROR_TEXT[e.kind] : "Could not read the code."); }
  };
  return (
    <div className="space-y-3">
      <QrScanner onText={fromText} />
      {err && <p role="alert" className="text-xs text-red-600">{err}</p>}
      <div className="grid gap-2"><Label htmlFor="pmac">MAC address</Label><Input id="pmac" placeholder="AA:BB:CC:DD:EE:FF" value={value.mac} disabled={disabled} onChange={(e) => onChange({ ...value, mac: e.target.value })} /></div>
      <div className="grid gap-2"><Label htmlFor="pcode">Pairing code</Label><Input id="pcode" autoComplete="off" placeholder="XXXX-XXXX-XXXX" value={value.code} disabled={disabled} onChange={(e) => onChange({ ...value, code: e.target.value })} /></div>
      {askType && (
        <div className="grid gap-2"><Label htmlFor="ptype">Type</Label>
          <select id="ptype" className="h-9 rounded-md border bg-input-background px-2 text-sm" value={value.type} disabled={disabled} onChange={(e) => onChange({ ...value, type: e.target.value as "flow" | "rain" })}><option value="flow">Flow node</option><option value="rain">Rain node</option></select></div>
      )}
    </div>
  );
}

function validPair(v: { mac: string; code: string }) {
  try { return { mac: normalizeMac(v.mac), code: normalizeCode(v.code) }; } catch { return null; }
}

export function AddNodeDialog({ open, gateway, tlsOk, onClose, onDone }: { open: boolean; gateway: GatewayV2 | null | undefined; tlsOk: boolean; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState<Pair>({ mac: "", code: "", type: "flow" });
  const [name, setName] = useState("");
  const { state, run, reset } = useCommand();
  useEffect(() => { if (open) { setF({ mac: "", code: "", type: "flow" }); setName(""); reset(); } }, [open]);   // eslint-disable-line react-hooks/exhaustive-deps
  const pair = validPair(f);
  const busy = state.phase === "sending" || state.phase === "sent";
  return (
    <Shell open={open} onClose={onClose} title="Add a node" description={tlsOk ? "Scan the sticker on the node, or type its MAC and pairing code." : undefined}>
      {!tlsOk ? <TlsRequired gateway={gateway} what="Adding a node" /> : (
        <>
          <PairFields value={f} onChange={setF} disabled={busy} askType />
          <div className="grid gap-2"><Label htmlFor="pname">Name (optional)</Label><Input id="pname" maxLength={23} value={name} onChange={(e) => setName(e.target.value)} disabled={busy} /></div>
          <CommandStatus state={state} />
        </>
      )}
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>{state.phase === "ok" || !tlsOk ? "Close" : "Cancel"}</Button>
        {tlsOk && <Button disabled={busy || !pair || state.phase === "ok"} onClick={async () => { if (pair && await run(() => v2.addNode({ ...pair, type: f.type, name: name.trim() || undefined, gatewayRef: gateway?.id }))) onDone(); }}>Add node</Button>}
      </DialogFooter>
    </Shell>
  );
}

export function ReplaceDialog({ node, gateway, tlsOk, onClose, onDone }: { node: NodeV2 | null; gateway: GatewayV2 | null | undefined; tlsOk: boolean; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState<Pair>({ mac: "", code: "", type: "flow" });
  const { state, run, reset } = useCommand();
  useEffect(() => { setF({ mac: "", code: "", type: node?.type ?? "flow" }); reset(); }, [node?.id]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!node) return null;
  const pair = validPair(f);
  const busy = state.phase === "sending" || state.phase === "sent";
  return (
    <Shell open onClose={onClose} title={`Replace hardware of ${nodeTitle(node)}`} description="The node keeps its number, name, calibration and pipeline position. Only the board changes.">
      {!tlsOk ? <TlsRequired gateway={gateway} what="Replacing a node" /> : (
        <>
          <PairFields value={f} onChange={setF} disabled={busy} />
          <CommandStatus state={state} />
        </>
      )}
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>{state.phase === "ok" || !tlsOk ? "Close" : "Cancel"}</Button>
        {tlsOk && <Button disabled={busy || !pair || state.phase === "ok"} onClick={async () => { if (pair && await run(() => v2.replaceNode(node.id, pair))) onDone(); }}>Replace</Button>}
      </DialogFooter>
    </Shell>
  );
}


function Spark({ pts, color, min, max, label }: { pts: Array<number | null>; color: string; min: number; max: number; label: string }) {
  const w = 300, h = 60;
  const xs = (i: number) => (pts.length <= 1 ? w / 2 : (i / (pts.length - 1)) * w);
  const ys = (v: number) => h - 4 - ((Math.min(Math.max(v, min), max) - min) / (max - min || 1)) * (h - 8);
  const d = pts.map((v, i) => (v === null ? null : `${xs(i).toFixed(1)},${ys(v).toFixed(1)}`)).filter(Boolean).join(" ");
  return <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-16 rounded border bg-muted/30" role="img" aria-label={label}><polyline points={d} fill="none" stroke={color} strokeWidth="2" /></svg>;
}

/** Radio-link trend (last 24 h, per hour): share of readings that never arrived and mean RSSI, plus what to check when the link is weak. */
export function LinkDialog({ node, onClose }: { node: NodeV2 | null; onClose: () => void }) {
  const [pts, setPts] = useState<LinkPoint[] | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    setPts(null); setErr("");
    if (!node) return;
    let live = true;
    v2.nodeLink(node.id, 24).then((r) => { if (live) setPts(r.points); }).catch((e) => { if (live) setErr(String(e?.message ?? e)); });
    return () => { live = false; };
  }, [node?.id]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!node) return null;
  const weak = weakLinkReasons(node);
  return (
    <Shell open onClose={onClose} title={`Radio link - ${nodeTitle(node)}`} description="Last 24 hours, one point per hour.">
      {err ? <p role="alert" className="text-sm text-red-600">{err}</p> : pts === null ? <p className="text-sm text-muted-foreground">Loading…</p> : pts.length === 0 ? <p className="text-sm text-muted-foreground">No readings in the last 24 hours.</p> : (
        <div className="space-y-3" data-testid="link-trend">
          <div><div className="text-xs text-muted-foreground mb-1">Missing readings (%), scale 0 to 50</div><Spark pts={pts.map((p) => p.lossPct)} color="#dc2626" min={0} max={50} label="missing readings per hour" /></div>
          <div><div className="text-xs text-muted-foreground mb-1">Signal (dBm), scale -100 to -40, higher is better</div><Spark pts={pts.map((p) => p.rssi)} color="#2563eb" min={-100} max={-40} label="signal strength per hour" /></div>
        </div>
      )}
      {weak.length > 0 && (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 space-y-1">
          <p className="font-semibold">Weak link: {weak.join("; ")}</p>
          <p>Check on the hardware: distance and walls between the node and the gateway, antenna orientation (the ESP32-C3 SuperMini antenna is weak; keep it away from metal and the buck converter), and the node's TX power setting.</p>
        </div>
      )}
      <p className="text-xs text-muted-foreground">A link counts as weak above {WEAK_LOSS_PCT} % missing readings or below {WEAK_RSSI_DBM} dBm.</p>
      <DialogFooter><Button variant="outline" onClick={onClose}>Close</Button></DialogFooter>
    </Shell>
  );
}
