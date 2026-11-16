// Protocol-v2 API: gateways, numbered nodes, signed node commands, pipelines, live zone analysis, summary.
import { API_BASE, request } from "./api";

export { API_BASE };

// ---------------------------------------------------------------- gateways and nodes
export interface GatewayV2 {
  id: number; gatewayId: string; fw: string | null; mode: string | null; ip: string | null; channel: number | null; rssi: number | null;
  state: "online" | "offline" | "unknown"; heartbeatAgeS: number | null; timeStatus: string; timeSynced: boolean | null;
  tls: boolean; tlsVerified: boolean; remoteAdd: boolean; nodes: number;
  rx: { frames: number; rejected: number; badTag: number; badValue: number; unknownMac: number; queueDropped: number };
  queue: { depth: number | null; overflow: number | null }; uptimeS: number | null; freeHeap: number | null; registryRev: number | null; registryAt: string | null;
}
export interface GatewaysResponse {
  gateways: GatewayV2[];
  mqtt: { tls: boolean; host: string; port: number; remoteAddAllowed: boolean };
  commandsEnabled: boolean;
}
export interface NodeLive {
  flowLpm: number | null; lastFlowLpm: number | null; ageS: number | null; cumPulses: number | null;
  todayLiters: number; weekLiters: number; missedPackets24h: number; receivedPackets24h: number; lossPct24h: number; gatewayOutagePackets24h?: number; gatewayOutageMin24h?: number;
}
export interface NodeV2 {
  id: number; gatewayRef: number; gatewayId?: string | null; type: "flow" | "rain"; number: number; label: string; name: string; mac: string;
  state: "online" | "offline" | "unknown" | "pending" | "removed" | "out_of_service"; registryState: string; outOfService?: boolean;
  batteryMv: number | null; rssi: number | null; lastSeen: string | null; pulsesPerLiter: number | null; txReduce: boolean; txPowerDbm?: number;
  addedEpoch: number | null; live: NodeLive | null; hardware: Array<{ mac: string; firstSeen: string; lastSeen: string }>;
}
export interface NearbyNode { mac: string; type: "flow" | "rain"; rssi: number | null; channel: number | null; lastSeen: string }
export type CommandStatus = "queued" | "sent" | "ok" | "error" | "timeout" | string;
export interface CommandAck { commandId: number; status: CommandStatus; result: string | null; number: number | null }
export interface CommandInfo { id: number; op: string; status: CommandStatus; result: string | null; rc: number | null; number: number | null; created_at: string; resolved_at: string | null }

// ---------------------------------------------------------------- pipeline document
export type PartKind = "source" | "pipe" | "elbow" | "valve" | "tee" | "cross" | "outlet" | "cap" | "node";
export interface DocPart {
  id: string; kind: PartKind; x?: number; y?: number; rot?: number; label?: string;
  lengthM?: number; diameterMm?: number; outletType?: "tap" | "shower" | "garden" | "tank";
  nodeType?: "flow" | "rain"; number?: number; nodeRef?: number; pipe?: string | null; pos?: number; dir?: "forward" | "reverse"; name?: string;
  [k: string]: unknown;
}
export interface DocLink { id: string; from: { part: string; port: string }; to: { part: string; port: string } }
export interface PipelineDoc { schema: 1; name: string; parts: DocPart[]; links: DocLink[]; zoneSettings?: Record<string, unknown>; [k: string]: unknown }

