import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ v2: {} as any, api: {} as any }));
vi.mock("../lib/v2", async (orig) => ({ ...(await orig<typeof import("../lib/v2")>()), v2: mocks.v2 }));
vi.mock("../lib/api", async (orig) => ({ ...(await orig<typeof import("../lib/api")>()), api: mocks.api }));
vi.mock("../lib/auth", () => ({ useAuth: () => ({ user: { role: "admin", email: "a@b.c" } }) }));

import { AdminModules } from "./AdminModules";
import { DevicesPage } from "./DevicesPage";
import { OverviewPage } from "./OverviewPage";
import { RainwaterHarvesting } from "./RainwaterHarvesting";
import { LinkDialog } from "./nodes/NodeDialogs";
import { Palette } from "./pipeline/PipelinePanels";
import { batteryImplausible, calibrationText, leakCheck, rssiText, weakLinkReasons } from "../lib/v2";
import { fmtClock, fmtDuration, fmtTime } from "../lib/usePolling";
import { LeakDetectionDashboard } from "./LeakDetectionDashboard";

const summary = (over: any = {}) => ({ gateway: { id: 1, gatewayId: "gw", state: "online", timeSynced: true, heartbeatAgeS: 3 }, gateways: { total: 1, online: 1 }, nodes: { total: 4, online: 3, offline: 1 },
  total: { basis: "none", flowLpm: null, todayLiters: null, weekLiters: null, node: null, pipelineId: null }, pipeline: null, leaks: { suspected: 0, confirmed: 0 }, otherAlerts: 0,
  battery: { withBattery: 0, noBattery: 4, low: [], lowLimitMv: 6600 }, ...over });
const wrap = (el: React.ReactNode) => render(<MemoryRouter>{el}</MemoryRouter>);

beforeEach(() => {
  Object.assign(mocks.v2, {
    summary: vi.fn(async () => summary()),
    nodes: vi.fn(async () => ({ nodes: [] })),
    alerts: vi.fn(async () => []),
    pipelines: vi.fn(async () => ({ pipelines: [] })),
    pipelineStatus: vi.fn(async () => null),
    nodeLink: vi.fn(async () => ({ hours: 24, points: [{ hour: "2026-01-01 10:00:00", missed: 1, received: 9, lossPct: 10, rssi: -70 }, { hour: "2026-01-01 11:00:00", missed: 3, received: 7, lossPct: 30, rssi: -82 }] })),
  });
  Object.assign(mocks.api, {
    getRainwaterSummary: vi.fn(async () => ({ isRaining: false, tankFillPercentage: null, valveOpen: false, dryRun: null, totalMeasurements: 0 })),
    getRainwaterLive: vi.fn(async () => ({ rainwater: null })),
    getRainwaterHistory: vi.fn(async () => []),
    getDevices: vi.fn(async () => ({ gateway: null, devices: [] })),
    getRegistry: vi.fn(async () => ({ devices: [], maxSlots: 4 })),
    getCommHistory: vi.fn(async () => []),
  });
});

describe("helpers", () => {
  it("calibration shows the default, battery plausibility and weak-link thresholds", () => {
    expect(calibrationText(null)).toBe("450 p/L (default)");
    expect(calibrationText(420)).toBe("420 p/L");
    expect(batteryImplausible(200)).toBe(true);
    expect(batteryImplausible(7400)).toBe(false);
    expect(batteryImplausible(null)).toBe(false);
    expect(weakLinkReasons({ rssi: -76, live: { lossPct24h: 14.2 } })).toHaveLength(1);          // 14.2 % > 10 %, -76 dBm is fine
    expect(weakLinkReasons({ rssi: -82, live: { lossPct24h: 3 } })).toHaveLength(1);
    expect(weakLinkReasons({ rssi: -60, live: { lossPct24h: 10 } })).toHaveLength(0);            // exactly 10 % is not "more than 10 %"
  });
});

