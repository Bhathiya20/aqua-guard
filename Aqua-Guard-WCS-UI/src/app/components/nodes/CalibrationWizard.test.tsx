import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CalSession, NodeV2 } from "../../lib/v2";

const mocks = vi.hoisted(() => ({ role: "admin" as "admin" | "user", v2: {} as any, session: null as any, active: { active: false } as any }));
vi.mock("../../lib/v2", async (orig) => ({ ...(await orig<typeof import("../../lib/v2")>()), v2: mocks.v2 }));
vi.mock("../../lib/auth", () => ({ useAuth: () => ({ user: { role: mocks.role, email: "a@b.c" } }) }));

import { CalibrationWizard } from "./CalibrationWizard";
import { StatusStrip } from "../StatusStrip";
import { leakCheck, type LiveZone } from "../../lib/v2";

const node = (n: number, over: Partial<NodeV2> = {}): NodeV2 => ({
  id: n, gatewayRef: 1, type: "flow", number: n, label: `Node ${n}`, name: `Name ${n}`, mac: `AA:BB:CC:00:00:0${n}`, state: "online", registryState: "paired",
  batteryMv: null, rssi: -50, lastSeen: "2026-01-01 12:00:00", pulsesPerLiter: 450, txReduce: false, addedEpoch: 1, hardware: [], live: null, ...over,
});
const cn = (id: number, over: any = {}) => ({ id, number: id, label: `Node ${id}`, name: "", order: id - 1, currentPpl: 450, isDefault: true, outOfService: false, idle: { ok: true, reason: "" }, live: { flowLpm: 0, cumPulses: 100, ageS: 1 }, pulsesInRun: null, ...over });
const stat = (id: number, over: any = {}) => ({ id, label: `Node ${id}`, runs: 3, meanPpl: 452, medianPpl: 451, weightedPpl: 452.3, spreadPct: 1.2, quality: "good", current: 450, ...over });
const session = (over: Partial<CalSession> = {}): CalSession => ({
  id: 5, status: "running", createdAt: "2026-01-01 12:00:00", endedAt: null, pipelineId: 9, nodes: [cn(2), cn(3)], runs: [], activeRunId: null, canStart: true,
  results: { runs: [], nodes: [stat(2), stat(3)], warnings: [], validRuns: 0 },
  proposals: { "2": { weighted: { value: 452.3, verdict: "ok", message: "" }, median: { value: 451, verdict: "ok", message: "" } }, "3": { weighted: { value: 452.3, verdict: "ok", message: "" }, median: { value: 451, verdict: "ok", message: "" } } },
  recommended: { minRuns: 3, maxRuns: 5, minLiters: 2 }, limits: { min: 300, max: 700, confirmPct: 15 }, ...over,
} as CalSession);

const renderWizard = () => render(<MemoryRouter><CalibrationWizard /></MemoryRouter>);

beforeEach(() => {
  mocks.role = "admin"; mocks.active = { active: false }; mocks.session = null;
  Object.assign(mocks.v2, {
    nodes: vi.fn(async () => ({ nodes: [node(2), node(3), node(4, { state: "out_of_service" })] })),
    calibrationActive: vi.fn(async () => mocks.active),
    calibration: vi.fn(async () => mocks.session),
    calibrationCreate: vi.fn(async () => { mocks.session = session(); mocks.active = { active: true, sessionId: 5 }; return mocks.session; }),
    calibrationStart: vi.fn(async () => (mocks.session = session({ activeRunId: 1, canStart: false, runs: [{ id: 1, idx: 1, status: "active", volumeL: null, durationS: null, reason: null, startedAt: "", stopRequestedAt: null, stoppedAt: null, waitingFor: [], cells: {} }] }))),
    calibrationStop: vi.fn(async () => (mocks.session = session({ activeRunId: 1, runs: [{ id: 1, idx: 1, status: "stopping", volumeL: null, durationS: null, reason: null, startedAt: "", stopRequestedAt: "", stoppedAt: null, waitingFor: ["Node 3"], cells: {} }] }))),
    calibrationVolume: vi.fn(async () => mocks.session),
    calibrationDeleteRun: vi.fn(async () => session()),
    calibrationCancel: vi.fn(async () => session({ status: "cancelled" })),
    calibrationApply: vi.fn(async () => ({ ok: true, status: "done", results: [{ nodeId: 2, label: "Node 2", commandId: 11, valueX10: 4523, error: null }, { nodeId: 3, label: "Node 3", commandId: 12, valueX10: 4523, error: null }] })),
    command: vi.fn(async (id: number) => ({ id, op: "C", status: "ok", result: "ok", rc: 0, number: 2, created_at: "", resolved_at: "" })),
    summary: vi.fn(async () => ({ gateway: { state: "online" }, nodes: { total: 2, online: 2, offline: 0 } })),
    pipelines: vi.fn(async () => ({ pipelines: [] })),
  });
});