export interface ValidationMessage { severity: "error" | "warning" | "info"; code: string; partId: string | null; message: string }
export interface ZoneInfo {
  id: string; name: string; class: "strong" | "weak" | "not_monitored" | string; reason: string; equation: string;
  inMeter: string | null; inNode?: number | null; outNodes?: number[]; outMeters: string[]; outlets: number; pipes: string[]; parts: string[]; outletIds: string[];
  suggestions: Array<Record<string, unknown>>;
}
export interface Coverage { text: string; [k: string]: unknown }
export interface Suggestion { text?: string; message?: string; pipe?: string; partId?: string; [k: string]: unknown }
export interface ValidationSummary {
  valid: boolean; messages: ValidationMessage[]; partial?: boolean; coverage?: Coverage; zones?: ZoneInfo[]; suggestions?: Suggestion[];
}
export interface AvailableNode { type: string; number: number; name: string; label: string; nodeRef: number; gatewayId?: string; outOfService?: boolean }
export interface PipelineFull extends ValidationSummary {
  id: number; name: string; activeVersion: number | null; version: number; note: string; savedValid: boolean; createdAt: string; createdBy: string;
  doc: PipelineDoc; availableNodes: AvailableNode[];
}
export interface PipelineListItem { id: number; name: string; preset?: string | null; activeVersion: number | null; versions: number; updatedAt: string | null; demo?: boolean; demoUntil?: number | null }
export interface VersionItem { version: number; note: string; valid: boolean; createdBy: string; createdAt: string; active: boolean }

export type ZoneStatus = "normal" | "suspected" | "confirmed" | "not_monitored" | "incomplete" | "calibrating";
export interface RuleProgressInfo { text: string; value: number | null; needed: number | null; unit: string; counting: boolean }
export interface ZoneSettings {
  thresholdLpm: number; tolerancePct: number; suspectMin: number; confirmMin: number; resolveMin: number; bucketS: number; nightStart: string; nightEnd: string;
  continuousSuspectMin: number; continuousConfirmMin: number; tz: string; tzOffsetMin: number; demo: boolean;
  windowS?: number; xPlusLpm?: number; xMinusLpm?: number; tLeakS?: number;      // responsive balance (WLDS)
}
/** Responsive balance of a strong zone: D = Q_in - sum(Q_out), signed.  paused = a meter has no complete data. */
export type BalanceState = "normal" | "suspicious" | "possible_leak" | "inconsistent" | "paused";
export interface Wlds { state: BalanceState; tdS: number; tLeakS: number; dLpm: number | null; qInLpm: number | null; qOutLpm: number | null; xPlusLpm: number; xMinusLpm: number; windowS: number; completeWindows: number; finalWindows: number }
export interface ZoneThresholds { zone: string; strong: boolean; xPlusLpm: number; xMinusLpm: number; tLeakS: number; windowS: number; defaults: { xPlusLpm: number; xMinusLpm: number; tLeakS: number; demoTLeakS: number }; custom: boolean; source: string | null; updatedAt: string | null; updatedBy: string | null }
export interface LearnResult { windows: number; totalWindows: number; windowS: number; meanDLpm: number; sdDLpm: number; meanFlowLpm: number; floorLpm: number; xPlusLpm: number; xMinusLpm: number; warnings: string[]; usable: boolean }
export interface LearnSession { session: null | { id: number; status: "running" | "done" | "cancelled"; startedAt: number; endedAt: number | null; createdBy: string | null; progress?: LearnResult; result?: LearnResult } }
export interface ZoneNodeInfo {
  partId: string; number: number; label: string; name: string; nodeRef: number | null; state: "online" | "offline" | "out_of_service" | "missing";
  lastSeen: string | null; flowLpm: number | null; ageS: number | null;
}
export interface LeakNodeRow extends ZoneNodeInfo { zones: Array<{ id: string; name: string; role: "in" | "out" }> }
export interface LiveZone {
  inflow?: ZoneNodeInfo[]; outflow?: ZoneNodeInfo[]; outletCount?: number;
  id: string; name: string; class: string; status: ZoneStatus; level: number; reason: string; equation: string;
  inMeter: string | null; outMeters: string[]; outlets: string[]; outletIds: string[]; pipes: Array<{ id: string; fraction: number }>; parts: string[];
  balance: { qIn: number; qOut: number; unaccounted: number; thresholdLpm: number; bucketTs: number; confidence: string } | null;
  meterFaultHint: string | null; suggestions: Array<Record<string, unknown>>; parent: string | null;
  rules: Array<{ rule: string; level: number; clear: boolean; reason: string; progress?: RuleProgressInfo | null }>; watching?: boolean; calibrating?: string[]; settings?: ZoneSettings; balanceState?: BalanceState | null; wlds?: Wlds | null; completeBuckets: number; finalBuckets: number; nodesMissing: string[]; nodesOffline?: string[]; nodesOutOfService?: string[];
}
export interface PipelineStatus {
  pipelineId: number; name: string; version: number; demo?: boolean; demoUntil?: number | null; zones: LiveZone[]; nodes?: LeakNodeRow[]; coverage: Coverage; suggestions: Suggestion[];
  live: Record<string, { flowLpm: number | null; measuredAt: string | null; fresh: boolean }>;
}
export interface ZoneSeries { zone: string; bucketS: number; buckets: Array<{ ts: number; qInL: number | null; qOutL: number | null; unaccountedL: number | null; complete: boolean; confidence: string; state: string }> }
export interface ZoneLeak { id: number; zone_id: string; pipeline_version: number; severity: string; state: string; message: string; active: boolean; created_at: string; cleared_at: string | null; updated_at: string | null }

