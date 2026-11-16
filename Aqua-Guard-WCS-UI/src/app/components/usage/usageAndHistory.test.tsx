import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ v2: {} as any, role: "admin" as "admin" | "user" }));
vi.mock("../../lib/v2", async (orig) => ({ ...(await orig<typeof import("../../lib/v2")>()), v2: mocks.v2 }));
vi.mock("../../lib/auth", () => ({ useAuth: () => ({ user: { role: mocks.role, email: "a@b.c" } }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("recharts", async () => {                                   // jsdom has no layout: render the chart containers as plain boxes
  const R = await vi.importActual<any>("recharts");
  return { ...R, ResponsiveContainer: ({ children }: any) => <div style={{ width: 400, height: 200 }}>{children}</div> };
});

import { HistoryTab } from "../alerts/HistoryTab";
import { AlertsPage } from "../AlertsPage";
import { NodeUsageDialog } from "./NodeUsageDialog";
import { UsageLiveTable } from "./UsageParts";
import { UsageSettingsPage } from "./UsageSettingsPage";

const ev = (id: number, over: any = {}) => ({
  id, type: "leak", typeLabel: "Leak", severity: "critical", message: "Confirmed leak in After Node 1", demo: false, node: null, zone: { id: "zone:flow1", name: "After Node 1" }, pipelineId: 1,
  startedAt: "2026-01-01 10:00:00", suspectedAt: "2026-01-01 10:00:00", confirmedAt: "2026-01-01 10:10:00", resolvedAt: "2026-01-01 10:20:00", durationS: 1200, peakLpm: 2.4, peakKind: "D", lostL: 18.5,
  resolvedBy: "admin@x.y", reason: "fixed_leak", reasonLabel: "Fixed leak", note: "pipe clamp", ...over,
});
const live = (n: number, over: any = {}) => ({ nodeRef: n, number: n, name: n === 2 ? "Washroom" : "", label: `Node ${n}${n === 2 ? " (Washroom)" : ""}`, preset: "washroom", presetLabel: "Washroom", custom: true, summary: "Washroom: 1 min 30 s / 4 min",
  tLimS: 90, tMaxS: 240, status: "NORMAL", tFlowS: 40, next: { what: "T_lim", inS: 50 }, allowed: false, quiet: false, flowLpm: 2.1, alertId: null, demo: null, ...over });
const profile = { preset: "washroom", tLimS: 90, tMaxS: 240, flowOnLpm: 0.3, flowOffLpm: 0.15, noFlowS: 10, quietStart: "22:00", quietEnd: "05:00", quietConfirmS: 60, allow24h: false, schedule: [] };

beforeEach(() => {
  mocks.role = "admin";
  Object.assign(mocks.v2, {
    alerts: vi.fn(async () => []), nodes: vi.fn(async () => ({ nodes: [{ id: 2, number: 2, name: "Washroom", type: "flow", state: "online" }] })),
    alertHistory: vi.fn(async () => ({ history: [ev(7), ev(8, { type: "night_flow", typeLabel: "Night flow", zone: null, node: { ref: 2, number: 2, type: "flow", name: "Washroom", label: "Node 2 (Washroom)" }, resolvedBy: "auto", reason: null, reasonLabel: null, note: "", demo: true, peakKind: "flow" })], reasons: {}, types: {} })),
    alertDetail: vi.fn(async (id: number) => ({ event: ev(id), series: { kind: "balance", unit: "L/min", points: [{ t: 1767268800, qIn: 3, qOut: 1, d: 2 }, { t: 1767268860, qIn: 3, qOut: 1, d: 2 }] } })),
    usageLive: vi.fn(async () => ({ nodes: [live(1, { name: "", label: "Node 1", preset: "default", presetLabel: "Default", custom: false, status: "NO_FLOW", tFlowS: 0, next: null, flowLpm: 0 }), live(2), live(3, { status: "CONTINUOUS_ABNORMAL_USAGE", tFlowS: 300, next: null }), live(4, { status: "UNKNOWN", tFlowS: null, next: null, flowLpm: null })] })),
    usageDefaults: vi.fn(async () => ({ profile: { ...profile, preset: "default", tLimS: 900, tMaxS: 1800 }, factory: profile, presets: [{ key: "default", label: "Default", tLimS: 900, tMaxS: 1800 }, { key: "washroom", label: "Washroom", tLimS: 90, tMaxS: 240 }, { key: "custom", label: "Custom" }], custom: false, updatedBy: null, updatedAt: null })),
    setUsageDefaults: vi.fn(async () => ({ profile })), resetUsageDefaults: vi.fn(async () => ({ ok: true })),
    usageAudit: vi.fn(async () => ({ audit: [{ ts: "2026-01-01 10:00:00", user: "admin@x.y", action: "usage_node", detail: JSON.stringify({ node: "Node 2 (Washroom)", changed: { tLimS: [900, 90] } }) }] })),
    nodeUsage: vi.fn(async () => ({ nodeRef: 2, label: "Node 2", profile, custom: true, updatedBy: "admin@x.y", updatedAt: "2026-01-01 10:00:00", presets: [{ key: "default", label: "Default", tLimS: 900, tMaxS: 1800 }, { key: "washroom", label: "Washroom", tLimS: 90, tMaxS: 240 }, { key: "canteen", label: "Canteen", tLimS: 300, tMaxS: 600 }, { key: "custom", label: "Custom" }], defaults: profile, demo: null, live: live(2) })),
    setNodeUsage: vi.fn(async () => ({ profile })), resetNodeUsage: vi.fn(async () => ({ ok: true })), setUsageDemo: vi.fn(async () => ({ ok: true, on: true, until: 1 })),
  });
});
const wrap = (ui: any, url = "/") => render(<MemoryRouter initialEntries={[url]}>{ui}</MemoryRouter>);

describe("History tab (Part 4)", () => {
  it("lists resolved events with real names, times, peak, water lost, who and why; demo is tagged", async () => {
    wrap(<HistoryTab />);
    const r = await screen.findByTestId("history-row-7");
    expect(r).toHaveTextContent("After Node 1");
    expect(r).toHaveTextContent("20 min");
    expect(r).toHaveTextContent("2.4 L/min");
    expect(r).toHaveTextContent("18.5 L");
    expect(r).toHaveTextContent("admin@x.y");
    expect(r).toHaveTextContent("Fixed leak");
    const n = screen.getByTestId("history-row-8");
    expect(n).toHaveTextContent("Node 2 (Washroom)");
    expect(n).toHaveTextContent("demo");
    expect(n).toHaveTextContent("auto");
  });
  it("filters go to the server and the CSV link carries the same filters", async () => {
    const user = userEvent.setup();
    wrap(<HistoryTab />);
    await screen.findByTestId("history-row-7");
    await user.selectOptions(screen.getByLabelText("Type"), "night_flow");
    await user.selectOptions(screen.getByLabelText("Reason"), "auto");
    await waitFor(() => expect(mocks.v2.alertHistory).toHaveBeenLastCalledWith(expect.objectContaining({ type: "night_flow", reason: "auto" })));
    expect(screen.getByTestId("history-csv").getAttribute("href")).toBe("/api/alerts/history.csv?type=night_flow&reason=auto");
  });
  it("clicking a row opens the detail with the D chart", async () => {
    const user = userEvent.setup();
    wrap(<HistoryTab />);
    await user.click(await screen.findByTestId("history-row-7"));
    const d = await screen.findByTestId("event-detail");
    expect(within(d).getByText(/Water lost/)).toBeInTheDocument();
    expect(await within(d).findByTestId("event-chart")).toBeInTheDocument();
  });
  it("the Alerts page has Active and History tabs and opens History from ?tab=history", async () => {
    const user = userEvent.setup();
    wrap(<AlertsPage />, "/alerts?tab=history");
    expect(await screen.findByTestId("history-tab")).toBeInTheDocument();
    await user.click(screen.getByTestId("tab-active"));
    expect(screen.queryByTestId("history-tab")).not.toBeInTheDocument();
  });
});

describe("Usage profiles (Part 1)", () => {
  it("shows a status chip, the running time and the next limit per node; an unknown state is never green", async () => {
    wrap(<UsageLiveTable />);
    const r2 = await screen.findByTestId("usage-row-2");
    expect(r2).toHaveTextContent("Washroom");
    expect(within(r2).getByTestId("usage-chip-2")).toHaveAttribute("data-status", "NORMAL");
    expect(within(r2).getByTestId("usage-line-2")).toHaveTextContent("T_flow 40 s");
    expect(within(r2).getByTestId("usage-line-2")).toHaveTextContent("prolonged-usage limit in 50 s");
    expect(within(screen.getByTestId("usage-row-3")).getByTestId("usage-chip-3")).toHaveTextContent("Continuous abnormal usage");
    expect(screen.getByTestId("usage-row-4")).toHaveTextContent("Unknown (no data)");
    expect(screen.getByTestId("usage-line-4")).toHaveTextContent("no recent data");
    expect(screen.getByTestId("usage-row-1")).toHaveTextContent("No flow");
  });
  it("the node dialog applies a preset's limits, saves, resets and starts the demo", async () => {
    const user = userEvent.setup();
    const onDone = vi.fn();
    wrap(<NodeUsageDialog node={{ id: 2, number: 2, name: "Washroom" }} onClose={() => {}} onDone={onDone} />);
    const dlg = await screen.findByTestId("usage-dialog");
    await user.selectOptions(await within(dlg).findByLabelText("Preset"), "canteen");
    expect(within(dlg).getByLabelText(/T_lim/)).toHaveValue(300);
    expect(within(dlg).getByLabelText(/T_max/)).toHaveValue(600);
    await user.clear(within(dlg).getByLabelText(/T_lim/));
    await user.type(within(dlg).getByLabelText(/T_lim/), "120");
    expect(within(dlg).getByLabelText("Preset")).toHaveValue("custom");                          // editing a limit makes it a custom profile
    await user.click(within(dlg).getByTestId("usage-save"));
    await waitFor(() => expect(mocks.v2.setNodeUsage).toHaveBeenCalledWith(2, expect.objectContaining({ preset: "custom", tLimS: 120, tMaxS: 600 })));
    await user.click(within(dlg).getByTestId("usage-reset"));
    await waitFor(() => expect(mocks.v2.resetNodeUsage).toHaveBeenCalledWith(2));
    await user.click(within(dlg).getByTestId("demo-night"));
    await waitFor(() => expect(mocks.v2.setUsageDemo).toHaveBeenCalledWith(2, true, true));
  });
  it("an allowed long-use schedule can be added and removed", async () => {
    const user = userEvent.setup();
    wrap(<NodeUsageDialog node={{ id: 2, number: 2, name: "Washroom" }} onClose={() => {}} onDone={() => {}} />);
    const dlg = await screen.findByTestId("usage-dialog");
    await user.click(await within(dlg).findByRole("button", { name: /Add/ }));
    expect(within(dlg).getByTestId("schedule-0")).toBeInTheDocument();
    await user.click(within(dlg).getByRole("button", { name: /Remove schedule 1/ }));
    expect(within(dlg).queryByTestId("schedule-0")).not.toBeInTheDocument();
    await user.click(within(dlg).getByRole("button", { name: /Add/ }));
    await user.click(within(dlg).getByTestId("usage-save"));
    await waitFor(() => expect(mocks.v2.setNodeUsage).toHaveBeenCalledWith(2, expect.objectContaining({ schedule: [{ days: [0, 1, 2, 3, 4], start: "08:00", end: "17:00" }] })));
  });
  it("the settings page edits the global defaults and shows the audit of changes", async () => {
    const user = userEvent.setup();
    wrap(<UsageSettingsPage />);
    const save = await screen.findByTestId("defaults-save");
    const lim = screen.getByLabelText(/T_lim/);
    await user.clear(lim); await user.type(lim, "600");
    await user.click(save);
    await waitFor(() => expect(mocks.v2.setUsageDefaults).toHaveBeenCalledWith(expect.objectContaining({ tLimS: 600 })));
    expect(await screen.findByTestId("usage-audit")).toHaveTextContent("admin@x.y");
    expect(screen.getByTestId("usage-audit")).toHaveTextContent("Node 2 (Washroom)");
  });
  it("a normal user cannot save the defaults", async () => {
    mocks.role = "user";
    wrap(<UsageSettingsPage />);
    await screen.findByLabelText(/T_lim/);
    expect(screen.queryByTestId("defaults-save")).not.toBeInTheDocument();
    expect(screen.getByLabelText(/T_lim/)).toBeDisabled();
  });
});
