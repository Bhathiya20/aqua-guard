import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ role: "admin" as "admin" | "user", v2: {} as any }));
vi.mock("../lib/v2", async (orig) => ({ ...(await orig<typeof import("../lib/v2")>()), v2: mocks.v2 }));
vi.mock("../lib/auth", () => ({ useAuth: () => ({ user: { role: mocks.role, email: "a@b.c" } }) }));

import { AlertsPage, alertState } from "./AlertsPage";
import { toCsv } from "./AnalyticsPage";
import { LeakDetectionDashboard } from "./LeakDetectionDashboard";
import { OverviewPage } from "./OverviewPage";

const gw = { id: 1, gatewayId: "gw", state: "online", timeSynced: true, heartbeatAgeS: 3 };
const mkNode = (n: number, over: any = {}) => ({ id: n, type: "flow", number: n, name: `N${n}`, state: "online", registryState: "paired", batteryMv: 7400, live: { flowLpm: 1.5, todayLiters: 10, weekLiters: 20 }, ...over });
const alert = (id: number, kind: string, over: any = {}) => ({ id, key: `k${id}`, kind, severity: "warning", message: `msg ${id}`, active: true, createdAt: "2026-01-01 10:00:00", clearedAt: null, acknowledgedBy: null, acknowledgedAt: null, nodeRef: null, zone: null, pipelineId: null, pipelineVersion: null, state: null, ...over });
const zone = (over: any = {}) => ({ id: "zone:flow2", name: "After Node 2", class: "strong", status: "suspected", level: 1, reason: "Unaccounted flow", equation: "", inMeter: "Node 2", outMeters: [], outlets: [], outletIds: [], pipes: [], parts: [], balance: { qIn: 5, qOut: 2, unaccounted: 3, thresholdLpm: 0.5, bucketTs: 1, confidence: "high" },
  meterFaultHint: null, suggestions: [], parent: null, rules: [{ rule: "balance", level: 1, clear: false, reason: "3 L/min unaccounted" }, { rule: "night_flow", level: 0, clear: true, reason: "" }], completeBuckets: 2, finalBuckets: 2, nodesMissing: [], ...over });

beforeEach(() => {
  mocks.role = "admin";
  Object.assign(mocks.v2, {
    summary: vi.fn(async () => ({ gateway: gw, nodes: { total: 4, online: 3, offline: 1 }, total: { basis: "source_meter", flowLpm: 4.25, todayLiters: 120, weekLiters: 800, node: 2, pipelineId: 1 }, pipeline: { id: 1, name: "House" },
      leaks: { suspected: 1, confirmed: 0 }, otherAlerts: 1, battery: { withBattery: 2, noBattery: 2, low: [{ number: 4, name: "Low one", mv: 6100 }], lowLimitMv: 6600 } })),
    nodes: vi.fn(async () => ({ nodes: [mkNode(2), mkNode(3, { batteryMv: null })] })),
    alerts: vi.fn(async () => [alert(1, "leak_suspected", { zone: "zone:flow2", pipelineId: 1, pipelineVersion: 2, severity: "critical" }), alert(2, "node_offline", { nodeRef: 2 }), alert(3, "low_battery", { active: false, clearedAt: "2026-01-01 11:00:00" })]),
    pipelines: vi.fn(async () => ({ pipelines: [{ id: 1, name: "House", activeVersion: 2, versions: 2, updatedAt: null }] })),
    pipelineStatus: vi.fn(async () => ({ pipelineId: 1, name: "House", version: 2, zones: [zone(), zone({ id: "zone:source", name: "Before Node 2", class: "not_monitored", status: "not_monitored", balance: null, rules: [], reason: "No inflow node" })], coverage: { text: "1 of 2 zones leak-monitored" }, suggestions: [{ text: "Add a node on Pipe 1." }], live: {} })),
    zoneLeaks: vi.fn(async () => ({ leaks: [] })),
    ack: vi.fn(async () => ({ ok: true })), resolve: vi.fn(async () => ({ ok: true })),
  });
});
const wrap = (el: React.ReactNode) => render(<MemoryRouter>{el}</MemoryRouter>);

describe("Overview", () => {
  it("shows gateway, node counts, total flow at the main supply, leaks, battery summary and the Live link", async () => {
    wrap(<OverviewPage />);
    expect((await screen.findAllByText("online")).length).toBeGreaterThan(0);
    expect(screen.getByText(/time synced/)).toBeInTheDocument();
    expect(screen.getByText("3 / 4")).toBeInTheDocument();
    expect(screen.getByText("4.25")).toBeInTheDocument();
    expect(screen.getByText(/main supply \(Node 2\)/)).toBeInTheDocument();
    expect(screen.getByText("1 suspected")).toBeInTheDocument();
    expect(screen.getByText(/Node 4 \(Low one\): 6\.10 V/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /pipeline live view/i })).toHaveAttribute("href", "/pipelines/1?mode=live");
    expect((await screen.findAllByText("No battery")).length).toBeGreaterThan(0);          // a node without a battery sensor, never "0 V"
  });
  it("without a pipeline it says how to enable leak detection", async () => {
    mocks.v2.summary.mockResolvedValueOnce({ gateway: null, nodes: { total: 0, online: 0, offline: 0 }, total: { basis: "none", flowLpm: null, todayLiters: null, weekLiters: null, node: null, pipelineId: null }, pipeline: null, leaks: { suspected: 0, confirmed: 0 }, otherAlerts: 0, battery: { withBattery: 0, noBattery: 0, low: [], lowLimitMv: 6600 } });
    wrap(<OverviewPage />);
    expect(await screen.findByText(/No gateway has reported yet/)).toBeInTheDocument();
    expect(screen.getByText(/needs a pipeline/)).toBeInTheDocument();
  });
});