// ---------------------------------------------------------------- summary / alerts
export interface SummaryV2 {
  gateway: GatewayV2 | null;
  gateways?: { total: number; online: number };
  nodes: { total: number; online: number; offline: number };
  total: { basis: "none" | "source_meter" | "sum_of_nodes"; flowLpm: number | null; todayLiters: number | null; weekLiters: number | null; node: number | null; pipelineId: number | null };
  pipeline: { id: number; name: string } | null;
  leaks: { suspected: number; confirmed: number }; otherAlerts: number;
  battery: { withBattery: number; noBattery: number; low: Array<{ number: number; name: string; mv: number }>; implausible?: Array<{ number: number; name: string; mv: number }>; lowLimitMv: number };
}
export interface AlertV2 {
  id: number; key: string; kind: string; severity: string; message: string; active: boolean; createdAt: string; clearedAt: string | null;
  acknowledgedBy: string | null; acknowledgedAt: string | null; nodeRef: number | null; zone: string | null; pipelineId: number | null; pipelineVersion: number | null; state: string | null; demo?: boolean;
}
// ---------------------------------------------------------------- resolved history (Part 4) and per-node usage profiles (Part 1)
export interface HistoryFilter { from?: string; to?: string; node?: number | ""; zone?: string; type?: string; reason?: string; limit?: number }
export interface HistoryEvent {
  id: number; type: string; typeLabel: string; severity: string; message: string; demo: boolean;
  node: { ref: number; number: number; type: string; name: string; label: string } | null; zone: { id: string; name: string } | null; pipelineId: number | null;
  startedAt: string; suspectedAt: string | null; confirmedAt: string | null; resolvedAt: string | null; durationS: number | null;
  peakLpm: number | null; peakKind: "D" | "flow"; lostL: number | null; resolvedBy: string; reason: string | null; reasonLabel: string | null; note: string;
}
export interface EventSeries { kind: "balance" | "flow" | "none"; unit: string; points: Array<{ t: number; qIn?: number; qOut?: number; d?: number; flow?: number }> }
export const RESOLVE_REASONS: Record<string, string> = { fixed_leak: "Fixed leak", false_alarm: "False alarm", test: "Test", tap_left_open: "Tap left open", sensor_fault: "Sensor fault", other: "Other" };

