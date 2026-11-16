import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import type { AvailableNode, LiveZone, PipelineDoc } from "../lib/v2";

const mocks = vi.hoisted(() => ({ v2: {} as any }));
vi.mock("../lib/v2", async (orig) => ({ ...(await orig<typeof import("../lib/v2")>()), v2: mocks.v2 }));
vi.mock("../lib/auth", () => ({ useAuth: () => ({ user: { role: "admin", email: "a@b.c" } }) }));

import { DemoMode, RuleProgress, WeakWhy, settingsSentence } from "./LeakInfo";
import { LiveSummary, ValidationBar, ZoneSummary } from "./pipeline/PipelinePanels";
import { addPart, connect, emptyDoc } from "./pipeline/docModel";
import { leakCheck } from "../lib/v2";

const S = { thresholdLpm: 0.3, tolerancePct: 10, suspectMin: 10, confirmMin: 30, resolveMin: 5, bucketS: 60, nightStart: "02:00", nightEnd: "04:00", continuousSuspectMin: 360, continuousConfirmMin: 720, tz: "Asia/Colombo", tzOffsetMin: 330, demo: false };
const prog = (text: string, over: any = {}) => ({ text, value: 2, needed: 10, unit: "min", counting: false, ...over });
const Z = (over: any = {}): LiveZone => ({ id: "z", name: "After Node 3", class: "strong", status: "normal", level: 0, reason: "", equation: "Q[3] - Q[4]", inMeter: null, outMeters: [], outlets: [], outletIds: [], pipes: [], parts: [], balance: null,
  meterFaultHint: null, suggestions: [], parent: null, rules: [], completeBuckets: 0, finalBuckets: 0, nodesMissing: [], nodesOffline: [], settings: S, ...over } as LiveZone);
const wrap = (el: React.ReactNode) => render(<MemoryRouter>{el}</MemoryRouter>);

describe("rule progress and configured values", () => {
  it("shows how far a rule is and the real configured values", () => {
    const z = Z({ rules: [{ rule: "balance", level: 0, clear: false, reason: "", progress: prog("Above threshold 2 min of 10 min → suspected (confirmed at 30 min)", { counting: true }) }] });
    render(<RuleProgress z={z} />);
    expect(screen.getByTestId("rule-balance")).toHaveTextContent("Above threshold 2 min of 10 min");
    expect(screen.getByTestId("rule-balance")).toHaveAttribute("data-counting", "1");
    expect(settingsSentence(z)).toBe("Threshold max(0.3 L/min, 10 %) · suspected after 10 min · confirmed after 30 min · resolved after 5 min normal · bucket 60 s");
    expect(settingsSentence(Z({ class: "weak" }))).toContain("night flow 02:00-04:00 (Asia/Colombo) · continuous flow longer than 6 h (confirmed at 12 h)");
  });
  it("a weak zone explains why unaccounted flow is not alarming, with the configured values", () => {
    render(<WeakWhy z={Z({ class: "weak" })} />);
    expect(screen.getByTestId("weak-why")).toHaveTextContent("The balance cannot prove a leak in this zone");
    expect(screen.getByTestId("weak-why")).toHaveTextContent("night flow (02:00-04:00) and continuous flow longer than 6 h");
    expect(render(<WeakWhy z={Z()} />).container).toBeEmptyDOMElement();                  // strong zone: nothing to explain
  });
  it("the zone panel shows the progress of every rule", () => {
    const z = Z({ class: "weak", rules: [
      { rule: "night_flow", level: 0, clear: false, reason: "", progress: prog("Night flow: outside window (02:00-04:00); last night's minimum 0.00 L/min was within the threshold") },
      { rule: "continuous_flow", level: 0, clear: false, reason: "", progress: prog("Continuous flow 4 min of 6 h → suspected (confirmed at 12 h)", { value: 4, needed: 360 }) }] });
    render(<ZoneSummary z={z} />);
    expect(screen.getByTestId("rule-night_flow")).toHaveTextContent("outside window (02:00-04:00)");
    expect(screen.getByTestId("rule-continuous_flow")).toHaveTextContent("4 min of 6 h");
    expect(screen.getByTestId("weak-why")).toBeInTheDocument();
  });
});

