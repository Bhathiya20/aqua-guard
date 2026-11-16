import { useState } from "react";
import { toast } from "sonner";
import { Cpu, Droplets, Trash2 } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { api, type RegistryDevice } from "../lib/api";
import { fmtAge, fmtTime, usePolling } from "../lib/usePolling";
import { ConnectionBanner, deviceTone, Loading, Pill } from "./common";

const statusTone = (s: string) => (s === "paired" ? "green" : s === "pairing" ? "amber" : s === "unpaired" ? "gray" : "blue");
const statusLabel: Record<string, string> = { provisioned: "ready to pair", pairing: "pairing…", paired: "paired", unpaired: "unpaired" };

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between gap-3 py-0.5"><span className="text-muted-foreground">{k}</span><span className="font-mono text-right break-all">{v}</span></div>;
}

// ------------------------------------------------------------------ one device
function DeviceCard({ d, admin, onChanged }: { d: RegistryDevice; admin: boolean; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(d.displayName);
  const [ppl, setPpl] = useState(d.calibrationPulsesPerLiter?.toString() ?? "");
  const [busy, setBusy] = useState(false);
  const [newCode, setNewCode] = useState("");
  const detail = usePolling(() => api.getRegistryDevice(d.uid), 4000, [d.uid, open]);
  const cur = open && detail.data ? detail.data : d;
  const Icon = d.type === "rain" ? Droplets : Cpu;

  const save = async () => {
    const calib = ppl.trim() === "" ? null : Number(ppl);
    if (calib !== null && (!Number.isFinite(calib) || calib < 10 || calib > 6000)) { toast.error("Calibration must be 10 – 6000 pulses per litre"); return; }
    setBusy(true);
    try { await api.updateRegistryDevice(d.uid, { displayName: name.trim(), ...(d.type === "flow" ? { calibrationPulsesPerLiter: calib } : {}) }); toast.success("Saved"); onChanged(); detail.refresh(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Save failed"); }
    finally { setBusy(false); }
  };
  const unpair = async () => {
    if (!window.confirm(`Unpair "${d.displayName}"? It will stop sending data and needs a NEW pairing code to join again. Historical readings are kept.`)) return;
    setBusy(true);
    try { const r = await api.unpairDevice(d.uid); toast.success(r.commandSent ? "Device unpaired" : "Unpaired in the database; the gateway will drop it when it reconnects"); onChanged(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Unpair failed"); }
    finally { setBusy(false); }
  };
  const issueCode = async () => {
    try { const r = await api.newPairingCode(d.uid); setNewCode(r.pairingCode); onChanged(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Could not create a code"); }
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base flex items-center gap-2"><Icon className="h-4 w-4" />{d.displayName}</CardTitle>
          <div className="flex items-center gap-1">
            <Pill tone={statusTone(d.pairingStatus)}>{statusLabel[d.pairingStatus] ?? d.pairingStatus}</Pill>
            {d.pairingStatus === "paired" && <Pill tone={deviceTone(d.state)}>{d.state}</Pill>}
          </div>
        </div>
        <CardDescription className="font-mono text-xs">{d.deviceId}{d.legacy ? " · built-in" : ""}{d.slot ? ` · slot ${d.slot}` : ""}</CardDescription>
      </CardHeader>
      <CardContent className="text-xs space-y-2">
        {d.pairingStatus === "paired" && d.type === "flow" && (
          <div className="rounded-md bg-muted p-2">
            {d.latest && d.latest.flowRate !== undefined ? (
              <div className="grid grid-cols-2 gap-1">
                <div><div className="text-muted-foreground">Flow</div><div className="text-lg font-semibold">{d.latest.flowRate.toFixed(2)} <span className="text-xs">L/min</span></div></div>
                <div><div className="text-muted-foreground">Measured volume</div><div className="text-lg font-semibold">{d.latest.totalLiters.toFixed(2)} <span className="text-xs">L</span></div></div>
              </div>
            ) : <span className="text-muted-foreground">No measurements received yet.</span>}
          </div>
        )}
        {d.pairingStatus === "pairing" && <p className="text-amber-700">Waiting for the device to complete the secure handshake…{d.pairingError ? ` ${d.pairingError}` : ""}</p>}
        {d.pairingStatus === "provisioned" && d.pairingError && <p className="text-red-600">{d.pairingError}</p>}
        <Row k="Last seen" v={d.lastSeen ? `${fmtAge(d.ageSeconds)} · ${fmtTime(d.lastSeen)}` : "never"} />
        {open && (
          <div className="space-y-2 border-t pt-2">
            <Row k="Application ID (uid)" v={cur.uid} />
            <Row k="Hardware MAC" v={cur.mac ?? "—"} />
            <Row k="Firmware" v={cur.firmwareVersion ?? "—"} />
            <Row k="Paired" v={cur.pairedAt ? `${fmtTime(cur.pairedAt)}${cur.pairedBy ? ` by ${cur.pairedBy}` : ""}` : "—"} />
            <Row k="Last identity check" v={cur.lastVerifiedAt ? fmtTime(cur.lastVerifiedAt) : "—"} />
            <Row k="Boot sessions seen" v={cur.sessions} />
            {cur.latest?.rssi != null && <Row k="Signal / channel" v={`${cur.latest.rssi} dBm · ch ${cur.latest.channel ?? "—"}`} />}
            {cur.latest?.pulsesPerLiter && <Row k="Reported calibration" v={`${cur.latest.pulsesPerLiter} pulses/L`} />}
            {admin ? (
              <div className="space-y-2 rounded-md border p-2">
                <label className="block">Friendly name<Input value={name} onChange={(e) => setName(e.target.value)} maxLength={60} /></label>
                {d.type === "flow" && (
                  <label className="block">Calibration override (pulses per litre, blank = use the device's value)
                    <Input value={ppl} onChange={(e) => setPpl(e.target.value)} inputMode="decimal" placeholder="e.g. 450" />
                  </label>
                )}
                <Button size="sm" onClick={save} disabled={busy}>Save</Button>
              </div>
            ) : <p className="text-muted-foreground">Retired v1 device: read-only. Use the Nodes page.</p>}
            {cur.history && cur.history.length > 0 && (
              <details><summary className="cursor-pointer text-muted-foreground">Pairing history</summary>
                <ul className="mt-1 space-y-0.5">{cur.history.map((h, i) => (
                  <li key={i}><span className={h.ok ? "text-emerald-700" : "text-red-600"}>{h.ok ? "✓" : "✗"}</span> {fmtTime(h.ts)} · {h.kind.replace(/_/g, " ")} — {h.detail}</li>
                ))}</ul>
              </details>
            )}
          </div>
        )}
        {newCode && (
          <div className="rounded-md border border-emerald-300 bg-emerald-50 p-2" role="status">
            New pairing code (shown once): <b className="font-mono text-sm">{newCode}</b>
            <div className="text-muted-foreground">QR text: AGP1:{d.deviceId}:{newCode}</div>
          </div>
        )}
        <div className="flex flex-wrap gap-2 pt-1">
          <Button variant="outline" size="sm" onClick={() => setOpen(!open)}>{open ? "Hide details" : "Details"}</Button>
          {admin && !d.legacy && d.pairingStatus === "paired" && <Button variant="outline" size="sm" onClick={unpair} disabled={busy}><Trash2 className="mr-1 h-3 w-3" />Unpair</Button>}
          {admin && !d.legacy && (d.pairingStatus === "unpaired" || d.pairingStatus === "provisioned") && <Button variant="outline" size="sm" onClick={issueCode}>New pairing code</Button>}
        </div>
      </CardContent>
    </Card>
  );
}

// ------------------------------------------------------------------ section
export function DeviceManagement() {
  const reg = usePolling(api.getRegistry, 3000);

  if (reg.loading && !reg.data) return <Loading />;
  const devices = reg.data?.devices ?? [];
  const paired = devices.filter((d) => d.pairingStatus === "paired").length;
  return (
    <div className="space-y-4">
      <ConnectionBanner error={reg.error} stale={reg.stale} onRetry={reg.refresh} />
      <p className="text-sm text-muted-foreground">{devices.length} registered · {paired} paired. Read-only: pair and manage sensors on the Nodes page.</p>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {devices.map((d) => <DeviceCard key={d.uid} d={d} admin={false} onChanged={reg.refresh} />)}
      </div>
    </div>
  );
}
