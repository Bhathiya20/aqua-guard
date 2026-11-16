import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import type { AvailableNode, LiveZone, PipelineDoc } from "../../lib/v2";

const mocks = vi.hoisted(() => ({ v2: {} as any }));
vi.mock("../../lib/v2", async (orig) => ({ ...(await orig<typeof import("../../lib/v2")>()), v2: mocks.v2 }));
vi.mock("../../lib/auth", () => ({ useAuth: () => ({ user: { role: "admin", email: "a@b.c" } }) }));

import { addPart, connect, emptyDoc, partLabels, placeNodeOnPipe, zoneTint } from "./docModel";
import { LiveSummary, ValidationBar } from "./PipelinePanels";
import { PipelinesPage } from "./PipelinesPage";

const NODE: AvailableNode = { type: "flow", number: 2, name: "", label: "Node 2", nodeRef: 2 };
function doc(): PipelineDoc {
  let d = emptyDoc("H");
  const add = (kind: any, x: number, y: number, extra: any = {}) => { const r = addPart(d, kind, x, y, extra); d = r.doc; return r.id; };
  const src = add("source", 0, 0), p1 = add("pipe", 100, 0), o1 = add("outlet", 300, 0, { outletType: "tap" }), o2 = add("outlet", 300, 100, { outletType: "shower" }), o3 = add("outlet", 300, 200, { outletType: "garden" }), c = add("cap", 300, 300);
  d = connect(d, { part: src, port: "out" }, { part: p1, port: "in" }).doc;
  void o1; void o2; void o3; void c;
  return d;
}
const Z = (id: string, name: string, status: string, over: any = {}): LiveZone => ({ id, name, class: "strong", status, level: 0, reason: "", equation: "", inMeter: null, outMeters: [], outlets: [], outletIds: [], pipes: [], parts: [], balance: null,
  meterFaultHint: null, suggestions: [], parent: null, rules: [], completeBuckets: 0, finalBuckets: 0, nodesMissing: [], nodesOffline: [], ...over } as LiveZone);

describe("part names", () => {
  it("default names follow type and order; an own label wins", () => {
    let d = doc();
    const L = partLabels(d);
    expect(Object.values(L)).toEqual(["Main supply", "Pipe 1", "Tap 1", "Shower 1", "Garden tap", "End cap 1"]);
    d = { ...d, parts: d.parts.map((p) => (p.kind === "outlet" && p.outletType === "tap" ? { ...p, label: "Kitchen" } : p)) };
    expect(Object.values(partLabels(d))[2]).toBe("Kitchen");
  });
  it("'Place Node 2 here' puts the node in the middle of that pipe", () => {
    const d = doc(), pipe = d.parts.find((p) => p.kind === "pipe")!;
    const r = placeNodeOnPipe(d, NODE, pipe.id)!;
    const m = r.doc.parts.find((p) => p.kind === "node")!;
    expect(m.pipe).toBe(pipe.id);
    expect(placeNodeOnPipe(r.doc, NODE, pipe.id)).toBeNull();                         // a node can be placed only once
    expect(placeNodeOnPipe(d, NODE, "nope")).toBeNull();
  });
});

describe("checks panel", () => {
  const sug = [{ kind: "cover", partId: "p1", text: "Add a node on Pipe 1 to monitor Tap 1." }];
  it("is collapsible, shows the count, and suggestions have Show and Place buttons", async () => {
    const user = userEvent.setup();
    const onPick = vi.fn(), onPlace = vi.fn();
    render(<ValidationBar doc={doc()} messages={[]} valid suggestions={sug as any} checking={false} error="" onPick={onPick} freeNodes={[NODE]} onPlace={onPlace} />);
    expect(screen.getByTestId("checks-count")).toHaveTextContent("1");
    await user.click(screen.getByTestId("suggestion-show")); expect(onPick).toHaveBeenCalledWith("p1");
    await user.click(screen.getByRole("button", { name: "Place Node 2 here" })); expect(onPlace).toHaveBeenCalledWith(NODE, "p1");
    await user.click(screen.getByTestId("checks-toggle"));
    expect(screen.queryByTestId("suggestion")).not.toBeInTheDocument();
    expect(screen.getByTestId("checks-count")).toHaveTextContent("1");                // the count stays visible when collapsed
  });
  it("without an unplaced paired node there is no Place button", () => {
    render(<ValidationBar doc={doc()} messages={[]} valid suggestions={sug as any} checking={false} error="" onPick={() => {}} freeNodes={[]} onPlace={() => {}} />);
    expect(screen.queryByTestId("suggestion-place")).not.toBeInTheDocument();
    expect(screen.getByTestId("suggestion-show")).toBeInTheDocument();
  });
});

describe("live view", () => {
  it("every zone has its own tint and the summary lists all zones with their state", async () => {
    const zones = [Z("a", "Kitchen branch", "normal"), Z("b", "Garden branch", "incomplete", { nodesOffline: ["Node 3"] }), Z("c", "Bath", "confirmed")];
    expect(new Set(zones.map((z) => zoneTint(zones, z.id))).size).toBe(3);
    const onSel = vi.fn();
    render(<LiveSummary zones={zones} tints={Object.fromEntries(zones.map((z) => [z.id, zoneTint(zones, z.id)]))} onSelect={onSel} />);
    expect(screen.getByTestId("live-summary")).toHaveTextContent("Leak confirmed");
    expect(within(screen.getByTestId("summary-b")).getByText(/no recent data from Node 3/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByTestId("summary-c")); expect(onSel).toHaveBeenCalledWith("c");
  });
});

describe("Pipelines list", () => {
  it("shows ACTIVE prominently, warns about duplicate names and offers Rename and Duplicate", async () => {
    mocks.v2.pipelines = vi.fn(async () => ({ pipelines: [{ id: 1, name: "House", activeVersion: 2, versions: 2, updatedAt: null }, { id: 2, name: "house", activeVersion: null, versions: 1, updatedAt: null }, { id: 3, name: "Garage", activeVersion: null, versions: 1, updatedAt: null }] }));
    render(<MemoryRouter><PipelinesPage /></MemoryRouter>);
    expect(await screen.findByText(/ACTIVE v2/)).toBeInTheDocument();
    expect(screen.getAllByTestId("dup-name")).toHaveLength(2);                          // "House" and "house"
    expect(within(screen.getByTestId("pipeline-3")).queryByTestId("dup-name")).not.toBeInTheDocument();
    expect(within(screen.getByTestId("pipeline-3")).getByRole("button", { name: /Rename/ })).toBeInTheDocument();
    expect(within(screen.getByTestId("pipeline-3")).getByRole("button", { name: /Duplicate/ })).toBeInTheDocument();
  });
});