describe("watching state", () => {
  const watching = Z({ watching: true, rules: [{ rule: "balance", level: 0, clear: false, reason: "", progress: prog("Above threshold 2 min of 10 min → suspected", { counting: true }) }] });
  it("a counting rule turns the all-clear into an amber 'Watching: ...' (never plain green)", () => {
    const lc = leakCheck([watching, Z({ id: "b", name: "Other", status: "normal" })]);
    expect(lc.state).toBe("watching");
    expect(lc.headline).toBe("Watching: After Node 3 above threshold");
    expect(leakCheck([Z({ status: "normal" })]).state).toBe("ok");
    expect(leakCheck([Z({ status: "suspected", watching: true })]).state).toBe("suspected");        // a real suspicion wins
  });
  it("the Live summary shows the counting rule under the zone", () => {
    wrap(<LiveSummary zones={[watching]} tints={{ z: "#ccc" }} onSelect={() => {}} />);
    expect(screen.getByTestId("live-summary")).toHaveTextContent("Watching: After Node 3 above threshold");
    expect(screen.getByTestId("watch-z")).toHaveTextContent("Above threshold 2 min of 10 min");
  });
});

describe("demo mode", () => {
  it("is clearly labelled, admin only, and toggles the pipeline", async () => {
    const user = userEvent.setup();
    mocks.v2.setDemo = vi.fn(async () => ({ ok: true, demo: true, demoUntil: 1 }));
    const onChange = vi.fn();
    const { unmount } = render(<DemoMode pipelineId={7} demo={false} demoUntil={null} admin onChange={onChange} />);
    expect(screen.getByTestId("demo-mode")).toHaveTextContent("suspected after 1 min and confirmed after 3 min");
    expect(screen.getByTestId("demo-mode")).toHaveTextContent("Switches itself off after 1 hour");
    await user.click(screen.getByTestId("demo-toggle"));
    await waitFor(() => expect(mocks.v2.setDemo).toHaveBeenCalledWith(7, true));
    expect(onChange).toHaveBeenCalled();
    unmount();
    render(<DemoMode pipelineId={7} demo demoUntil={1_800_000_000} admin={false} onChange={onChange} />);
    expect(screen.getByTestId("demo-mode")).toHaveAttribute("data-on", "1");
    expect(screen.queryByTestId("demo-toggle")).not.toBeInTheDocument();
  });
});

describe("placing an out-of-service node", () => {
  const doc = (): PipelineDoc => { let d = emptyDoc("H"); const a = addPart(d, "source", 0, 0); d = a.doc; const b = addPart(d, "pipe", 100, 0); d = connect(b.doc, { part: a.id, port: "out" }, { part: b.id, port: "in" }).doc; return d; };
  const sug = [{ kind: "cover", partId: "p1", text: "Add a node on Pipe 1." }];
  const N = (number: number, over: any = {}): AvailableNode => ({ type: "flow", number, name: "", label: `Node ${number}`, nodeRef: number, ...over });
  it("shows a badge and asks before placing; an in-service node is offered first", async () => {
    const user = userEvent.setup();
    const onPlace = vi.fn(), n2 = N(2, { outOfService: true });
    render(<ValidationBar doc={doc()} messages={[]} valid suggestions={sug as any} checking={false} error="" onPick={() => {}} freeNodes={[n2]} onPlace={onPlace} />);
    expect(screen.getByTestId("suggestion-oos")).toHaveTextContent("out of service");
    await user.click(screen.getByTestId("suggestion-place"));
    expect(onPlace).not.toHaveBeenCalled();
    expect(screen.getByTestId("suggestion-confirm")).toHaveTextContent("Node 2 is out of service");
    await user.click(within(screen.getByTestId("suggestion-confirm")).getByRole("button", { name: "Cancel" }));
    expect(onPlace).not.toHaveBeenCalled();
    await user.click(screen.getByTestId("suggestion-place")); await user.click(screen.getByTestId("suggestion-confirm-yes"));
    expect(onPlace).toHaveBeenCalledWith(n2, "p1");
  });
  it("offers the node that is in service first, without a warning", async () => {
    const user = userEvent.setup();
    const onPlace = vi.fn(), n3 = N(3);
    render(<ValidationBar doc={doc()} messages={[]} valid suggestions={sug as any} checking={false} error="" onPick={() => {}} freeNodes={[N(2, { outOfService: true }), n3]} onPlace={onPlace} />);
    expect(screen.queryByTestId("suggestion-oos")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("suggestion-place"));
    expect(onPlace).toHaveBeenCalledWith(n3, "p1");
  });
});