export interface UsageSchedule { days: number[]; start: string; end: string }
export interface UsageProfile {
  preset: string; tLimS: number; tMaxS: number; flowOnLpm: number; flowOffLpm: number; noFlowS: number; quietStart: string; quietEnd: string; quietConfirmS: number; allow24h: boolean; schedule: UsageSchedule[];
}
export interface UsagePresetInfo { key: string; label: string; tLimS?: number; tMaxS?: number }
export type UsageStatus = "NO_FLOW" | "NORMAL" | "PROLONGED_USAGE" | "CONTINUOUS_ABNORMAL_USAGE" | "NIGHT_FLOW" | "UNKNOWN" | "OUT_OF_SERVICE";
export interface UsageLive {
  nodeRef: number; number: number; name: string; label: string; preset: string; presetLabel: string; custom: boolean; summary: string; tLimS: number; tMaxS: number;
  status: UsageStatus; tFlowS: number | null; next: { what: string; inS: number } | null; allowed: boolean; quiet: boolean; flowLpm: number | null; alertId: number | null;
  demo: { until: number; quiet: boolean } | null;
}
export interface NodeUsage {
  nodeRef: number; label: string; profile: UsageProfile; custom: boolean; updatedBy: string | null; updatedAt: string | null; presets: UsagePresetInfo[]; defaults: UsageProfile;
  demo: { until: number; quiet: boolean } | null; live: UsageLive | null;
}
export const USAGE_TEXT: Record<UsageStatus, { text: string; tone: "green" | "amber" | "red" | "gray" | "blue" }> = {
  NO_FLOW: { text: "No flow", tone: "gray" }, NORMAL: { text: "Normal use", tone: "green" }, PROLONGED_USAGE: { text: "Prolonged usage", tone: "amber" },
  CONTINUOUS_ABNORMAL_USAGE: { text: "Continuous abnormal usage", tone: "red" }, NIGHT_FLOW: { text: "Night flow - abnormal", tone: "red" }, UNKNOWN: { text: "Unknown (no data)", tone: "gray" }, OUT_OF_SERVICE: { text: "Out of service", tone: "gray" },
};
export interface ZoneUsagePoint { bucket: string; inLiters: number; outLiters: number; unaccountedLiters: number; buckets: number; completeBuckets: number }
export interface UsagePoint { bucket: string; liters: number; samples: number; lowConfidence: boolean }

export interface PresetInfo { id: string; name: string; description: string; physicalSetup: string[]; slots: string[]; minNodes: number; maxNodes: number }

// ---------------------------------------------------------------- calibration wizard
export interface CalNode {
  id: number; number: number; label: string; name: string; order: number; currentPpl: number; isDefault: boolean; outOfService: boolean;
  idle: { ok: boolean; reason: string }; live: { flowLpm: number | null; cumPulses: number | null; ageS: number | null }; pulsesInRun: number | null;
}
export interface CalCell { pulses: number | null; valid: boolean; reason: string | null; ppl?: number | null; flowLpm?: number | null }
export interface CalRun {
  id: number; idx: number; status: "active" | "stopping" | "done" | "invalid"; volumeL: number | null; durationS: number | null; reason: string | null;
  startedAt: string; stopRequestedAt: string | null; stoppedAt: string | null; waitingFor: string[]; cells: Record<string, CalCell>;
}
export interface CalNodeStat { id: number; label: string; runs: number; meanPpl: number | null; medianPpl: number | null; weightedPpl: number | null; spreadPct: number | null; quality: "good" | "ok" | "poor" | "unknown" | "none"; current: number }
export interface CalWarning { code: string; severity: "warning" | "info"; runId: number | null; nodeId: number | null; message: string }
export interface CalProposal { value: number | null; verdict: "ok" | "confirm" | "refuse"; message: string }
export interface CalSession {
  id: number; status: "running" | "done" | "cancelled"; createdAt: string; endedAt: string | null; pipelineId: number | null; nodes: CalNode[]; runs: CalRun[];
  results: { runs: Array<{ id: number; idx: number; status: string; volumeL: number | null; durationS: number | null; nodes: Record<string, CalCell> }>; nodes: CalNodeStat[]; warnings: CalWarning[]; validRuns: number };
  proposals: Record<string, { weighted: CalProposal; median: CalProposal }>; activeRunId: number | null; canStart: boolean;
  recommended: { minRuns: number; maxRuns: number; minLiters: number }; limits: { min: number; max: number; confirmPct: number };
}
export interface CalApplyResult { nodeId: number; label: string; commandId: number | null; valueX10?: number; error: string | null }
export interface CalHistory { id: number; at: string; user: string; oldPpl: number; newPpl: number; runs: number | null; spreadPct: number | null; source: "wizard" | "manual" | "revert"; method: string | null; sessionId: number | null; commandStatus: string | null }