describe("Admin dashboard modules", () => {
  it("without a pipeline: 'Not configured', never NORMAL", async () => {
    wrap(<AdminModules />);
    expect((await screen.findAllByText("Not configured")).length).toBe(2);
    expect(screen.queryByText(/NORMAL/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /create a pipeline/i })).toHaveAttribute("href", "/pipelines");
    expect(screen.getByText("3 / 4 online")).toBeInTheDocument();
    expect(screen.getByText("No data")).toBeInTheDocument();                                   // rainwater without readings
  });
  it("with a pipeline: zones by status and active leaks", async () => {
    mocks.v2.summary = vi.fn(async () => summary({ pipeline: { id: 1, name: "House" }, leaks: { suspected: 1, confirmed: 1 } }));
    mocks.v2.pipelines = vi.fn(async () => ({ pipelines: [{ id: 1, name: "House", activeVersion: 2, versions: 2, updatedAt: null }] }));
    mocks.v2.pipelineStatus = vi.fn(async () => ({ pipelineId: 1, name: "House", version: 2, live: {}, coverage: {}, suggestions: [],
      zones: [{ id: "a", status: "normal" }, { id: "b", status: "normal" }, { id: "c", status: "confirmed" }, { id: "d", status: "not_monitored" }] }));
    wrap(<AdminModules />);
    expect(await screen.findByText("2 normal")).toBeInTheDocument();
    expect(screen.getByText("1 confirmed leak")).toBeInTheDocument();
    expect(screen.getByText("1 not monitored")).toBeInTheDocument();
    expect(screen.getByText("House")).toBeInTheDocument();
  });
});

describe("Overview batteries", () => {
  it("implausible readings are not reported as 'all fine'", async () => {
    mocks.v2.summary = vi.fn(async () => summary({ battery: { withBattery: 2, noBattery: 0, low: [], implausible: [{ number: 3, name: "Com 34", mv: 200 }], lowLimitMv: 6600 } }));
    wrap(<OverviewPage />);
    expect(await screen.findByText(/Node 3 \(Com 34\): 0\.20 V is not a plausible battery reading/)).toBeInTheDocument();
    expect(screen.queryByText("All batteries are fine.")).not.toBeInTheDocument();
  });
});

describe("Rainwater page", () => {
  it("with no readings shows 'No data' and none of the fake states", async () => {
    wrap(<RainwaterHarvesting />);
    expect(await screen.findByTestId("empty-state")).toHaveTextContent("No data");
    expect(screen.getAllByText("No data").length).toBeGreaterThan(0);
    for (const bad of [/Dry weather/i, /SENSOR ERROR/i, /^CLOSED$/, /^0s$/, /Subsystem Offline/i]) expect(screen.queryByText(bad)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /go to nodes/i })).toHaveAttribute("href", "/nodes");
  });
});

describe("Legacy page", () => {
  it("is read-only: banner, no Add device, no ESP-01S instructions", async () => {
    mocks.api.getRegistry = vi.fn(async () => ({ devices: [{ uid: 1, deviceId: "AG-FLOW-001", type: "flow", displayName: "Old", pairingStatus: "paired", legacy: true, state: "online", lastSeen: null, ageSeconds: null }], maxSlots: 4 }));
    wrap(<DevicesPage />);
    expect(await screen.findByTestId("legacy-banner")).toHaveTextContent("Retired v1 devices - use Nodes");
    expect(await screen.findByText("Old")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add device/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/ESP-01S/)).not.toBeInTheDocument();
  });
});

describe("Link dialog", () => {
  it("shows the trend and what to check on the hardware for a weak link", async () => {
    const node: any = { id: 5, number: 4, name: "Far one", rssi: -82, live: { lossPct24h: 25.9 } };
    render(<LinkDialog node={node} onClose={() => {}} />);
    expect(await screen.findByTestId("link-trend")).toBeInTheDocument();
    expect(screen.getByText(/Weak link: 25\.9 % of readings missing/)).toBeInTheDocument();
    expect(screen.getByText(/antenna orientation/)).toBeInTheDocument();
    await waitFor(() => expect(mocks.v2.nodeLink).toHaveBeenCalledWith(5, 24));
    void userEvent;
  });
});

