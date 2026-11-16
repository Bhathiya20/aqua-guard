import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ v2: {} as any, api: {} as any }));
vi.mock("../lib/v2", async (orig) => ({ ...(await orig<typeof import("../lib/v2")>()), v2: mocks.v2 }));
vi.mock("../lib/api", async (orig) => ({ ...(await orig<typeof import("../lib/api")>()), api: mocks.api }));
vi.mock("../lib/auth", () => ({ useAuth: () => ({ user: { role: "admin", email: "a@b.c" } }) }));

import { AlertsPage } from "./AlertsPage";
import { bucketKeys, bucketLabel, fillBuckets } from "./AnalyticsPage";
import { OverviewPage } from "./OverviewPage";
import { StatusStrip, setupSteps } from "./StatusStrip";
import { fmtAge, fmtDuration } from "../lib/usePolling";
import { leakCheck, nodeTitle } from "../lib/v2";

const summary = (over: any = {}) => ({ gateway: { id: 1, gatewayId: "gw", state: "online", timeSynced: true, heartbeatAgeS: 3 }, gateways: { total: 1, online: 1 }, nodes: { total: 4, online: 3, offline: 1 },
  total: { basis: "none", flowLpm: null, todayLiters: null, weekLiters: null, node: null, pipelineId: null }, pipeline: null, leaks: { suspected: 0, confirmed: 0 }, otherAlerts: 0,
  battery: { withBattery: 0, noBattery: 4, low: [], lowLimitMv: 6600 }, ...over });
const alert = (id: number, kind: string, over: any = {}) => ({ id, key: `${kind}:${id}`, kind, severity: "warning", message: `${kind} #${id}`, active: true, createdAt: "2026-11-15 10:00:00", clearedAt: null,
  acknowledgedBy: null, acknowledgedAt: null, nodeRef: null, zone: null, pipelineId: null, pipelineVersion: null, state: null, ...over });
const wrap = (el: React.ReactNode) => render(<MemoryRouter>{el}</MemoryRouter>);

beforeEach(() => {
  Object.assign(mocks.v2, {
    summary: vi.fn(async () => summary()), nodes: vi.fn(async () => ({ nodes: [] })), alerts: vi.fn(async () => []), pipelines: vi.fn(async () => ({ pipelines: [] })),
    pipelineStatus: vi.fn(async () => null), ack: vi.fn(async () => ({})), resolve: vi.fn(async () => ({})),
  });
  Object.assign(mocks.api, { getRainwaterLive: vi.fn(async () => ({ rainwater: null })) });
});

describe("durations and names", () => {
  it("durations are human readable everywhere", () => {
    expect(fmtDuration(6583)).toBe("1 h 49 min");
    expect(fmtAge(6583)).toBe("1 h 49 min ago");
    expect(fmtAge(null)).toBe("never");
    expect(fmtAge(12)).toBe("12 s ago");
  });
  it("rain nodes are called Rain 1, flow nodes Node 2", () => {
    expect(nodeTitle({ number: 1, name: "Rain 1", type: "rain" })).toBe("Rain 1");
    expect(nodeTitle({ number: 2, name: "Main", type: "flow" })).toBe("Node 2 - Main");
    expect(nodeTitle({ number: 2, name: "" })).toBe("Node 2");
  });
});

describe("analytics buckets", () => {
  const now = new Date(Date.UTC(2026, 10, 15, 17, 55));            // Sun 15 Nov 2026 17:55 UTC = 23:25 Colombo
  it("daily: every day of the range is present, empty ones are 0, labels are short", () => {
    const rows = fillBuckets("day", [{ bucket: "2026-11-10", liters: 12 }], (k) => ({ bucket: k, liters: 0 }), now, 7);
    expect(rows.map((r) => r.bucket)).toEqual(["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-14", "2026-11-15"]);
    expect(rows.find((r) => r.bucket === "2026-11-10")!.liters).toBe(12);
    expect(rows.filter((r) => r.liters === 0)).toHaveLength(6);
    expect(bucketLabel("day", "2026-11-15")).toMatch(/Sun.*15/);
  });
  it("hourly labels are local clock times; monthly labels are month names", () => {
    expect(bucketKeys("hour", now, 1)).toHaveLength(24);
    expect(bucketLabel("hour", "2026-11-15 17:00:00")).toBe("22:30");           // UTC 17:00 = 22:30 in Colombo
    expect(bucketLabel("month", "2026-11")).toBe("Nov");
    expect(bucketKeys("month", now, 365)).toHaveLength(12);
  });
});

