import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ v2: {} as any, role: "admin" as "admin" | "user" }));
vi.mock("../../lib/v2", async (orig) => ({ ...(await orig<typeof import("../../lib/v2")>()), v2: mocks.v2 }));
vi.mock("../../lib/auth", () => ({ useAuth: () => ({ user: { role: mocks.role, email: "a@b.c" } }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { LeakDetectionDashboard } from "../LeakDetectionDashboard";

const node = (n: number, over: any = {}) => ({ partId: `n${n}`, number: n, label: `Node ${n}`, name: `Name ${n}`, nodeRef: n, state: "online", lastSeen: "2026-01-01 12:00:00", flowLpm: n === 1 ? 3.2 : 1.1, ageS: 2, ...over });
const settings = { thresholdLpm: 0.3, tolerancePct: 10, suspectMin: 10, confirmMin: 30, resolveMin: 5, bucketS: 60, nightStart: "02:00", nightEnd: "04:00", continuousSuspectMin: 360, continuousConfirmMin: 720, tz: "Asia/Colombo", tzOffsetMin: 330, demo: false };
const strong = {
  id: "zone:flow1", name: "After Node 1", class: "strong", status: "normal", level: 0, reason: "Has a sensor at its inlet and no outlets.", equation: "Q[Node 1] - (Q[Node 2])", inMeter: "Node 1", outMeters: ["Node 2"], outlets: [], outletIds: [],
  pipes: [], parts: [], balance: { qIn: 3.1, qOut: 1.0, unaccounted: 2.1, thresholdLpm: 0.31, bucketTs: 1, confidence: "high" }, meterFaultHint: null, suggestions: [], parent: null,
  rules: [{ rule: "balance", level: 0, clear: true, reason: "", progress: { text: "ok", value: null, needed: null, unit: "min", counting: false } }], settings, completeBuckets: 5, finalBuckets: 5, nodesMissing: [],
  inflow: [node(1)], outflow: [node(2)], outletCount: 0,
};
const weak = { ...strong, id: "zone:flow2", name: "After Node 2", class: "weak", reason: "Outlets inside.", inflow: [node(2, { state: "offline", flowLpm: null })], outflow: [], outletCount: 2, balance: null, rules: [] };
const pipelines = [
  { id: 1, name: "My house", activeVersion: 3, versions: 3, updatedAt: null, preset: null },
  { id: 7, name: "Leak test bench", activeVersion: null, versions: 1, updatedAt: null, preset: "leak_bench" },
  { id: 9, name: "Calibration rig 2026", activeVersion: null, versions: 1, updatedAt: null, preset: "calibration_rig" },
];
const wrap = () => render(<MemoryRouter><LeakDetectionDashboard /></MemoryRouter>);

beforeEach(() => {
  mocks.role = "admin";
  Object.assign(mocks.v2, {
    pipelines: vi.fn(async () => ({ pipelines })),
    pipelineStatus: vi.fn(async () => ({ pipelineId: 1, name: "My house", version: 3, live: {}, coverage: { text: "" }, suggestions: [], zones: [strong, weak],
      nodes: [{ ...node(1), zones: [{ id: "zone:flow1", name: "After Node 1", role: "in" }] }, { ...node(2, { state: "offline", flowLpm: null, lastSeen: null }), zones: [{ id: "zone:flow1", name: "After Node 1", role: "out" }, { id: "zone:flow2", name: "After Node 2", role: "in" }] }] })),
    zoneLeaks: vi.fn(async () => ({ leaks: [] })),
    versions: vi.fn(async () => ({ activeVersion: null, versions: [{ version: 1, valid: true, note: "", createdBy: "", createdAt: "", active: false }] })),
    activate: vi.fn(async () => ({ ok: true, activeVersion: 1 })),
  });
});

const more = async (id: string) => { await userEvent.click(await screen.findByTestId(`zone-more-${id}`)); };

describe("Leak Detection page", () => {
  it("first view is compact: status and one line per zone, everything else is collapsed", async () => {
    wrap();
    expect(await screen.findByTestId("zone-summary-zone:flow1")).toHaveTextContent("In 3.10 · out 1.00 L/min");
    expect(screen.queryByTestId("comparison-zone:flow1")).not.toBeInTheDocument();
    expect(screen.queryByTestId("zone-diagram-zone:flow1")).not.toBeInTheDocument();
    expect(screen.queryByTestId("leak-node-table")).not.toBeInTheDocument();
    expect(screen.queryByTestId("usage-live")).not.toBeInTheDocument();
    expect(screen.getByTestId("section-toggle-history")).toHaveAttribute("aria-expanded", "false");
  });

  it("More details opens the comparison and the rules; Settings opens the zone settings dialog with the time rules", async () => {
    mocks.v2.zoneTime = vi.fn(async () => ({ quietStart: "22:00", quietEnd: "05:00", continuousSuspectH: 6, continuousConfirmH: 12, overridden: false,
      source: { quietStart: "default", quietEnd: "default", continuousSuspectH: "default", continuousConfirmH: "default" }, defaults: { quietStart: "22:00", quietEnd: "05:00", continuousSuspectH: 6, continuousConfirmH: 12 } }));
    mocks.v2.zoneThresholds = vi.fn(async () => ({ zone: "zone:flow1", strong: true, xPlusLpm: 0.3, xMinusLpm: 0.3, tLeakS: 30, windowS: 5, defaults: { xPlusLpm: 0.3, xMinusLpm: 0.3, tLeakS: 30, demoTLeakS: 10 }, custom: false, source: null, updatedAt: null, updatedBy: null }));
    wrap();
    await more("zone:flow1");
    expect(screen.getByTestId("zone-details-zone:flow1")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("zone-settings-btn-zone:flow1"));
    const dlg = await screen.findByTestId("zone-settings-dialog-zone:flow1");
    expect(await within(dlg).findByTestId("time-settings-zone:flow1")).toHaveTextContent("all values inherited");
    expect(within(dlg).getByTestId("balance-settings-zone:flow1")).toBeInTheDocument();
  });

  it("a weak zone has settings too and says in one line why there are no balance thresholds", async () => {
    mocks.v2.zoneTime = vi.fn(async () => ({ quietStart: "23:00", quietEnd: "04:00", continuousSuspectH: 4, continuousConfirmH: 12, overridden: true,
      source: { quietStart: "zone", quietEnd: "zone", continuousSuspectH: "zone", continuousConfirmH: "default" }, defaults: { quietStart: "22:00", quietEnd: "05:00", continuousSuspectH: 6, continuousConfirmH: 12 } }));
    mocks.v2.resetZoneTime = vi.fn(async () => ({}));
    wrap();
    await more("zone:flow2");
    await userEvent.click(screen.getByTestId("zone-settings-btn-zone:flow2"));
    expect(await screen.findByTestId("no-balance-why")).toHaveTextContent("No balance thresholds here");
    const t = await screen.findByTestId("time-settings-zone:flow2");
    await waitFor(() => expect(t).toHaveAttribute("data-overridden", "1"));
    expect(t).toHaveTextContent("overridden");
    await userEvent.click(within(t).getByTestId("reset-time-zone:flow2"));
    await waitFor(() => expect(mocks.v2.resetZoneTime).toHaveBeenCalledWith(1, "zone:flow2"));
  });

  it("shows the comparison of each zone: inflow, outflow, formula with numbers, tolerance, status and a diagram", async () => {
    wrap();
    await more("zone:flow1");
    const c = await screen.findByTestId("comparison-zone:flow1");
    expect(within(c).getByTestId("cmp-zone:flow1-1")).toHaveTextContent("Node 1");
    expect(within(c).getByTestId("cmp-zone:flow1-1")).toHaveTextContent("3.20");
    expect(within(c).getByTestId("cmp-zone:flow1-2")).toHaveTextContent("1.10");
    expect(within(c).getByText("Q_in − Σ Q_out = unaccounted")).toBeInTheDocument();
    expect(screen.getByTestId("formula-live-zone:flow1")).toHaveTextContent("3.20 − 1.10 = 2.10 L/min");
    expect(screen.getByTestId("formula-bucket-zone:flow1")).toHaveTextContent("3.10 − 1.00 = 2.10");
    expect(screen.getByTestId("tolerance-zone:flow1")).toHaveTextContent("0.3 L/min");
    expect(screen.getByTestId("tolerance-zone:flow1")).toHaveTextContent("10 %");
    expect(screen.getByTestId("zone-diagram-zone:flow1")).toBeInTheDocument();
    expect(screen.getByTestId("leak-zone-zone:flow1")).toHaveTextContent("Normal");
    expect(screen.getByTestId("rule-progress")).toBeInTheDocument();
  });

  it("a weak zone says where the water goes and a node without data shows a dash, never zero", async () => {
    wrap();
    await more("zone:flow2");
    const w = await screen.findByTestId("comparison-zone:flow2");
    expect(w).toHaveTextContent("the water leaves through unmeasured outlets");
    expect(within(w).getByTestId("cmp-zone:flow2-2")).toHaveTextContent("—");
    expect(screen.getByTestId("zone-diagram-zone:flow2")).toHaveTextContent("2 outlets (unmeasured)");
  });

  it("the node table lists every node with live flow, state, last seen and the zones it feeds (in / out)", async () => {
    wrap();
    await userEvent.click(await screen.findByTestId("section-toggle-nodes"));
    const t = await screen.findByTestId("leak-node-table");
    const r1 = within(t).getByTestId("leak-node-1"), r2 = within(t).getByTestId("leak-node-2");
    expect(r1).toHaveTextContent("3.20 L/min"); expect(r1).toHaveTextContent("online"); expect(r1).toHaveTextContent("in · After Node 1");
    expect(r2).toHaveTextContent("offline"); expect(r2).toHaveTextContent("never");
    expect(r2).toHaveTextContent("out · After Node 1"); expect(r2).toHaveTextContent("in · After Node 2");
  });

  it("admin: the selector lists the pipelines, the calibration rig is not selectable, switching asks for confirmation then activates", async () => {
    const user = userEvent.setup();
    wrap();
    const sel = await screen.findByLabelText("Leak detection runs on:");
    expect(sel).toHaveValue("1");
    expect(within(sel).getByRole("option", { name: /Calibration rig 2026 \(calibration rig/ })).toBeDisabled();
    await user.selectOptions(sel, "7");
    expect(await screen.findByText(/Run leak detection on “Leak test bench”/)).toBeInTheDocument();
    expect(mocks.v2.activate).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(mocks.v2.activate).not.toHaveBeenCalled();
    await user.selectOptions(sel, "7");
    await user.click(await screen.findByRole("button", { name: "Switch" }));
    await waitFor(() => expect(mocks.v2.activate).toHaveBeenCalledWith(7, 1));
  });

  it("users see the selector read-only", async () => {
    mocks.role = "user";
    wrap();
    const sel = await screen.findByLabelText("Leak detection runs on:");
    expect(sel).toBeDisabled();
    expect(screen.getByText(/Only an administrator can switch/)).toBeInTheDocument();
  });

  it("with no active pipeline the admin can still pick one", async () => {
    mocks.v2.pipelines = vi.fn(async () => ({ pipelines: pipelines.map((p) => ({ ...p, activeVersion: null })) }));
    mocks.v2.pipelineStatus = vi.fn(async () => null);
    wrap();
    const sel = await screen.findByLabelText("Leak detection runs on:");
    expect(sel).toBeEnabled();
    expect(within(sel).getByRole("option", { name: "(no pipeline active)" })).toBeInTheDocument();
    expect(screen.getByText("Not configured")).toBeInTheDocument();
  });
});
