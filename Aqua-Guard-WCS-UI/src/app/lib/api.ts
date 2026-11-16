// API client. Authentication is a server-side session (HttpOnly cookie); the CSRF token is kept in memory only.
// Base URL: VITE_API_BASE (build time) > same host as the page on port 5000 (works from a phone on the LAN).
const envBase = (import.meta as any).env?.VITE_API_BASE as string | undefined;
export const API_BASE: string = (envBase && envBase.trim()
  ? envBase.trim().replace(/\/+$/, "")
  : `${window.location.protocol}//${window.location.hostname}:5000/api`);

// ---------------------------------------------------------------- types
export interface FlowReading {
  id: number; node: number; bootId: number; seq: number;
  flowRate: number; totalLiters: number; intervalPulses: number; cumPulses: number; deltaPulses: number | null;
  pulsesPerLiter: number; uptime: number; status: number; recovered: boolean; backlog: boolean; gapBefore: number | null;
  channel: number | null; rssi: number | null; measuredAt: string; receivedAt: string;
  ageSeconds: number | null; active: boolean;
}

export interface RainwaterReading {
  id: number; bootId: number; seq: number; rain: boolean;
  waterLevel: number | null; levelValid: boolean; outOfRange?: boolean; distance: number | null;
  turbidity: number | null; turbidityValid: boolean; waterClear: boolean; tankFull: boolean;
  valveOpen: boolean; valveState: number | null; valveReason: number | null; firstFlush: boolean;
  buzzer: boolean; dryRun: boolean; faults: number; rainEvents: number; uptime: number; recovered: boolean;
  channel: number | null; rssi: number | null; measuredAt: string; receivedAt: string;
  ageSeconds: number | null; active: boolean;
}

export interface GatewayStatus { status: string; receivedAt: string | null; ageSeconds: number | null; active: boolean; }

export interface GatewayDetail {
  online: boolean; status: string; lastHeartbeatAt: string | null; heartbeatAgeSeconds: number | null;
  lastStatusMessageAt: string | null; statusAgeSeconds: number | null; lastWillOffline: boolean;
  gatewayId: string | null; wifiUp: boolean | null; wifiRssi: number | null; channel: number | null;
  mqttUp: boolean | null; freeHeap: number | null; uptimeSeconds: number | null;
  queueDepth: number | null; queueOverflow: number | null; channelChanges: number | null;
  wifiReconnects: number | null; mqttReconnects: number | null;
}

export interface CommStats {
  received: number; duplicates: number; recovered: number; lost: number; missing: number;
  outOfOrder: number; nacksSent: number; invalid: number; sessions: number;
}

export interface DeviceStatus {
  device: string; node: number; kind: "flow" | "rain"; label: string; state: "online" | "offline" | "unknown";
  lastSeen: string | null; lastDataAt: string | null; ageSeconds: number | null;
  bootId: number | null; lastSeq: number | null; sessions: number; uptimeSeconds: number | null;
  channel: number | null; rssi: number | null; firmwareStatus: number | null;
  nodeRetransmits: number | null; nodeDropped: number | null; nodeRediscoveries: number | null;
  comm: CommStats | null;
}

export interface SystemStatusData {
  gatewayStatus: string; gatewayLastReceived: string | null; gatewayAgeSeconds: number | null; gatewayActive: boolean;
  gateway: GatewayDetail; devices: DeviceStatus[];
  node1LastReceived: string | null; node2LastReceived: string | null; rainwaterLastReceived: string | null;
  mqtt: { connected: boolean; [k: string]: unknown };
}

export interface DevicesData { gateway: GatewayDetail; devices: DeviceStatus[]; }

export interface CommHistoryRow {
  id: number; node_id: number; received_at: string; boot_id: number | null; received: number; duplicates: number;
  recovered: number; lost: number; missing: number; out_of_order: number; nacks_sent: number; sessions: number;
  invalid: number; node_retransmits: number | null; node_dropped: number | null; node_rediscoveries: number | null;
  age_s: number | null; rssi: number | null; node_uptime_s: number | null;
}

export interface WaterLiveData { node1: FlowReading | null; node2: FlowReading | null; gateway: GatewayStatus | null; }

