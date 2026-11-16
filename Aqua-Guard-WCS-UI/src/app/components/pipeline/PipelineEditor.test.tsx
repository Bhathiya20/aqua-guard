import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PipelineDoc } from "../../lib/v2";

const mocks = vi.hoisted(() => ({ role: "admin" as "admin" | "user", v2: {} as any }));
vi.mock("../../lib/v2", async (orig) => ({ ...(await orig<typeof import("../../lib/v2")>()), v2: mocks.v2 }));
vi.mock("../../lib/auth", () => ({ useAuth: () => ({ user: { role: mocks.role, email: "a@b.c" } }) }));

import { PipelineEditor } from "./PipelineEditor";

const DOC: PipelineDoc = {
  schema: 1, name: "House",
  parts: [
    { id: "src", kind: "source", x: 0, y: 0, rot: 0 },
    { id: "p1", kind: "pipe", x: 100, y: 0, rot: 0, lengthM: 3 },
    { id: "o1", kind: "outlet", x: 260, y: 0, rot: 0, outletType: "tap" },
    { id: "n1", kind: "node", nodeType: "flow", number: 2, nodeRef: 2, name: "Main outlet", pipe: "p1", pos: 0.5, dir: "forward" },
  ],
  links: [{ id: "e1", from: { part: "src", port: "out" }, to: { part: "p1", port: "in" } }, { id: "e2", from: { part: "p1", port: "out" }, to: { part: "o1", port: "in" } }],
};
const nodeV2 = (n: number, name: string) => ({ id: n, gatewayRef: 1, type: "flow", number: n, label: `Node ${n}`, name, mac: "AA", state: "online", registryState: "paired", batteryMv: null, rssi: -50, lastSeen: null, pulsesPerLiter: 450, txReduce: false, addedEpoch: 1, live: null, hardware: [] });
const zone = (id: string, status: string, parts: string[]) => ({ id, name: id === "zone:source" ? "Before Node 2" : "After Node 2", class: "strong", status, level: status === "confirmed" ? 2 : 0, reason: "", equation: "Q_unaccounted = Q_in - sum(Q_out)", inMeter: null, outMeters: [], outlets: [], outletIds: [],
  pipes: [], parts, balance: { qIn: 6, qOut: 2, unaccounted: 4, thresholdLpm: 0.5, bucketTs: 1, confidence: "high" }, meterFaultHint: null, suggestions: [], parent: null, rules: [], completeBuckets: 3, finalBuckets: 3, nodesMissing: [] });

beforeEach(() => {
  mocks.role = "admin";
  (window as any).matchMedia = (q: string) => ({ matches: true, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false });
  Object.assign(mocks.v2, {
    versions: vi.fn(async () => ({ activeVersion: 2, versions: [{ version: 2, note: "", valid: true, createdBy: "a", createdAt: "2026-01-01 10:00:00", active: true }] })),
    pipeline: vi.fn(async () => ({ id: 1, name: "House", activeVersion: 2, version: 2, note: "", savedValid: true, createdAt: "2026-01-01 10:00:00", createdBy: "a", doc: DOC, valid: true, messages: [],
      availableNodes: [{ type: "flow", number: 3, name: "Com 34 last one", label: "Node 3 - Com 34 last one", nodeRef: 3 }, { type: "flow", number: 4, name: "Com 37 2nd one", label: "x", nodeRef: 4 }] })),
    nodes: vi.fn(async () => ({ nodes: [nodeV2(2, "Main outlet"), nodeV2(3, "Com 34 last one"), nodeV2(4, "Com 37 2nd one")] })),
    validate: vi.fn(async () => ({ valid: false, messages: [{ severity: "error", code: "port_open", partId: "o1", message: "Outlet 1 is not connected to the pipe it belongs to." }], coverage: { text: "1 of 2 zones leak-monitored; 1 of 1 outlets covered" } })),
    pipelineStatus: vi.fn(async () => ({ pipelineId: 1, name: "House", version: 2, zones: [zone("zone:source", "normal", ["src"]), zone("zone:flow2", "confirmed", ["o1"])], coverage: { text: "ok" }, suggestions: [], live: { n1: { flowLpm: 6.2, measuredAt: "x", fresh: true } } })),
    zoneSeries: vi.fn(async () => ({ zone: "zone:flow2", bucketS: 60, buckets: [] })),
    zoneLeaks: vi.fn(async () => ({ leaks: [] })),
    savePipeline: vi.fn(), activate: vi.fn(),
  });
});

const mount = () => render(<MemoryRouter><PipelineEditor pipelineId={1} /></MemoryRouter>);

describe("Pipeline editor", () => {
  it("palette lists only paired nodes that are not placed yet, labelled with number and name", async () => {
    mount();
    const pal = await screen.findByTestId("palette");
    expect(within(pal).getByTestId("palette-node-3")).toHaveTextContent("Node 3 - Com 34 last one");
    expect(within(pal).getByTestId("palette-node-4")).toBeInTheDocument();
    expect(within(pal).queryByTestId("palette-node-2")).not.toBeInTheDocument();       // Node 2 is already on pipe p1
    expect(within(pal).getByText("T junction")).toBeInTheDocument();
    expect(within(pal).getByText("Outlet - tank inlet")).toBeInTheDocument();
  });

  it("clicking a validation message selects and shows the part it names", async () => {
    const user = userEvent.setup();
    mount();
    const msg = await screen.findByTestId("msg-port_open");
    expect(screen.getByTestId("coverage")).toHaveTextContent("1 of 2 zones leak-monitored");
    await user.click(msg);
    const props = await screen.findByTestId("properties");
    expect(within(props).getByText("o1")).toBeInTheDocument();
  });

  it("Live mode locks editing, shows the zone legend and the live flow on each node", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByTestId("palette");
    expect(screen.getByRole("button", { name: /save new version/i })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^live$/i }));
    expect(await screen.findByTestId("zone-legend")).toBeInTheDocument();
    expect(screen.getByTestId("canvas")).toHaveAttribute("data-mode", "live");
    expect(screen.getByText(/editing locked/i)).toBeInTheDocument();
    expect(screen.queryByTestId("palette")).not.toBeInTheDocument();                    // nothing to drag in
    expect(screen.queryByRole("button", { name: /save new version/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument();
    expect(await screen.findByTestId("flow-n1")).toHaveTextContent("6.20 L/min");
    expect(screen.getByTestId("zone-zone:flow2")).toHaveTextContent(/unacc 4\.00/);
    await user.click(screen.getByTestId("zone-zone:flow2"));
    const detail = await screen.findByTestId("zone-detail");
    expect(within(detail).getByText("Confirmed leak")).toBeInTheDocument();
    await waitFor(() => expect(mocks.v2.zoneSeries).toHaveBeenCalledWith(1, "zone:flow2", 6));
  });

  it("a normal user can look but not edit or save", async () => {
    mocks.role = "user";
    mount();
    await screen.findByTestId("palette");
    expect(screen.getByText(/read only/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /save new version/i })).not.toBeInTheDocument();
    expect(screen.getByTestId("palette-pipe")).toBeDisabled();
  });

  it("Activate stays disabled for a draft with errors and for the version that is already active", async () => {
    mount();
    await screen.findByTestId("palette");
    expect(screen.getByRole("button", { name: /^activate$/i })).toBeDisabled();           // v2 is valid but already active
  });
});
