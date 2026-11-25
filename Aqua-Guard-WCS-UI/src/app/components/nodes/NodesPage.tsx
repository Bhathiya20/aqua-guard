// Nodes page: table and cards of all nodes, the "..." actions menu and the Usage profile column.
import { useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { BatteryFull, BatteryWarning, ExternalLink, Gauge, MoreHorizontal, Pencil, Plus, Radio, RefreshCw, Replace, Ruler, Server, Trash2, Wrench, Zap } from "lucide-react";
import { Button } from "../ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "../ui/dropdown-menu";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../ui/table";
import { useAuth } from "../../lib/auth";
import { ageSince, fmtAge, fmtTime, fmtUptime, usePolling } from "../../lib/usePolling";
import { batteryImplausible, batteryText, calibrationText, rssiText, lpm, liters, nodeTitle, v2, weakLinkReasons, type GatewayV2, type NodeV2, type UsageLive } from "../../lib/v2";
import { ConnectionBanner, EmptyState, Loading, PageHeader, Pill, deviceTone, stateText } from "../common";
import { NodeUsageDialog } from "../usage/NodeUsageDialog";
import { AddNodeDialog, CalibrateDialog, LinkDialog, RecalcDialog, RemoveDialog, RenameDialog, ReplaceDialog, TxPowerDialog, txText } from "./NodeDialogs";

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between gap-3 py-0.5 text-sm"><span className="text-muted-foreground">{k}</span><span className="font-mono text-right break-all">{v}</span></div>;
}

export function GatewayCard({ gw, tls, mqtt }: { gw: GatewayV2; tls: boolean; mqtt: { host: string; port: number } }) {
  const url = gw.ip ? `http://${gw.ip}/` : null;
  return (
    <Card data-testid="gateway-card">
      <CardHeader className="pb-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-base flex items-center gap-2"><Server className="h-4 w-4" />Gateway {gw.gatewayId}</CardTitle>
          <div className="flex flex-wrap gap-1.5">
            <Pill tone={deviceTone(gw.state)}>{gw.state}</Pill>
            <Pill tone={gw.timeSynced ? "green" : gw.timeSynced === false ? "amber" : "gray"}>{gw.timeSynced ? "time synced" : gw.timeSynced === false ? "time not synced" : "time unknown"}</Pill>
            <Pill tone={tls ? "green" : "amber"}>{tls ? "MQTT TLS" : "MQTT without TLS"}</Pill>
          </div>
        </div>
        <CardDescription>Last heartbeat {gw.heartbeatAgeS === null ? "never" : fmtAge(Math.round(gw.heartbeatAgeS))} · firmware {gw.fw ?? "?"} · broker {mqtt.host}:{mqtt.port}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-x-8 sm:grid-cols-2">
        <div>
          <Row k="IP address" v={url ? <a className="underline inline-flex items-center gap-1 text-blue-700" href={url} target="_blank" rel="noreferrer">{gw.ip}<ExternalLink className="h-3 w-3" /></a> : "—"} />
          <Row k="Wi-Fi channel" v={gw.channel ?? "—"} />
          <Row k="Wi-Fi signal" v={rssiText(gw.rssi)} />
          <Row k="Uptime" v={fmtUptime(gw.uptimeS)} />
        </div>
        <div>
          <Row k="Frames received" v={gw.rx.frames} />
          <Row k="Rejected (bad tag / value / unknown MAC)" v={`${gw.rx.rejected} (${gw.rx.badTag} / ${gw.rx.badValue} / ${gw.rx.unknownMac})`} />
          <Row k="Queue depth / overflow" v={`${gw.queue.depth ?? "—"} / ${gw.queue.overflow ?? "—"}`} />
          <Row k="Dropped from queue" v={<span className={gw.rx.queueDropped ? "text-amber-700" : ""}>{gw.rx.queueDropped}</span>} />
        </div>
      </CardContent>
    </Card>
  );
}

function BatteryCell({ mv, lowMv = 6600 }: { mv: number | null; lowMv?: number }) {
  if (mv === null) return <span className="text-muted-foreground inline-flex items-center gap-1"><BatteryWarning className="h-3.5 w-3.5" />No battery</span>;
  if (batteryImplausible(mv)) return <span className="text-amber-700 inline-flex items-center gap-1" title="Not a plausible reading for a 2S Li-ion pack: check the battery divider wiring, or the node has no battery."><BatteryWarning className="h-3.5 w-3.5" />{batteryText(mv)} (implausible)</span>;
  const low = mv < lowMv;
  return <span className={`inline-flex items-center gap-1 ${low ? "text-red-600 font-semibold" : ""}`}>{low ? <BatteryWarning className="h-3.5 w-3.5" /> : <BatteryFull className="h-3.5 w-3.5" />}{batteryText(mv)}</span>;
}