export interface WaterSummaryData {
  authoritativeNode: number; authoritativeVolume: number; node1TotalLiters: number; node2TotalLiters: number;
  node1CurrentFlow: number | null; node2CurrentFlow: number | null;
  todayLiters: number; last7DaysLiters: number; last30DaysLiters: number; missedIntervals: number; totalMeasurements: number;
}

export interface ConsumptionPoint { bucket: string; liters: number; samples: number; missedIntervals: number; }
export interface ConsumptionData { period: "day" | "week" | "month"; node: number; series: ConsumptionPoint[]; }

export type LeakState = "NORMAL" | "SUSPECTED" | "CONFIRMED" | "RESOLVED" | "SENSOR_FAULT";

export interface LeakEvent {
  id: string; dbId: number; node1Flow: number; node2Flow: number; flowDifference: number; differencePercentage: number;
  severity: "normal" | "warning" | "critical"; status: "active" | "suspected" | "resolved" | "dismissed"; state: LeakState;
  detectedAt: string; suspectedAt: string | null; confirmedAt: string | null; resolvedAt: string | null;
  peakDifference: number | null; peakPercentage: number | null;
}

export interface LeakLiveData {
  node1Flow: number | null; node2Flow: number | null; flowDifference: number | null; differencePercentage: number | null;
  state: LeakState; stateSince: string | null; reason: string;
  status: "normal" | "warning" | "leak" | "unavailable"; severity: "normal" | "warning" | "critical" | "none";
  node1Active: boolean; node2Active: boolean; lastUpdated: string | null; activeEvent: LeakEvent | null;
  thresholds: { absoluteLpm: number; relativePct: number; suspectAfterS: number; confirmAfterS: number };
}

export interface LeakSummaryData {
  activeLeaks: number; suspectedLeaks: number; eventsToday: number; eventsThisWeek: number; resolvedEvents: number; state: LeakState;
}

export interface RainwaterLiveData { rainwater: RainwaterReading | null; }

export interface RainwaterSummaryData {
  isRaining: boolean; tankFillPercentage: number | null; ultrasonicDistance: number | null; ultrasonicValid: boolean;
  turbidityAdc: number | null; waterClear: boolean | null; turbidityValid: boolean | null; valveOpen: boolean;
  valveState: number | null; valveReason: number | null; tankFull: boolean | null; dryRun: boolean | null;
  faults: number | null; totalMeasurements: number; rainEventCount: number;
}

export interface RainEvent {
  id: number; startedAt: string; lastRainAt: string | null; endedAt: string | null;
  startLevelPct: number | null; endLevelPct: number | null; peakLevelPct: number | null; levelGainPct: number | null;
  valveOpenSeconds: number; unsafeSeconds: number;
}

export interface AlertItem {
  id: number; key: string; kind: string; severity: "info" | "warning" | "critical" | string; message: string;
  active: boolean; createdAt: string; clearedAt: string | null; acknowledgedBy: string | null; acknowledgedAt: string | null;
}

export interface OverviewData {
  consumption: {
    authoritativeNode: number;
    allTime: { liters: number }; today: { liters: number }; last7Days: { liters: number }; last30Days: { liters: number };
  };
  node1: FlowReading | null; node2: FlowReading | null; rainwater: RainwaterReading | null;
  leak: { state: LeakState; since: string | null; reason: string; eventId: number | null };
  gateway: GatewayDetail; devices: DeviceStatus[]; alerts: AlertItem[];
}

export interface LiveData { node1: FlowReading | null; node2: FlowReading | null; rainwater: RainwaterReading | null; gateway: GatewayStatus | null; }

export interface LeakageReport {
  id: string; dbId: number; building: string; location: string; description: string;
  status: "pending" | "investigating" | "resolved" | "rejected"; date: string; reportedBy: string;
}

export interface UserProfileData {
  id: number; firstName: string; lastName: string; email: string; phone: string; address: string; role: "admin" | "user";
}

export interface SessionInfo { user: UserProfileData; csrfToken: string; mustChangePassword: boolean; sessionIdleMinutes?: number; }

export interface AdminUser extends UserProfileData { active: boolean; lockedUntil: number | null; [k: string]: unknown; }