const q = (o: Record<string, string | number | boolean | undefined | null>) => {
  const p = new URLSearchParams();
  Object.entries(o).forEach(([k, v]) => { if (v !== undefined && v !== null) p.set(k, String(v)); });
  const s = p.toString();
  return s ? `?${s}` : "";
};
const body = (o: unknown) => JSON.stringify(o);

export const v2 = {
  summary: () => request<SummaryV2>("/summary"),
  gateways: () => request<GatewaysResponse>("/gateways"),
  nodes: () => request<{ nodes: NodeV2[] }>("/nodes"),
  nearby: () => request<{ nearby: NearbyNode[] }>("/nodes/nearby"),
  nodeLink: (ref: number, hours = 24) => request<{ hours: number; points: LinkPoint[] }>(`/nodes/${ref}/link${q({ hours })}`),
  command: (id: number) => request<CommandInfo>(`/commands/${id}`),
  addNode: (b: { mac: string; code: string; type: "flow" | "rain"; name?: string; gatewayRef?: number }) => request<CommandAck>(`/nodes${q({ wait: 0 })}`, { method: "POST", body: body(b) }),
  replaceNode: (ref: number, b: { mac: string; code: string }) => request<CommandAck>(`/nodes/${ref}/replace${q({ wait: 0 })}`, { method: "POST", body: body(b) }),
  patchNode: (ref: number, b: { name?: string; pulsesPerLiter?: number; txReduce?: boolean; txPowerDbm?: 8.5 | 11 | 13 | 15 }) => request<CommandAck>(`/nodes/${ref}${q({ wait: 0 })}`, { method: "PATCH", body: body(b) }),
  setDemo: (pid: number, on: boolean) => request<{ ok: boolean; demo: boolean; demoUntil: number | null }>(`/pipelines/${pid}/demo`, { method: "PUT", body: body({ on }) }),
  setOutOfService: (ref: number, outOfService: boolean) => request<{ ok: boolean; outOfService: boolean }>(`/nodes/${ref}/service`, { method: "PUT", body: body({ outOfService }) }),
  removeNode: (ref: number) => request<CommandAck>(`/nodes/${ref}${q({ wait: 0 })}`, { method: "DELETE" }),
  usage: (ref: number, bucket: "hour" | "day" | "week" | "month", days: number) => request<{ nodeRef: number; bucket: string; usage: UsagePoint[] }>(`/nodes/${ref}/usage${q({ bucket, days })}`),

  calibrationActive: () => request<{ active: boolean; sessionId?: number; nodes?: Array<{ id: number; label: string }> }>("/calibration/active"),
  calibrationCreate: (nodeIds: number[]) => request<CalSession>("/calibration/sessions", { method: "POST", body: body({ nodeIds }) }),
  calibration: (id: number) => request<CalSession>(`/calibration/sessions/${id}`),
  calibrationStart: (id: number) => request<CalSession>(`/calibration/sessions/${id}/runs`, { method: "POST" }),
  calibrationStop: (id: number, run: number) => request<CalSession>(`/calibration/sessions/${id}/runs/${run}/stop`, { method: "POST" }),
  calibrationVolume: (id: number, run: number, volumeL: number) => request<CalSession>(`/calibration/sessions/${id}/runs/${run}`, { method: "PUT", body: body({ volumeL }) }),
  calibrationDeleteRun: (id: number, run: number) => request<CalSession>(`/calibration/sessions/${id}/runs/${run}`, { method: "DELETE" }),
  calibrationCancel: (id: number) => request<CalSession>(`/calibration/sessions/${id}/cancel`, { method: "POST" }),
  calibrationApply: (id: number, b: { method: "weighted" | "median"; confirm?: boolean; nodeIds?: number[] }) => request<{ ok: boolean; status: string; results: CalApplyResult[] }>(`/calibration/sessions/${id}/apply`, { method: "POST", body: body(b) }),
  calibrationHistory: (ref: number) => request<{ history: CalHistory[] }>(`/nodes/${ref}/calibrations`),
  calibrationRevert: (ref: number, hid: number) => request<CommandAck>(`/nodes/${ref}/calibrations/${hid}/revert`, { method: "POST" }),

  pipelinePresets: () => request<{ presets: PresetInfo[] }>("/pipelines/presets"),
  createFromPreset: (b: { preset: string; nodeIds: number[]; name?: string }) => request<{ id: number; version: number; activeVersion: number | null; valid: boolean; messages: ValidationMessage[]; missingNodes: number }>("/pipelines/from-preset", { method: "POST", body: body(b) }),
  pipelines: () => request<{ pipelines: PipelineListItem[] }>("/pipelines"),
  pipeline: (id: number, version?: number) => request<PipelineFull>(`/pipelines/${id}${q({ version })}`),
  versions: (id: number) => request<{ activeVersion: number | null; versions: VersionItem[] }>(`/pipelines/${id}/versions`),
  createPipeline: (b: { name?: string; doc?: PipelineDoc; note?: string; activate?: boolean }) => request<{ id: number; version: number; activeVersion: number | null; valid: boolean; messages: ValidationMessage[] }>("/pipelines", { method: "POST", body: body(b) }),
  savePipeline: (id: number, b: { doc: PipelineDoc; name?: string; note?: string; activate?: boolean }) =>
    request<{ id: number; version: number; valid: boolean; activated: boolean; messages: ValidationMessage[] }>(`/pipelines/${id}`, { method: "PUT", body: body(b) }),
  validate: (doc: PipelineDoc) => request<ValidationSummary>("/pipelines/validate", { method: "POST", body: body({ doc }) }),
  activate: (id: number, version: number) => request<{ ok: boolean; activeVersion: number }>(`/pipelines/${id}/activate`, { method: "POST", body: body({ version }) }),
  renamePipeline: (id: number, name: string) => request<{ id: number; name: string }>(`/pipelines/${id}`, { method: "PATCH", body: body({ name }) }),
  deletePipeline: (id: number) => request<{ ok: boolean }>(`/pipelines/${id}`, { method: "DELETE" }),
  pipelineStatus: (id: number) => request<PipelineStatus>(`/pipelines/${id}/status`),
  zoneSeries: (id: number, zone: string, hours = 6) => request<ZoneSeries>(`/pipelines/${id}/zones/${encodeURIComponent(zone)}/series${q({ hours })}`),
  zoneUsage: (id: number, zone: string, bucket: "hour" | "day" | "week" | "month", days: number) => request<{ zone: string; bucket: string; usage: ZoneUsagePoint[] }>(`/pipelines/${id}/zones/${encodeURIComponent(zone)}/usage${q({ bucket, days })}`),
  zoneThresholds: (pid: number, zid: string) => request<ZoneThresholds>(`/pipelines/${pid}/zones/${encodeURIComponent(zid)}/thresholds`),
  setZoneThresholds: (pid: number, zid: string, b: { xPlusLpm?: number; xMinusLpm?: number; tLeakS?: number; source?: "learned" | "manual" }) => request<ZoneThresholds>(`/pipelines/${pid}/zones/${encodeURIComponent(zid)}/thresholds`, { method: "PUT", body: body(b) }),
  resetZoneThresholds: (pid: number, zid: string) => request<ZoneThresholds>(`/pipelines/${pid}/zones/${encodeURIComponent(zid)}/thresholds`, { method: "DELETE" }),
  learnState: (pid: number, zid: string) => request<LearnSession>(`/pipelines/${pid}/zones/${encodeURIComponent(zid)}/learn`),
  learnAction: (pid: number, zid: string, action: "start" | "stop" | "cancel") => request<LearnSession>(`/pipelines/${pid}/zones/${encodeURIComponent(zid)}/learn`, { method: "POST", body: body({ action }) }),
  zoneLeaks: (id: number) => request<{ leaks: ZoneLeak[] }>(`/pipelines/${id}/leaks`),

  alerts: (active = false, limit = 300) => request<AlertV2[]>(`/alerts${q({ active: active ? 1 : 0, limit })}`),
  ack: (id: number) => request<{ ok: boolean }>(`/alerts/${id}/ack`, { method: "POST" }),
  resolve: (id: number, reason: string, note = "") => request<{ ok: boolean }>(`/alerts/${id}/resolve`, { method: "POST", body: body({ reason, note }) }),
  alertHistory: (f: HistoryFilter) => request<{ history: HistoryEvent[]; reasons: Record<string, string>; types: Record<string, string> }>(`/alerts/history${q(f as Record<string, string | number | undefined>)}`),
  alertDetail: (id: number) => request<{ event: HistoryEvent; series: EventSeries }>(`/alerts/${id}/detail`),
  usageDefaults: () => request<{ profile: UsageProfile; factory: UsageProfile; presets: UsagePresetInfo[]; custom: boolean; updatedBy: string | null; updatedAt: string | null }>("/usage/defaults"),
  setUsageDefaults: (p: Partial<UsageProfile>) => request<{ profile: UsageProfile }>("/usage/defaults", { method: "PUT", body: body(p) }),
  resetUsageDefaults: () => request<{ ok: boolean }>("/usage/defaults", { method: "DELETE" }),
  usageLive: () => request<{ nodes: UsageLive[] }>("/usage/live"),
  usageAudit: (limit = 50) => request<{ audit: Array<{ ts: string; user: string | null; action: string; detail: string }> }>(`/usage/audit${q({ limit })}`),
  nodeUsage: (ref: number) => request<NodeUsage>(`/nodes/${ref}/usage-profile`),
  setNodeUsage: (ref: number, p: Partial<UsageProfile>) => request<{ profile: UsageProfile }>(`/nodes/${ref}/usage-profile`, { method: "PUT", body: body(p) }),
  resetNodeUsage: (ref: number) => request<{ ok: boolean }>(`/nodes/${ref}/usage-profile`, { method: "DELETE" }),
  setUsageDemo: (ref: number, on: boolean, quiet = false) => request<{ ok: boolean; on: boolean; until: number | null }>(`/nodes/${ref}/usage-profile/demo`, { method: "PUT", body: body({ on, quiet }) }),
};