describe("Alerts", () => {
  it("lists alerts with their life-cycle state and filters by type and status", async () => {
    const user = userEvent.setup();
    wrap(<AlertsPage />);
    expect(await screen.findByTestId("alert-1")).toHaveTextContent(/Leak suspected/);
    expect(screen.getByTestId("alert-2")).toHaveTextContent(/Node offline/);
    expect(screen.queryByTestId("alert-3")).not.toBeInTheDocument();                       // cleared: hidden by the default "Active" filter
    await user.selectOptions(screen.getByLabelText("Status"), "all");
    expect(screen.getByTestId("alert-3")).toHaveTextContent(/cleared/);
    await user.selectOptions(screen.getByLabelText("Type"), "node_offline");
    expect(screen.queryByTestId("alert-1")).not.toBeInTheDocument();
    expect(screen.getByTestId("alert-2")).toBeInTheDocument();
  });
  it("admins can acknowledge and resolve by hand: resolving asks for a reason first", async () => {
    const user = userEvent.setup();
    wrap(<AlertsPage />);
    const leak = await screen.findByTestId("alert-1");
    expect(within(leak).getByRole("button", { name: /acknowledge/i })).toBeInTheDocument();
    expect(within(leak).getByRole("button", { name: /resolve/i })).toBeInTheDocument();          // leak events too: the reason says "fixed leak" / "false alarm"
    expect(within(leak).getByRole("link", { name: /live view/i })).toHaveAttribute("href", "/pipelines/1?mode=live&zone=zone%3Aflow2");
    await user.click(within(screen.getByTestId("alert-2")).getByRole("button", { name: /resolve/i }));
    expect(mocks.v2.resolve).not.toHaveBeenCalled();                                                // nothing is sent before a reason is chosen
    const dlg = await screen.findByTestId("resolve-dialog");
    expect(within(dlg).getByTestId("resolve-confirm")).toBeDisabled();
    await user.click(within(dlg).getByLabelText("Tap left open"));
    await user.type(within(dlg).getByLabelText("Note"), "closed it");
    await user.click(within(dlg).getByTestId("resolve-confirm"));
    await waitFor(() => expect(mocks.v2.resolve).toHaveBeenCalledWith(2, "tap_left_open", "closed it"));
  });
  it("a normal user sees no action buttons", async () => {
    mocks.role = "user";
    wrap(<AlertsPage />);
    await screen.findByTestId("alert-1");
    expect(screen.queryByRole("button", { name: /acknowledge|resolve/i })).not.toBeInTheDocument();
  });
  it("describes the leak life cycle", () => {
    expect(alertState(alert(1, "leak_suspected") as any).text).toBe("suspected");
    expect(alertState(alert(1, "leak_confirmed") as any).text).toBe("confirmed");
    expect(alertState(alert(1, "leak_confirmed", { active: false }) as any).text).toBe("resolved");
  });
});

describe("Leak detection", () => {
  it("shows each zone with class, status, Q in / out / unaccounted, rules and a link to the Live view", async () => {
    wrap(<LeakDetectionDashboard />);
    const z = await screen.findByTestId("leak-zone-zone:flow2");
    expect(z).toHaveTextContent("Suspected leak");
    expect(z).toHaveTextContent(/strong/);
    expect(z).toHaveTextContent("5.00");
    expect(z).toHaveTextContent("3.00");
    expect(z).toHaveTextContent("Balance (in vs out)");
    expect(within(z).getByRole("link", { name: /live view/i })).toHaveAttribute("href", "/pipelines/1?mode=live&zone=zone%3Aflow2");
    expect(screen.getByTestId("leak-zone-zone:source")).toHaveTextContent(/not monitored/);
    expect(screen.getByText("Add a node on Pipe 1.")).toBeInTheDocument();
  });
  it("without an active pipeline there is an explanation instead of a Node 1 vs Node 2 comparison", async () => {
    mocks.v2.pipelines.mockResolvedValue({ pipelines: [] });
    wrap(<LeakDetectionDashboard />);
    expect(await screen.findByText(/No pipeline is active/)).toBeInTheDocument();
  });
});

describe("CSV export", () => {
  it("quotes commas, quotes and line breaks and keeps the header row", () => {
    expect(toCsv([{ a: 1, b: 'say "hi", ok' }, { a: 2, b: null }])).toBe('a,b\r\n1,"say ""hi"", ok"\r\n2,\r\n');
    expect(toCsv([])).toBe("");
  });
});
