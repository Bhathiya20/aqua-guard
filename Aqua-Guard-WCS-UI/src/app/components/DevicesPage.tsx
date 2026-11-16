// Devices page (registry view).
import { useState } from "react";
import { Link } from "react-router";
import { Cpu, Radio } from "lucide-react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { api, type DeviceStatus } from "../lib/api";
import { fmtAge, fmtClock, fmtTime, fmtUptime, usePolling } from "../lib/usePolling";
import { ConnectionBanner, deviceTone, EmptyState, Loading, PageHeader, Pill } from "./common";
import { DeviceManagement } from "./DeviceManagement";

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between gap-3 py-0.5"><span className="text-muted-foreground">{k}</span><span className="font-mono text-right">{v}</span></div>;
}

function DeviceCard({ d }: { d: DeviceStatus }) {
  const c = d.comm;
  const total = c ? c.received + c.lost : 0;
  const lossPct = c && total > 0 ? (c.lost / total) * 100 : null;
  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base flex items-center gap-2"><Cpu className="h-4 w-4" />{d.label}</CardTitle>
          <Pill tone={deviceTone(d.state)}>{d.state}</Pill>
        </div>
        <CardDescription>Last seen {fmtAge(d.ageSeconds)} · {fmtTime(d.lastSeen)}</CardDescription>
      </CardHeader>
      <CardContent className="text-xs">
        <Row k="Uptime" v={fmtUptime(d.uptimeSeconds)} />
        <Row k="Boot / session id" v={d.bootId !== null ? `${d.bootId.toString(16).toUpperCase()} (#${d.sessions})` : "—"} />
        <Row k="Last sequence" v={d.lastSeq ?? "—"} />
        <Row k="RSSI" v={d.rssi ? `${d.rssi} dBm` : "—"} />
        <Row k="Wi-Fi channel" v={d.channel ?? "—"} />
        <div className="my-2 border-t" />
        <Row k="Packets received" v={c?.received ?? "—"} />
        <Row k="Recovered (retransmit)" v={c?.recovered ?? "—"} />
        <Row k="Duplicates" v={c?.duplicates ?? "—"} />
        <Row k="Out of order" v={c?.outOfOrder ?? "—"} />
        <Row k="Missing now" v={c?.missing ?? "—"} />
        <Row k="Permanently lost" v={<span className={c && c.lost > 0 ? "text-red-600" : ""}>{c?.lost ?? "—"}</span>} />
        <Row k="Packet loss" v={lossPct !== null ? `${lossPct.toFixed(2)} %` : "—"} />
        <Row k="NACKs sent" v={c?.nacksSent ?? "—"} />
        <Row k="Rejected frames" v={c?.invalid ?? "—"} />
        <div className="my-2 border-t" />
        <Row k="Node retransmits" v={d.nodeRetransmits ?? "—"} />
        <Row k="Node dropped (window full)" v={d.nodeDropped ?? "—"} />
        <Row k="Channel re-discoveries" v={d.nodeRediscoveries ?? "—"} />
      </CardContent>
    </Card>
  );
}