interface Actions { onUsage?: (n: NodeV2) => void; usage?: Map<number, UsageLive>; onRename: (n: NodeV2) => void; onCalibrate: (n: NodeV2) => void; onRecalc?: (n: NodeV2) => void; onTx?: (n: NodeV2) => void; onService?: (n: NodeV2) => void; onReplace: (n: NodeV2) => void; onRemove: (n: NodeV2) => void; onTrend?: (n: NodeV2) => void }

function ActionButtons({ n, a, admin }: { n: NodeV2; a: Actions; admin: boolean }) {
  if (!admin) return <span className="text-xs text-muted-foreground">view only</span>;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="icon" variant="outline" className="h-8 w-8" aria-label={`Actions for ${nodeTitle(n)}`} data-testid={`row-menu-${n.number}`}><MoreHorizontal className="h-4 w-4" /></Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onSelect={() => a.onRename(n)}><Pencil className="mr-2 h-3.5 w-3.5" />Rename</DropdownMenuItem>
        {n.type === "flow" && <DropdownMenuItem onSelect={() => a.onCalibrate(n)}><Ruler className="mr-2 h-3.5 w-3.5" />Calibrate</DropdownMenuItem>}
        {n.type === "flow" && a.onRecalc && n.calibration?.earlierUsesOld && <DropdownMenuItem data-testid={`menu-recalc-${n.number}`} onSelect={() => a.onRecalc!(n)}><Ruler className="mr-2 h-3.5 w-3.5" />Recalculate history…</DropdownMenuItem>}
        {n.type === "flow" && a.onUsage && <DropdownMenuItem data-testid={`menu-usage-${n.number}`} onSelect={() => a.onUsage!(n)}><Gauge className="mr-2 h-3.5 w-3.5" />Usage settings</DropdownMenuItem>}
        {n.type === "flow" && a.onTx && <DropdownMenuItem data-testid={`menu-tx-${n.number}`} onSelect={() => a.onTx!(n)}><Zap className="mr-2 h-3.5 w-3.5" />TX power</DropdownMenuItem>}
        {a.onService && <DropdownMenuItem data-testid={`menu-service-${n.number}`} onSelect={() => a.onService!(n)}><Wrench className="mr-2 h-3.5 w-3.5" />{n.outOfService ? "Back in service" : "Out of service / maintenance"}</DropdownMenuItem>}
        <DropdownMenuItem onSelect={() => a.onReplace(n)}><Replace className="mr-2 h-3.5 w-3.5" />Replace</DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem className="text-red-700 focus:text-red-700" onSelect={() => a.onRemove(n)}><Trash2 className="mr-2 h-3.5 w-3.5" />Remove</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function missing(n: NodeV2, onTrend?: (n: NodeV2) => void) {
  const l = n.live;
  if (!l) return "—";
  const weak = weakLinkReasons(n);
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span className={l.lossPct24h > 10 ? "text-red-600 font-semibold" : ""} title="Readings that never arrived in the last 24 h (gaps in the node's sequence numbers), as a share of all readings it sent.">{l.lossPct24h.toFixed(1)} % missing <span className="text-muted-foreground">({l.missedPackets24h})</span></span>
      {(l.gatewayOutagePackets24h ?? 0) > 0 && <span className="text-xs text-muted-foreground" title="Readings missing while the gateway was offline or rebooting. They are not counted in the % above.">+{l.gatewayOutagePackets24h} during gateway outage</span>}
      {weak.length > 0 && <span title={weak.join("; ")}><Pill tone="amber">weak link</Pill></span>}
      {onTrend && <button type="button" className="text-xs underline text-blue-700" onClick={() => onTrend(n)}>trend</button>}
    </span>
  );
}

/** The state pill; a node in maintenance keeps its "out of service" flag visible even while the gateway is offline (the API then reports "unknown"). */
function StateCell({ n }: { n: NodeV2 }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <Pill tone={deviceTone(n.state)}>{stateText(n.state)}</Pill>
      {n.outOfService && n.state !== "out_of_service" && <Pill tone="gray">out of service</Pill>}
    </span>
  );
}