describe("Pipeline palette", () => {
  const doc: any = { schema: 1, name: "x", parts: [], links: [] };
  const av = (n: number, gw: string) => ({ type: "flow", number: n, name: `N${n}`, label: `Node ${n}`, nodeRef: n, gatewayId: gw });
  it("shows an icon per part, groups nodes by gateway and searches them", async () => {
    const user = userEvent.setup();
    render(<Palette doc={doc} available={[1, 2, 3, 4, 5].map((n) => av(n, n < 4 ? "gw-a" : "gw-b"))} pending={null} disabled={false} onPick={() => {}} />);
    expect(screen.getByTestId("palette-source").querySelector("svg")).not.toBeNull();
    expect(screen.getByTestId("palette-outlet-shower").querySelector("svg")).not.toBeNull();
    expect(screen.getByTestId("palette-gw-gw-a")).toHaveTextContent("Node 1");
    expect(screen.getByTestId("palette-gw-gw-b")).toHaveTextContent("Node 5");
    await user.type(screen.getByLabelText("Search nodes"), "node 5");
    expect(screen.queryByTestId("palette-node-1")).not.toBeInTheDocument();
    expect(screen.getByTestId("palette-node-5")).toBeInTheDocument();
  });
  it("without paired nodes it says where to pair one", () => {
    render(<Palette doc={doc} available={[]} pending={null} disabled={false} onPick={() => {}} />);
    expect(screen.getByTestId("palette-no-nodes")).toHaveTextContent(/Nodes page/);
  });
});

const Z = (id: string, status: string, over: any = {}) => ({ id, name: id, class: "strong", status, level: 0, reason: "", equation: "", inMeter: "Node 1", outMeters: [], outlets: [], outletIds: [], pipes: [], parts: [], balance: null,
  meterFaultHint: null, suggestions: [], parent: null, rules: [], completeBuckets: 0, finalBuckets: 0, nodesMissing: [], nodesOffline: [], ...over });

describe("leak check overall state (Group 1)", () => {
  it("is green only when every monitored zone has complete recent data", () => {
    expect(leakCheck([Z("a", "normal"), Z("b", "normal")]).state).toBe("ok");
    expect(leakCheck([Z("a", "normal"), Z("b", "not_monitored", { class: "not_monitored" })]).state).toBe("ok");
  });
  it("incomplete data pauses the check (amber) and names the nodes - never 'No leak detected'", () => {
    const r = leakCheck([Z("a", "normal"), Z("b", "incomplete", { nodesOffline: ["Node 3"] }), Z("c", "incomplete", { nodesMissing: ["Node 4"] })]);
    expect(r.state).toBe("paused");
    expect(r.headline).toBe("Leak check paused - 2 zones have no data (nodes offline)");
    expect(r.nodes).toEqual(["Node 3", "Node 4"]);
    expect(r.headline).not.toMatch(/No leak detected/);
  });
  it("a leak outranks a paused zone; nothing monitored is not green", () => {
    expect(leakCheck([Z("a", "suspected"), Z("b", "incomplete")]).state).toBe("suspected");
    expect(leakCheck([Z("a", "confirmed"), Z("b", "incomplete")]).state).toBe("confirmed");
    expect(leakCheck([Z("a", "not_monitored", { class: "not_monitored" })]).state).toBe("none");
    expect(leakCheck([]).state).toBe("none");
  });
  it("the Leak Detection page shows the amber paused banner", async () => {
    mocks.v2.pipelines = vi.fn(async () => ({ pipelines: [{ id: 1, name: "House", activeVersion: 2, versions: 2, updatedAt: null }] }));
    mocks.v2.zoneLeaks = vi.fn(async () => ({ leaks: [] }));
    mocks.v2.pipelineStatus = vi.fn(async () => ({ pipelineId: 1, name: "House", version: 2, live: {}, coverage: { text: "" }, suggestions: [], zones: [Z("a", "normal"), Z("b", "incomplete", { nodesOffline: ["Node 3"] })] }));
    wrap(<LeakDetectionDashboard />);
    const box = await screen.findByTestId("leak-overall");
    expect(box).toHaveAttribute("data-state", "paused");
    expect(box).toHaveTextContent("Leak check paused - 1 zone has no data");
    expect(box).toHaveTextContent("Node 3");
    expect(screen.queryByText("No leak detected")).not.toBeInTheDocument();
  });
  it("the Overview leak card is amber, not green, while a zone has no data", async () => {
    mocks.v2.summary = vi.fn(async () => summary({ pipeline: { id: 1, name: "House" } }));
    mocks.v2.pipelines = vi.fn(async () => ({ pipelines: [{ id: 1, name: "House", activeVersion: 2, versions: 2, updatedAt: null }] }));
    mocks.v2.pipelineStatus = vi.fn(async () => ({ pipelineId: 1, name: "House", version: 2, live: {}, coverage: {}, suggestions: [], zones: [Z("b", "incomplete", { nodesOffline: ["Node 3"] })] }));
    wrap(<OverviewPage />);
    expect(await screen.findByTestId("ov-leak-paused")).toHaveTextContent("Leak check paused");
    expect(screen.queryByText("no active leak")).not.toBeInTheDocument();
  });
});