// ---------------------------------------------------------------- device registry / pairing
export interface RegistryLatest {
  flowRate?: number; pulsesPerLiter?: number; cumPulses?: number; measuredAt?: string; receivedAt?: string;
  rssi?: number | null; channel?: number | null; totalLiters: number; samples: number; missedIntervals: number;
}
export interface RegistryDevice {
  uid: number; deviceId: string; type: "flow" | "rain"; displayName: string; mac: string | null;
  pairingStatus: "provisioned" | "pairing" | "paired" | "unpaired"; slot: number | null; legacy: boolean;
  firmwareVersion: string | null; calibrationPulsesPerLiter: number | null;
  state: "online" | "offline" | "unknown" | "n/a"; lastSeen: string | null; ageSeconds: number | null;
  pairedAt: string | null; pairedBy: string | null; unpairedAt: string | null; createdAt: string;
  pairingError: string | null; locked: boolean; lastVerifiedAt: string | null; sessions: number; bootId: number | null;
  latest: RegistryLatest | null;
  history?: Array<{ ts: string; kind: string; ok: number; detail: string; user_email: string | null }>;
}
export interface RegistryList { devices: RegistryDevice[]; gatewayOnline: boolean; pairingConfigured: boolean; maxSlots: number; }
export interface DiscoveredDevice {
  uid: number; mac: string; type: string; firmwareVersion: string; channel: number; rssi: number; firstSeen: string; lastSeen: string;
  announces: number; provisioned: boolean; pairingStatus: string | null; deviceId: string | null; canPair: boolean;
}
export interface DiscoveredList { devices: DiscoveredDevice[]; gatewayOnline: boolean; pairingConfigured: boolean; }

// ---------------------------------------------------------------- transport
export class ApiError extends Error {
  status: number;
  data: any;
  constructor(message: string, status: number, data?: any) { super(message); this.status = status; this.data = data; }
}

let csrfToken = "";
export const setCsrfToken = (t: string) => { csrfToken = t || ""; };

let unauthorizedHandler: (() => void) | null = null;
export const onUnauthorized = (fn: (() => void) | null) => { unauthorizedHandler = fn; };

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method || "GET").toUpperCase();
  const headers: Record<string, string> = { ...(options.headers as Record<string, string> || {}) };
  if (options.body) headers["Content-Type"] = "application/json";
  if (method !== "GET" && method !== "HEAD" && csrfToken) headers["X-CSRF-Token"] = csrfToken;

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...options, method, headers, credentials: "include" });
  } catch {
    throw new ApiError(`Cannot reach the server at ${API_BASE}`, 0);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/login") && path !== "/me") unauthorizedHandler?.();
    throw new ApiError(data.error || `Request failed with status ${res.status}`, res.status, data);
  }
  return data as T;
}

const qs = (o: Record<string, string | number | undefined | null>) => {
  const p = new URLSearchParams();
  Object.entries(o).forEach(([k, v]) => { if (v !== undefined && v !== null) p.set(k, String(v)); });
  const s = p.toString();
  return s ? `?${s}` : "";
};

const body = (o: unknown) => JSON.stringify(o);