/** Helpers used by several pages. */
export const BATTERY_PLAUSIBLE_MV: [number, number] = [4000, 9500];             // keep in step with the backend (battery_min/max_plausible_mv)
export const batteryImplausible = (mv: number | null | undefined) => mv !== null && mv !== undefined && (mv < BATTERY_PLAUSIBLE_MV[0] || mv > BATTERY_PLAUSIBLE_MV[1]);
export const DEFAULT_PPL = 450;
export const calibrationText = (ppl: number | null | undefined) => (ppl ? `${ppl} p/L` : `${DEFAULT_PPL} p/L (default)`);
export const WEAK_LOSS_PCT = 10, WEAK_RSSI_DBM = -80;
/** Reasons why a node's radio link is weak (empty = fine): more than 10 % of its readings never arrived, or the signal is below -80 dBm. */
export function weakLinkReasons(n: { rssi: number | null; live: { lossPct24h: number } | null }): string[] {
  const out: string[] = [];
  if (n.live && n.live.lossPct24h > WEAK_LOSS_PCT) out.push(`${n.live.lossPct24h.toFixed(1)} % of readings missing (limit ${WEAK_LOSS_PCT} %)`);
  if (n.rssi !== null && n.rssi !== 0 && n.rssi < WEAK_RSSI_DBM) out.push(`signal ${n.rssi} dBm (limit ${WEAK_RSSI_DBM} dBm)`);
  return out;
}
export interface LinkPoint { hour: string; missed: number; received: number; lossPct: number; rssi: number | null }
export const batteryText = (mv: number | null | undefined) => (mv === null || mv === undefined ? "No battery" : `${(mv / 1000).toFixed(2)} V`);
export const nodeTitle = (n: { number: number; name: string; type?: string }) => `${n.type === "rain" ? "Rain" : "Node"} ${n.number}${n.name && n.name !== `${n.type === "rain" ? "Rain" : "Node"} ${n.number}` ? ` - ${n.name}` : ""}`;
export const lpm = (v: number | null | undefined, d = 2) => (v === null || v === undefined ? "—" : v.toFixed(d));
export const liters = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v >= 1000 ? `${(v / 1000).toFixed(2)} m³` : `${v.toFixed(v < 10 ? 2 : 1)} L`);