function UsageProfileCell({ u }: { u?: UsageLive }) {
  if (!u) return <span className="text-muted-foreground">Default</span>;
  return <span title={u.summary}>{u.demo ? <Pill tone="violet">demo limits</Pill> : u.presetLabel}{u.custom && !u.demo ? <span className="text-muted-foreground"> (edited)</span> : null}</span>;
}

export function NodesTable({ nodes, a, admin }: { nodes: NodeV2[]; a: Actions; admin: boolean }) {
  const [tech, setTech] = useState(false);                     // progressive disclosure: the first view has the columns people read; the technical ones are one click away
  return (
    <>
      <div className="hidden md:flex justify-end pb-2"><Button size="sm" variant="outline" aria-expanded={tech} onClick={() => setTech((t) => !t)} data-testid="nodes-more-columns">{tech ? "Fewer columns" : "More columns (MAC, battery, signal, calibration…)"}</Button></div>
      <div className="hidden md:block rounded-lg border overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>#</TableHead>{tech && <TableHead>Type</TableHead>}<TableHead>Name</TableHead>{tech && <TableHead>MAC</TableHead>}<TableHead>State</TableHead><TableHead>Last seen</TableHead>
              <TableHead className="text-right">Flow now</TableHead><TableHead className="text-right">Today</TableHead>{tech && <><TableHead>Battery</TableHead><TableHead>RSSI</TableHead><TableHead>TX power</TableHead>
              <TableHead>Calibration</TableHead><TableHead>Usage profile</TableHead><TableHead>Missing readings (24 h)</TableHead></>}<TableHead>Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {nodes.map((n) => (
              <TableRow key={n.id} data-testid={`node-row-${n.number}`}>
                <TableCell className="font-semibold">{n.type === "rain" ? "Rain" : "Node"} {n.number}</TableCell>
                {tech && <TableCell data-testid={`type-${n.number}`}><Pill tone={n.type === "rain" ? "blue" : "gray"}>{n.type === "rain" ? "Rain" : "Flow"}</Pill></TableCell>}
                <TableCell>{n.name || <span className="text-muted-foreground">—</span>}</TableCell>
                {tech && <TableCell className="font-mono text-xs">{n.mac}</TableCell>}
                <TableCell><StateCell n={n} /></TableCell>
                <TableCell className="text-xs">{n.lastSeen ? fmtAge(n.live?.ageS !== null && n.live?.ageS !== undefined ? Math.round(n.live.ageS) : ageSince(n.lastSeen)) : "never"}<div className="text-muted-foreground">{fmtTime(n.lastSeen)}</div></TableCell>
                <TableCell className="text-right font-mono">{n.live ? `${lpm(n.live.flowLpm)} L/min` : "—"}</TableCell>
                <TableCell className="text-right font-mono">{n.live ? liters(n.live.todayLiters) : "—"}</TableCell>
                {tech && <>
                <TableCell><BatteryCell mv={n.batteryMv} /></TableCell>
                <TableCell className="font-mono text-xs">{n.state === "online" ? rssiText(n.rssi) : "—"}</TableCell>
                <TableCell className="font-mono text-xs" data-testid={`tx-${n.number}`}>{n.type === "rain" ? "—" : txText(n)}</TableCell>
                <TableCell className="font-mono text-xs">{n.type === "rain" ? "—" : calibrationText(n.pulsesPerLiter, n.calibration)}</TableCell>
                <TableCell className="text-xs" data-testid={`usage-profile-${n.number}`}>{n.type === "rain" ? "—" : <UsageProfileCell u={a.usage?.get(n.id)} />}</TableCell>
                <TableCell className="text-xs">{missing(n, a.onTrend)}</TableCell>
                </>}
                <TableCell><ActionButtons n={n} a={a} admin={admin} /></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <div className="md:hidden space-y-3">
        {nodes.map((n) => (
          <Card key={n.id} data-testid={`node-card-${n.number}`}>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between gap-2">
                <CardTitle className="text-base">{nodeTitle(n)}</CardTitle>
                <StateCell n={n} />
              </div>
              <CardDescription className="font-mono text-xs">{n.mac}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              <div>
                <Row k="Type" v={n.type === "rain" ? "Rain" : "Flow"} />
                <Row k="Flow now" v={n.live ? `${lpm(n.live.flowLpm)} L/min` : "—"} />
                <Row k="Today" v={n.live ? liters(n.live.todayLiters) : "—"} />
                <Row k="Last seen" v={fmtTime(n.lastSeen)} />
                <Row k="Battery" v={<BatteryCell mv={n.batteryMv} />} />
                <Row k="RSSI" v={n.state === "online" ? rssiText(n.rssi) : "—"} />
                <Row k="TX power" v={n.type === "rain" ? "—" : txText(n)} />
                <Row k="Calibration" v={n.type === "rain" ? "—" : calibrationText(n.pulsesPerLiter, n.calibration)} />
                {n.type === "flow" && <Row k="Usage profile" v={<UsageProfileCell u={a.usage?.get(n.id)} />} />}
                <Row k="Missing (24 h)" v={missing(n, a.onTrend)} />
              </div>
              <ActionButtons n={n} a={a} admin={admin} />
            </CardContent>
          </Card>
        ))}
      </div>
    </>
  );
}

export function NodesPage() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const gws = usePolling(() => v2.gateways(), 3000);
  const nodes = usePolling(() => v2.nodes(), 2000);
  const nearby = usePolling(() => v2.nearby(), 5000);
  const [rename, setRename] = useState<NodeV2 | null>(null);
  const [calib, setCalib] = useState<NodeV2 | null>(null);
  const [txn, setTxn] = useState<NodeV2 | null>(null);
  const [replace, setReplace] = useState<NodeV2 | null>(null);
  const [remove, setRemove] = useState<NodeV2 | null>(null);
  const [recalc, setRecalc] = useState<NodeV2 | null>(null);
  const [adding, setAdding] = useState(false);
  const [trend, setTrend] = useState<NodeV2 | null>(null);
  const [usageNode, setUsageNode] = useState<NodeV2 | null>(null);
  const usageLive = usePolling(() => v2.usageLive(), 4000);
  const usageBy = new Map((usageLive.data?.nodes ?? []).map((u) => [u.nodeRef, u]));

  const toggleService = async (n: NodeV2) => {
    try { await v2.setOutOfService(n.id, !n.outOfService); toast.success(n.outOfService ? `${nodeTitle(n)} is back in service` : `${nodeTitle(n)} is out of service: no offline alerts`); nodes.refresh(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Could not change the service state"); }
  };
  const [gwSel, setGwSel] = useState<number | null>(null);
  const allGw = gws.data?.gateways ?? [];
  // the selected gateway; by default the online one (never just "the last one in the table")
  const gw = allGw.find((g) => g.id === gwSel) ?? allGw.find((g) => g.state === "online") ?? allGw[0] ?? null;
  const tlsOk = !!gws.data?.mqtt.remoteAddAllowed;
  const list = (nodes.data?.nodes ?? []).filter((n) => n.state !== "removed");
  const after = () => { nodes.refresh(); gws.refresh(); };

  return (
    <div className="space-y-5">
      <PageHeader icon={<Radio className="h-6 w-6 text-blue-600" />} title="Nodes" subtitle="The gateway keeps the list of paired nodes; this page mirrors it and sends signed commands."
        right={<div className="flex gap-2"><Button variant="outline" size="sm" onClick={after}><RefreshCw className="mr-1.5 h-3.5 w-3.5" />Refresh</Button>
          {admin && <Button size="sm" variant="outline" asChild><Link to="/nodes/calibrate" data-testid="calibrate-nodes"><Ruler className="mr-1.5 h-3.5 w-3.5" />Calibrate nodes</Link></Button>}
          <Button size="sm" variant="outline" asChild><Link to="/usage-settings" data-testid="usage-settings-link"><Gauge className="mr-1.5 h-3.5 w-3.5" />Usage settings</Link></Button>
          {admin && <Button size="sm" onClick={() => setAdding(true)}><Plus className="mr-1.5 h-3.5 w-3.5" />Add node</Button>}</div>} />

      <ConnectionBanner error={gws.error || nodes.error} stale={gws.stale || nodes.stale} onRetry={after} />
      {gws.data && !gws.data.commandsEnabled && admin && (
        <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">The site command key is not set on the backend (AQUAGUARD_CMD_KEY), so rename, calibrate and remove are disabled.</div>
      )}

      {allGw.length > 1 && (
        <div className="flex flex-wrap items-center gap-2 text-sm" data-testid="gateway-selector">
          <label htmlFor="gwsel" className="font-medium">Gateway</label>
          <select id="gwsel" className="rounded-md border bg-background px-2 py-1.5" value={gw?.id ?? ""} onChange={(e) => setGwSel(Number(e.target.value))}>
            {allGw.map((g) => <option key={g.id} value={g.id}>{g.gatewayId} ({g.state}, {g.nodes} nodes)</option>)}
          </select>
          <span className="text-muted-foreground">{allGw.length} gateways known · new nodes are added to the selected one</span>
        </div>
      )}
      {gws.loading ? <Loading label="Loading gateway…" /> : gw ? <GatewayCard gw={gw} tls={gws.data!.mqtt.tls} mqtt={gws.data!.mqtt} />
        : <Card><CardContent><EmptyState title="No gateway" action={{ to: "/", label: "Back to Overview" }}>No gateway has reported yet. Power the gateway and check that it reaches the MQTT broker.</EmptyState></CardContent></Card>}

      <section className="space-y-2">
        <h2 className="text-lg font-semibold">Paired nodes</h2>
        {nodes.loading ? <Loading label="Loading nodes…" /> : list.length === 0
          ? <Card><CardContent><EmptyState>No nodes are paired yet.{admin ? " Use “Add node”." : ""}</EmptyState></CardContent></Card>
          : allGw.length > 1
            ? allGw.map((g) => {
              const mine = list.filter((n) => n.gatewayRef === g.id);
              return (
                <div key={g.id} className="space-y-2" data-testid={`gateway-group-${g.gatewayId}`}>
                  <h3 className="text-sm font-semibold text-muted-foreground">Gateway {g.gatewayId} · {g.state} · {mine.length} node{mine.length === 1 ? "" : "s"}</h3>
                  {mine.length === 0 ? <Card><CardContent><EmptyState>No nodes on this gateway.</EmptyState></CardContent></Card>
                    : <NodesTable nodes={mine} admin={admin} a={{ onRename: setRename, onCalibrate: setCalib, onRecalc: admin ? setRecalc : undefined, onTx: setTxn, onService: toggleService, onReplace: setReplace, onRemove: setRemove, onTrend: setTrend, onUsage: setUsageNode, usage: usageBy }} />}
                </div>);
            })
            : <NodesTable nodes={list} admin={admin} a={{ onRename: setRename, onCalibrate: setCalib, onRecalc: admin ? setRecalc : undefined, onTx: setTxn, onService: toggleService, onReplace: setReplace, onRemove: setRemove, onTrend: setTrend, onUsage: setUsageNode, usage: usageBy }} />}
      </section>

      <section className="space-y-2">
        <h2 className="text-lg font-semibold">Nodes nearby</h2>
        <p className="text-sm text-muted-foreground">Unpaired nodes the gateway currently hears. This list is read-only: add them from the gateway page or with “Add node”.</p>
        {(nearby.data?.nearby.length ?? 0) === 0
          ? <Card><CardContent><EmptyState>No unpaired node in range.</EmptyState></CardContent></Card>
          : <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{nearby.data!.nearby.map((x) => (
            <Card key={x.mac}><CardContent className="py-3 text-sm space-y-0.5">
              <div className="font-mono">{x.mac}</div>
              <div className="text-muted-foreground">{x.type === "rain" ? "Rain node" : "Flow node"} · {rssiText(x.rssi)} · ch {x.channel ?? "—"}</div>
              <div className="text-xs text-muted-foreground">heard {fmtTime(x.lastSeen)}</div>
            </CardContent></Card>))}</div>}
      </section>

      <AddNodeDialog open={adding} gateway={gw} tlsOk={tlsOk} onClose={() => setAdding(false)} onDone={after} />
      <RenameDialog node={rename} onClose={() => setRename(null)} onDone={after} />
      <CalibrateDialog node={calib} onClose={() => setCalib(null)} onDone={after} />
      <TxPowerDialog node={txn} onClose={() => setTxn(null)} onDone={after} />
      <ReplaceDialog node={replace} gateway={allGw.find((g) => g.id === replace?.gatewayRef) ?? gw} tlsOk={tlsOk} onClose={() => setReplace(null)} onDone={after} />
      <NodeUsageDialog node={usageNode} onClose={() => setUsageNode(null)} onDone={usageLive.refresh} />
      <LinkDialog node={trend} onClose={() => setTrend(null)} />
      <RecalcDialog node={recalc} onClose={() => setRecalc(null)} onDone={after} />
      <RemoveDialog node={remove} onClose={() => setRemove(null)} onDone={after} />
    </div>
  );
}