describe("Calibration wizard", () => {
  it("selects nodes (out-of-service ones are left out), reorders them and creates the session in that order", async () => {
    const user = userEvent.setup();
    renderWizard();
    await screen.findByText("Available nodes");
    expect(screen.queryByText(/Node 4/)).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Add Node 2" }));
    await user.click(screen.getByRole("button", { name: "Add Node 3" }));
    expect(screen.getByTestId("rig-diagram")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Move Node 2 down" }));                    // order is now Node 3, Node 2
    expect(within(screen.getByTestId("rig-order")).getAllByRole("listitem").map((li) => li.textContent)).toEqual([expect.stringContaining("Node 3"), expect.stringContaining("Node 2")]);
    expect(screen.getByText(/leak detection is paused/i)).toBeInTheDocument();
    await user.click(screen.getByTestId("start-session"));
    await waitFor(() => expect(mocks.v2.calibrationCreate).toHaveBeenCalledWith([3, 2]));
    expect(await screen.findByText(/never takes part in leak detection/i)).toBeInTheDocument();
  });

  it("start is disabled until all nodes are idle, then run -> stop shows what it waits for", async () => {
    const user = userEvent.setup();
    mocks.active = { active: true, sessionId: 5 };
    mocks.session = session({ canStart: false, nodes: [cn(2), cn(3, { idle: { ok: false, reason: "water is flowing" } })] });
    renderWizard();
    const start = await screen.findByTestId("run-start");
    expect(start).toBeDisabled();
    expect(screen.getByTestId("start-blocked")).toHaveTextContent("Node 3 (water is flowing)");
    mocks.session = session({ canStart: true });
    await waitFor(() => expect(screen.getByTestId("run-start")).toBeEnabled(), { timeout: 4000 });
    await user.click(screen.getByTestId("run-start"));
    await user.click(await screen.findByTestId("run-stop"));
    expect(await screen.findByTestId("run-stopping")).toHaveTextContent("Waiting for Node 3");
  }, 10000);

  it("results: warnings, weighted by default, Calibrate asks again when the change is large, then shows sent -> ok per node", async () => {
    const user = userEvent.setup();
    mocks.active = { active: true, sessionId: 5 };
    const cells = (p: number) => ({ pulses: p, valid: true, reason: null, ppl: p / 2, flowLpm: 3 });
    mocks.session = session({
      runs: [{ id: 1, idx: 1, status: "done", volumeL: 2, durationS: 40, reason: null, startedAt: "", stopRequestedAt: "", stoppedAt: "", waitingFor: [], cells: { "2": cells(900), "3": cells(1000) } }],
      results: { runs: [{ id: 1, idx: 1, status: "done", volumeL: 2, durationS: 40, nodes: { "2": cells(900), "3": cells(1000) } }], nodes: [stat(2), stat(3, { spreadPct: 6.2, quality: "poor" })],
        warnings: [{ code: "node_disagrees", severity: "warning", runId: 1, nodeId: 3, message: "Run 1: Node 3 reads 10.0 % different from the other nodes" }], validRuns: 1 },
    });
    renderWizard();
    await user.click(await screen.findByTestId("to-results"));
    expect(screen.getByTestId("cal-warnings")).toHaveTextContent("Node 3 reads 10.0 % different");
    expect(within(screen.getByTestId("stat-3")).getByText("poor")).toBeInTheDocument();
    expect(within(screen.getByTestId("stat-2")).getAllByText("452.3").length).toBeGreaterThan(0);          // volume-weighted is the default value
    mocks.v2.calibrationApply.mockRejectedValueOnce(Object.assign(new Error("needs"), { status: 409, data: { code: "needs_confirm", error: "Node 2: 530 differs 18 % from the current 450" } }));
    await user.click(screen.getByTestId("apply"));
    expect(await screen.findByText(/differs 18 %/)).toBeInTheDocument();
    expect(mocks.v2.calibrationApply).toHaveBeenLastCalledWith(5, { method: "weighted", confirm: false });
    await user.click(screen.getByRole("button", { name: "Apply anyway" }));
    await waitFor(() => expect(mocks.v2.calibrationApply).toHaveBeenLastCalledWith(5, { method: "weighted", confirm: true }));
    await waitFor(() => expect(screen.getByTestId("apply-2")).toHaveAttribute("data-phase", "ok"));
    expect(screen.getByTestId("apply-3")).toHaveTextContent("sent → ok (452.3 pulses/L)");
    await user.click(screen.getByTestId("use-median"));
    expect(within(screen.getByTestId("stat-2")).getAllByText("451").length).toBeGreaterThan(0);                // the median value is now the one to apply
    expect(screen.getByTestId("apply")).toBeDisabled();                                                          // everything was sent: no second apply
  }, 12000);

  it("users see a running session read-only", async () => {
    mocks.role = "user"; mocks.active = { active: true, sessionId: 5 };
    mocks.session = session({ runs: [{ id: 1, idx: 1, status: "done", volumeL: null, durationS: 40, reason: null, startedAt: "", stopRequestedAt: "", stoppedAt: "", waitingFor: [], cells: {} }] });
    renderWizard();
    await screen.findByTestId("live-nodes");
    expect(screen.queryByTestId("run-controls")).not.toBeInTheDocument();
    expect(screen.queryByTestId("cancel-session")).not.toBeInTheDocument();
  });

  it("an invalid run explains why", async () => {
    mocks.active = { active: true, sessionId: 5 };
    mocks.session = session({ runs: [{ id: 1, idx: 1, status: "invalid", volumeL: null, durationS: 40, reason: "Node 2 restarted during the run", startedAt: "", stopRequestedAt: "", stoppedAt: "", waitingFor: [], cells: {} }] });
    renderWizard();
    expect(await screen.findByText(/Node 2 restarted during the run/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete run 1" })).toBeInTheDocument();
  });
});

describe("calibration banner and leak check", () => {
  it("shows a banner on every page while a session runs", async () => {
    mocks.active = { active: true, sessionId: 5, nodes: [{ id: 2, label: "Node 2" }] };
    Object.assign(mocks.v2, { pipelineStatus: vi.fn(async () => ({ zones: [] })) });
    render(<MemoryRouter><StatusStrip /></MemoryRouter>);
    expect(await screen.findByTestId("calibration-banner")).toHaveTextContent("leak detection is paused for Node 2");
  });

  it("calibrating zones are neither fine nor a fault: the leak check says it is paused for calibration", () => {
    const z = { id: "z", name: "After Node 2", class: "strong", status: "calibrating", calibrating: ["Node 2"], nodesMissing: [] } as unknown as LiveZone;
    const lc = leakCheck([z]);
    expect(lc.state).toBe("none");
    expect(lc.headline).toMatch(/calibration in progress/i);
    const ok = { id: "y", name: "Other", class: "strong", status: "normal", nodesMissing: [] } as unknown as LiveZone;
    expect(leakCheck([z, ok]).note).toMatch(/paused: calibrating Node 2/);
  });
});
