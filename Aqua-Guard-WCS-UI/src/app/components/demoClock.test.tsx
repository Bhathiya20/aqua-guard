import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ v2: {} as any }));
vi.mock("../lib/v2", async (orig) => ({ ...(await orig<typeof import("../lib/v2")>()), v2: mocks.v2 }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { ClockBanner, DemoClockSwitch } from "./DemoClock";

const clock = (mode: string) => ({ mode, forced: mode !== "auto", since: 1000, until: mode === "auto" ? null : 4600, by: "a@b.c", maxS: 3600 });

beforeEach(() => { mocks.v2.demoClock = vi.fn(async () => clock("auto")); mocks.v2.setDemoClock = vi.fn(async (m: string) => clock(m)); });

describe("Demo clock UI", () => {
  it("shows no banner with the real clock", async () => {
    render(<MemoryRouter><ClockBanner /></MemoryRouter>);
    await waitFor(() => expect(mocks.v2.demoClock).toHaveBeenCalled());
    expect(screen.queryByTestId("clock-banner")).not.toBeInTheDocument();
  });

  it("shows a banner on every page while the clock is forced, saying that it is simulated and when it ends", async () => {
    mocks.v2.demoClock = vi.fn(async () => clock("night"));
    render(<MemoryRouter><ClockBanner /></MemoryRouter>);
    const b = await screen.findByTestId("clock-banner");
    expect(b).toHaveTextContent("Demo clock: NIGHT (simulated)");
    expect(b).toHaveTextContent("switches itself off at");
    expect(b).toHaveTextContent("Real timestamps and readings are not changed");
  });

  it("admin switches Auto / Day / Night; a user only sees the state", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<DemoClockSwitch admin />);
    await user.click(await screen.findByTestId("clock-night"));
    await waitFor(() => expect(mocks.v2.setDemoClock).toHaveBeenCalledWith("night"));
    await user.click(screen.getByTestId("clock-day"));
    await waitFor(() => expect(mocks.v2.setDemoClock).toHaveBeenCalledWith("day"));
    unmount();
    render(<DemoClockSwitch admin={false} />);
    expect(await screen.findByTestId("demo-clock")).toBeInTheDocument();
    expect(screen.queryByTestId("clock-night")).not.toBeInTheDocument();
  });
});