describe("no-value and time rules (Group 1)", () => {
  it("RSSI 0 or null is shown as a dash everywhere", () => {
    expect(rssiText(0)).toBe("—"); expect(rssiText(null)).toBe("—"); expect(rssiText(-61)).toBe("-61 dBm");
    expect(weakLinkReasons({ rssi: 0, live: { lossPct24h: 0 } })).toHaveLength(0);
  });
  it("times are shown in Asia/Colombo whatever the browser zone is", () => {
    expect(fmtClock("2026-11-15 17:55:00")).toBe("23:25");                 // UTC 17:55 = 23:25 local
    expect(fmtTime("2026-11-15 17:55:00")).toMatch(/23:25:00/);
    expect(fmtClock(Date.UTC(2026, 10, 15, 17, 55) / 1000)).toBe("23:25");
  });
  it("durations are human readable", () => {
    expect(fmtDuration(6583)).toBe("1 h 49 min"); expect(fmtDuration(45)).toBe("45 s"); expect(fmtDuration(180)).toBe("3 min"); expect(fmtDuration(90000)).toBe("1 d 1 h");
  });
  it("Rainwater: ultrasonic out of range is not a sensor error", async () => {
    const base = { id: 1, bootId: 1, seq: 1, rain: false, waterLevel: null, levelValid: false, distance: null, turbidity: 3000, turbidityValid: true, waterClear: true, tankFull: false, valveOpen: false, valveState: 6, valveReason: 0,
      firstFlush: false, buzzer: false, dryRun: true, faults: 0, rainEvents: 0, uptime: 100, recovered: false, channel: 9, rssi: -50, measuredAt: "2026-11-15 17:55:00", receivedAt: "2026-11-15 17:55:00", ageSeconds: 3, active: true };
    mocks.api.getRainwaterLive = vi.fn(async () => ({ rainwater: { ...base, outOfRange: true } }));
    mocks.api.getRainwaterHistory = vi.fn(async () => [{ ...base, outOfRange: true }]);
    const { unmount } = wrap(<RainwaterHarvesting />);
    expect((await screen.findAllByText("Out of range")).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Sensor error/i)).not.toBeInTheDocument();
    expect(screen.getAllByText("23:25:00", { exact: false }).length).toBeGreaterThan(0);        // history timestamp in local time
    unmount();
    mocks.api.getRainwaterLive = vi.fn(async () => ({ rainwater: { ...base, outOfRange: false } }));
    wrap(<RainwaterHarvesting />);
    expect((await screen.findAllByText("Sensor error")).length).toBeGreaterThan(0);
  });
});