export function DevicesPage() {
  const [tab, setTab] = useState<"manage" | "link">("manage");
  const dev = usePolling(api.getDevices, 3000);
  const [node, setNode] = useState(1);
  const hist = usePolling(() => api.getCommHistory(node, 120), 10000, [node]);
  const gw = dev.data?.gateway;

  if (dev.loading && !dev.data) return <Loading />;

  const histRows = (hist.data ?? []).map((r) => ({
    t: fmtClock(r.received_at),
    received: r.received, lost: r.lost, recovered: r.recovered, duplicates: r.duplicates,
  }));

  return (
    <div className="space-y-6">
      <PageHeader icon={<Radio className="h-6 w-6 text-blue-600" />} title="Legacy devices (v1)"
        subtitle="Read-only view of the retired v1 sensors and their ESP-NOW link quality"
        right={
          <div className="inline-flex rounded-md border p-0.5 text-sm" role="tablist">
            {(["manage", "link"] as const).map((t) => (
              <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
                className={`rounded px-3 py-1 ${tab === t ? "bg-blue-600 text-white" : "text-muted-foreground"}`}>{t === "manage" ? "Devices" : "Link monitoring"}</button>
            ))}
          </div>} />
      <div role="note" data-testid="legacy-banner" className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
        <b>Retired v1 devices - use Nodes.</b> This page only shows history; new sensors are paired and managed on the Nodes page.
        <Link to="/nodes" className="ml-auto font-semibold underline">Go to Nodes</Link>
      </div>
      {tab === "manage" && <DeviceManagement />}
      {tab === "link" && <>
      <ConnectionBanner error={dev.error} stale={dev.stale} onRetry={dev.refresh} />

      {gw && (
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between gap-2">
              <CardTitle className="text-base flex items-center gap-2"><Radio className="h-4 w-4" />Gateway {gw.gatewayId ?? ""}</CardTitle>
              <Pill tone={gw.online ? "green" : "red"}>{gw.status}</Pill>
            </div>
            <CardDescription>
              Heartbeat {fmtAge(gw.heartbeatAgeSeconds)}{gw.lastWillOffline ? " · broker reported last-will OFFLINE" : ""}
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-x-8 gap-y-0 sm:grid-cols-2 lg:grid-cols-3 text-xs">
            <Row k="Wi-Fi" v={gw.wifiUp === null ? "—" : gw.wifiUp ? "up" : "down"} />
            <Row k="Wi-Fi channel (ESP-NOW follows this)" v={gw.channel ?? "—"} />
            <Row k="Wi-Fi RSSI" v={gw.wifiRssi !== null ? `${gw.wifiRssi} dBm` : "—"} />
            <Row k="Channel changes seen" v={gw.channelChanges ?? "—"} />
            <Row k="Wi-Fi reconnects" v={gw.wifiReconnects ?? "—"} />
            <Row k="MQTT" v={gw.mqttUp === null ? "—" : gw.mqttUp ? "connected" : "down"} />
            <Row k="MQTT reconnects" v={gw.mqttReconnects ?? "—"} />
            <Row k="Buffer depth" v={gw.queueDepth ?? "—"} />
            <Row k="Buffer overflow (dropped)" v={<span className={gw.queueOverflow ? "text-red-600" : ""}>{gw.queueOverflow ?? "—"}</span>} />
            <Row k="Free heap" v={gw.freeHeap !== null ? `${Math.round(gw.freeHeap / 1024)} KiB` : "—"} />
            <Row k="Uptime" v={fmtUptime(gw.uptimeSeconds)} />
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {dev.data?.devices.map((d) => <DeviceCard key={d.device} d={d} />)}
      </div>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <CardTitle>Link quality history</CardTitle>
              <CardDescription>Cumulative counters reported by the gateway (resets when the gateway restarts)</CardDescription>
            </div>
            <select value={node} onChange={(e) => setNode(Number(e.target.value))} className="h-9 rounded-md border bg-background px-2 text-sm" aria-label="Node">
              <option value={1}>Node 1</option><option value={2}>Node 2</option><option value={3}>Rainwater node</option>
            </select>
          </div>
        </CardHeader>
        <CardContent>
          {histRows.length < 2 ? <EmptyState>Not enough link history yet.</EmptyState> : (
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={histRows} margin={{ left: 0, right: 8, top: 8, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                  <XAxis dataKey="t" tick={{ fontSize: 10 }} minTickGap={40} />
                  <YAxis tick={{ fontSize: 10 }} width={40} />
                  <Tooltip /><Legend />
                  <Line type="monotone" dataKey="recovered" stroke="#0ea5e9" dot={false} isAnimationActive={false} />
                  <Line type="monotone" dataKey="duplicates" stroke="#94a3b8" dot={false} isAnimationActive={false} />
                  <Line type="monotone" dataKey="lost" stroke="#dc2626" dot={false} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </CardContent>
      </Card>
      </>}
    </div>
  );
}