describe("status strip and setup progress", () => {
  it("the strip shows gateway, nodes online and an honest leak check on every page", async () => {
    mocks.v2.pipelines = vi.fn(async () => ({ pipelines: [{ id: 1, name: "House", activeVersion: 1, versions: 1, updatedAt: null }] }));
    mocks.v2.pipelineStatus = vi.fn(async () => ({ pipelineId: 1, name: "House", version: 1, live: {}, coverage: {}, suggestions: [], zones: [{ id: "a", name: "A", class: "strong", status: "incomplete", nodesMissing: [], nodesOffline: ["Node 3"], parts: [], rules: [] }] }));
    wrap(<StatusStrip />);
    const strip = await screen.findByTestId("status-strip");
    await waitFor(() => expect(strip).toHaveTextContent("3 / 4 online"));
    await waitFor(() => expect(strip).toHaveTextContent("paused (no data)"));
  });
  it("steps: gateway online, nodes added, pipeline drawn, activated, leak check running", () => {
    const s = summary() as any;
    const steps = (sum: any, n: number, act: boolean, z: any[]) => setupSteps(sum, n, act, leakCheck(z)).map((x) => x.done);
    expect(steps(s, 0, false, [])).toEqual([true, true, false, false, false]);
    expect(steps({ ...s, gateway: null, nodes: { total: 0, online: 0, offline: 0 } }, 0, false, [])).toEqual([false, false, false, false, false]);
    const ok = [{ class: "strong", status: "normal" }] as any, paused = [{ class: "strong", status: "incomplete" }] as any;
    expect(steps(s, 1, true, ok)).toEqual([true, true, true, true, true]);
    expect(steps(s, 1, true, paused)).toEqual([true, true, true, true, false]);          // paused is not running
  });
  it("Overview shows the Setup progress card until everything is done", async () => {
    wrap(<OverviewPage />);
    const card = await screen.findByTestId("setup-progress");
    expect(card).toHaveTextContent("Setup progress");
    expect(within(card).getByTestId("step-gateway")).toHaveAttribute("data-done", "1");
    expect(within(card).getByTestId("step-pipeline")).toHaveAttribute("data-done", "0");
  });
});

describe("Overview rainwater card", () => {
  it("appears only when rainwater data exists", async () => {
    const { unmount } = wrap(<OverviewPage />);
    await screen.findByTestId("setup-progress");
    expect(screen.queryByTestId("ov-rainwater")).not.toBeInTheDocument();
    unmount();
    mocks.api.getRainwaterLive = vi.fn(async () => ({ rainwater: { rain: true, waterLevel: 62, levelValid: true, outOfRange: false, turbidityValid: true, waterClear: true, valveOpen: false, valveState: 0, dryRun: true, active: true } }));
    wrap(<OverviewPage />);
    const c = await screen.findByTestId("ov-rainwater");
    expect(c).toHaveTextContent("62 %");
    expect(c).toHaveTextContent("Raining");
    expect(c).toHaveTextContent("Clear");
    expect(c).toHaveTextContent("dry-run");
  });
});

describe("alerts grouping", () => {
  it("repeated alerts of one type are grouped with Acknowledge all and Resolve all", async () => {
    const user = userEvent.setup();
    mocks.v2.alerts = vi.fn(async () => [alert(1, "node_offline"), alert(2, "node_offline"), alert(3, "node_offline"), alert(4, "low_battery")]);
    wrap(<AlertsPage />);
    const g = await screen.findByTestId("alert-group-node_offline");
    expect(within(g).getByTestId("group-count")).toHaveTextContent("3 alerts");
    expect(within(g).getAllByTestId(/^alert-\d+$/)).toHaveLength(1);                      // collapsed: newest only
    expect(screen.getByTestId("alert-group-low_battery")).not.toHaveTextContent("alerts");
    await user.click(within(g).getByRole("button", { name: /Acknowledge all/ }));
    await waitFor(() => expect(mocks.v2.ack).toHaveBeenCalledTimes(3));
    await user.click(within(g).getByRole("button", { name: /Resolve all/ }));
    await user.click(await screen.findByLabelText("Test"));
    await user.click(screen.getByTestId("resolve-confirm"));
    await waitFor(() => expect(mocks.v2.resolve).toHaveBeenCalledTimes(3));
    expect(mocks.v2.resolve).toHaveBeenCalledWith(1, "test", "");
    await user.click(within(g).getByRole("button", { name: "show all" }));
    expect(within(g).getAllByTestId(/^alert-\d+$/)).toHaveLength(3);
  });
  it("leak alerts can be acknowledged and resolved by hand (with a reason)", async () => {
    mocks.v2.alerts = vi.fn(async () => [alert(1, "leak_confirmed", { severity: "critical" }), alert(2, "leak_confirmed", { severity: "critical" })]);
    wrap(<AlertsPage />);
    const g = await screen.findByTestId("alert-group-leak_confirmed");
    expect(within(g).getByRole("button", { name: /Acknowledge all/ })).toBeInTheDocument();
    expect(within(g).getByRole("button", { name: /Resolve all/ })).toBeInTheDocument();
  });
});

describe("out of service nodes", () => {
  const zone = (over: any) => ({ id: "z", name: "House", class: "strong", status: "incomplete", nodesMissing: [], nodesOffline: [], nodesOutOfService: [], parts: [], rules: [], ...over });
  it("a zone whose only gap is an out-of-service node is excluded, not paused and not silently fine", () => {
    const lc = leakCheck([zone({ nodesOutOfService: ["Node 2"] }), zone({ id: "b", name: "Garden", status: "normal" })] as any);
    expect(lc.state).toBe("ok");
    expect(lc.note).toContain("House not monitored");
    expect(lc.note).toContain("Node 2 out of service");
    expect(leakCheck([zone({ nodesOutOfService: ["Node 2"] })] as any).state).toBe("none");               // nothing left to monitor: never green
    expect(leakCheck([zone({ nodesOutOfService: ["Node 2"], nodesOffline: ["Node 3"] })] as any).state).toBe("paused");   // another node is really offline
  });
});