export const api = {
  // session
  login: async (email: string, password: string) => {
    const r = await request<SessionInfo>("/login", { method: "POST", body: body({ email, password }) });
    setCsrfToken(r.csrfToken);
    return r;
  },
  me: async () => {
    const r = await request<SessionInfo>("/me");
    setCsrfToken(r.csrfToken);
    return r;
  },
  logout: async () => { try { await request<{ ok: boolean }>("/logout", { method: "POST" }); } finally { setCsrfToken(""); } },
  changePassword: (currentPassword: string, newPassword: string) =>
    request<{ ok: boolean }>("/change-password", { method: "POST", body: body({ currentPassword, newPassword }) }),

  // system
  health: () => request<{ ok: boolean; database: string; mqtt: boolean }>("/health"),
  getSystemStatus: () => request<SystemStatusData>("/system/status"),
  getDevices: () => request<DevicesData>("/devices"),
  getCommHistory: (node?: number, limit = 120) => request<CommHistoryRow[]>(`/comm/history${qs({ node, limit })}`),
  getOverview: () => request<OverviewData>("/overview"),

  // water
  getWaterLive: () => request<WaterLiveData>("/water/live"),
  getWaterHistory: (node?: number, limit = 100) => request<FlowReading[]>(`/water/history${qs({ node, limit })}`),
  getWaterSummary: () => request<WaterSummaryData>("/water/summary"),
  getConsumption: (period: "day" | "week" | "month", count = 14, node?: number) =>
    request<ConsumptionData>(`/water/consumption${qs({ period, count, node })}`),

  // leaks
  getLeakLive: () => request<LeakLiveData>("/leaks/live"),
  getLeakEvents: (limit = 50) => request<LeakEvent[]>(`/leaks/events${qs({ limit })}`),
  getLeakSummary: () => request<LeakSummaryData>("/leaks/summary"),

  // rainwater
  getRainwaterLive: () => request<RainwaterLiveData>("/rainwater/live"),
  getRainwaterHistory: (limit = 100) => request<RainwaterReading[]>(`/rainwater/history${qs({ limit })}`),
  getRainwaterSummary: () => request<RainwaterSummaryData>("/rainwater/summary"),
  getRainEvents: (limit = 50) => request<RainEvent[]>(`/rainwater/events${qs({ limit })}`),

  // alerts
  getAlerts: (active = false, limit = 100) => request<AlertItem[]>(`/alerts${qs({ active: active ? 1 : 0, limit })}`),
  ackAlert: (id: number) => request<{ ok: boolean }>(`/alerts/${id}/ack`, { method: "POST" }),

  // profile (the server decides who may read/write what)
  getProfile: (email?: string) => request<UserProfileData>(`/profile${qs({ email })}`),
  updateProfile: (b: { currentEmail?: string; firstName?: string; lastName?: string; email?: string; phone?: string; address?: string }) =>
    request<{ ok: boolean }>("/profile", { method: "PUT", body: body(b) }),

  // reports
  getReports: () => request<LeakageReport[]>("/reports"),
  createReport: (b: { building: string; location: string; description: string; reportedBy?: string }) =>
    request<LeakageReport>("/reports", { method: "POST", body: body(b) }),
  updateReportStatus: (id: number, status: LeakageReport["status"]) =>
    request<LeakageReport>(`/reports/${id}`, { method: "PUT", body: body({ status }) }),

  // admin
  getUsers: () => request<AdminUser[]>("/admin/users"),
  createUser: (b: { email: string; role: "user" | "admin"; password: string; firstName?: string; lastName?: string }) =>
    request<{ ok: boolean; id: number }>("/admin/users", { method: "POST", body: body(b) }),
  updateUser: (id: number, b: { role?: "user" | "admin"; active?: boolean }) =>
    request<{ ok: boolean }>(`/admin/users/${id}`, { method: "PUT", body: body(b) }),
  getAudit: (limit = 100) => request<Array<Record<string, unknown>>>(`/admin/audit${qs({ limit })}`),

  // device registry / pairing (reads: any user; writes: administrator)
  getRegistry: () => request<RegistryList>("/registry/devices"),
  getRegistryDevice: (uid: number) => request<RegistryDevice>(`/registry/devices/${uid}`),
  getDiscovered: () => request<DiscoveredList>("/registry/discovered"),
  pairDevice: (uid: number, code: string, name?: string) =>
    request<{ ok: boolean; status: string; slot: number; commandSent: boolean; message: string }>(`/registry/devices/${uid}/pair`, { method: "POST", body: body({ code, name }) }),
  unpairDevice: (uid: number) => request<{ ok: boolean; commandSent: boolean }>(`/registry/devices/${uid}/unpair`, { method: "POST" }),
  newPairingCode: (uid: number) => request<{ ok: boolean; pairingCode: string }>(`/registry/devices/${uid}/new-code`, { method: "POST" }),
  updateRegistryDevice: (uid: number, b: { displayName?: string; calibrationPulsesPerLiter?: number | null }) =>
    request<{ ok: boolean }>(`/registry/devices/${uid}`, { method: "PUT", body: body(b) }),

  // backward-compatible aliases used by older components
  live: () => request<LiveData>("/live"),
  reports: () => request<LeakageReport[]>("/reports"),
  updateReport: (id: number, status: LeakageReport["status"]) =>
    request<LeakageReport>(`/reports/${id}`, { method: "PUT", body: body({ status }) }),
  profile: (email?: string) => request<UserProfileData>(`/profile${qs({ email })}`),
  flowHistory: (node?: number, limit = 100) => request<FlowReading[]>(`/water/history${qs({ node, limit })}`),
  rainwaterHistory: (limit = 100) => request<RainwaterReading[]>(`/rainwater/history${qs({ limit })}`),
};