// ---------------------------------------------------------------- overall leak-check state (the worst honest state of all zones)
export type LeakCheck = {
  state: "none" | "confirmed" | "suspected" | "paused" | "inconsistent" | "watching" | "ok";
  paused: LiveZone[]; nodes: string[]; headline: string; excluded?: LiveZone[]; note?: string; watching?: LiveZone[];
};
/** Green ("ok") only when EVERY monitored zone has complete recent data and no leak; a zone without data pauses the check (amber), it never counts as fine. */
export function leakCheck(zones: LiveZone[] | null | undefined): LeakCheck {
  const allZ = (zones ?? []).filter((z) => z.class !== "not_monitored" && z.status !== "not_monitored");
  const calZ = allZ.filter((z) => z.status === "calibrating");                       // paused on purpose while a calibration session runs: neither fine nor a fault
  const all = allZ.filter((z) => z.status !== "calibrating");
  // a zone whose only missing data is a node the owner put out of service is left out of the check (and said so), it never counts as fine
  const isExcluded = (z: LiveZone) => z.status === "incomplete" && (z.nodesOutOfService?.length ?? 0) > 0 && (z.nodesOffline?.length ?? 0) === 0 && z.nodesMissing.length === 0;
  const excluded = all.filter(isExcluded), mon = all.filter((z) => !isExcluded(z));
  const notes = [
    calZ.length ? `${calZ.map((z) => z.name).join(", ")} paused: calibrating ${Array.from(new Set(calZ.flatMap((z) => z.calibrating ?? []))).join(", ")}` : "",
    excluded.length ? `${excluded.map((z) => z.name).join(", ")} not monitored: ${Array.from(new Set(excluded.flatMap((z) => z.nodesOutOfService ?? []))).join(", ")} out of service` : "",
  ].filter(Boolean);
  const note = notes.length ? notes.join("; ") : undefined;
  const paused = mon.filter((z) => z.status === "incomplete");
  const nodes = Array.from(new Set(paused.flatMap((z) => [...(z.nodesOffline ?? []), ...(z.nodesMissing ?? [])])));
  if (mon.length === 0) return { state: "none", paused: [], nodes: [], excluded, note, headline: calZ.length ? "Leak check paused - calibration in progress" : excluded.length ? "Leak check off - monitored nodes are out of service" : "Nothing can be monitored yet" };
  if (mon.some((z) => z.status === "confirmed")) return { state: "confirmed", paused, nodes, excluded, note, headline: "Leak confirmed" };
  if (mon.some((z) => z.status === "suspected")) return { state: "suspected", paused, nodes, excluded, note, headline: "Leak suspected" };
  if (paused.length > 0) return { state: "paused", paused, nodes, excluded, note, headline: `Leak check paused - ${paused.length} zone${paused.length === 1 ? " has" : "s have"} no data${nodes.length ? " (nodes offline)" : ""}` };
  const incons = mon.filter((z) => z.balanceState === "inconsistent");
  if (incons.length > 0) return { state: "inconsistent", paused: [], nodes: [], excluded, note, headline: `Measurement inconsistency: ${incons.map((z) => z.name).join(", ")} - outflow is larger than inflow (check sensor, calibration or test; not a leak)` };
  const watching = mon.filter((z) => z.watching || z.balanceState === "suspicious");
  if (watching.length > 0) return { state: "watching", paused: [], nodes: [], excluded, note, headline: `Watching: ${watching.map((z) => z.name).join(", ")} ${watching.some((z) => z.class === "strong") ? "above threshold" : "flowing for a long time"}`, watching } as LeakCheck;
  return { state: "ok", paused: [], nodes: [], excluded, note, headline: "No leak detected" };
}

/** RSSI of exactly 0 dBm is the "never measured" default, not a real value. */
export const rssiValid = (r: number | null | undefined): r is number => r !== null && r !== undefined && r !== 0;
export const rssiText = (r: number | null | undefined) => (rssiValid(r) ? `${r} dBm` : "—");
