import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("../../lib/v2", async (orig) => ({ ...(await orig<typeof import("../../lib/v2")>()), v2: { zoneThresholds: vi.fn(async () => ({ xPlusLpm: 0.3, xMinusLpm: 0.3, tLeakS: 60, defaults: { demoTLeakS: 10 }, custom: false })) } }));
import { BalanceState } from "./BalancePanel";
import { leakCheck } from "../../lib/v2";

const wl = (state: string, over: any = {}) => ({ state, tdS: 25, tLeakS: 60, dLpm: 0.8, qInLpm: 2, qOutLpm: 1.2, xPlusLpm: 0.3, xMinusLpm: 0.3, windowS: 5, completeWindows: 5, finalWindows: 5, ...over });
const Z = (state: string, over: any = {}, w: any = {}) => ({ id: "z1", name: "After Node 1", class: "strong", status: "normal", balanceState: state, wlds: wl(state, w), nodesMissing: [], rules: [], ...over }) as any;

describe("responsive balance (WLDS) display", () => {
  it("suspicious shows the live counter x s of T_leak", () => {
    render(<BalanceState z={Z("suspicious")} />);
    expect(screen.getByTestId("td-z1")).toHaveTextContent("25 s of 60 s");
    expect(screen.getByTestId("balance-state-z1")).toHaveAttribute("data-state", "suspicious");
  });
  it("inconsistency says outflow is larger than inflow and is not a leak", () => {
    render(<BalanceState z={Z("inconsistent", {}, { dLpm: -0.9 })} />);
    expect(screen.getByTestId("inconsistent-z1")).toHaveTextContent("Outflow is larger than inflow: check sensor / calibration / test");
    expect(screen.getByTestId("d-live-z1")).toHaveTextContent("-0.90");
  });
  it("overall check is never green while a zone is suspicious or inconsistent", () => {
    expect(leakCheck([Z("suspicious")]).state).toBe("watching");
    expect(leakCheck([Z("inconsistent")]).state).toBe("inconsistent");
    expect(leakCheck([Z("normal")]).state).toBe("ok");
  });
});
