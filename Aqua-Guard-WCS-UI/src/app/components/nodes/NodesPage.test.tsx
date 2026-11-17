import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { GatewayV2, NodeV2 } from "../../lib/v2";

const gw: GatewayV2 = {
  id: 1, gatewayId: "gw-test", fw: "2.0", mode: "run", ip: "192.168.1.50", channel: 9, rssi: -55, state: "online", heartbeatAgeS: 4, timeStatus: "synced", timeSynced: true,
  tls: false, tlsVerified: false, remoteAdd: false, nodes: 2,
  rx: { frames: 100, rejected: 3, badTag: 2, badValue: 1, unknownMac: 0, queueDropped: 0 }, queue: { depth: 0, overflow: 0 }, uptimeS: 500, freeHeap: 100000, registryRev: 3, registryAt: null,
};
const node = (n: number, over: Partial<NodeV2> = {}): NodeV2 => ({
  id: n, gatewayRef: 1, type: "flow", number: n, label: `Node ${n}`, name: `Name ${n}`, mac: `AA:BB:CC:00:00:0${n}`, state: "online", registryState: "paired",
  batteryMv: 7400, rssi: -50, lastSeen: "2026-01-01 12:00:00", pulsesPerLiter: 450, txReduce: false, addedEpoch: 1, hardware: [],
  live: { flowLpm: 3.5, lastFlowLpm: 3.5, ageS: 2, cumPulses: 100, todayLiters: 12.3, weekLiters: 40, missedPackets24h: 2, receivedPackets24h: 98, lossPct24h: 2 }, ...over,
});

const mocks = vi.hoisted(() => ({
  state: { tls: false, nodes: [] as any[], role: "admin" as "admin" | "user" },
  v2: {} as any,
}));
vi.mock("../../lib/v2", async (orig) => {
  const real = await orig<typeof import("../../lib/v2")>();
  return { ...real, v2: mocks.v2 };
});
vi.mock("../../lib/auth", () => ({ useAuth: () => ({ user: { role: mocks.state.role, email: "a@b.c" } }) }));

import { NodesPage } from "./NodesPage";

beforeEach(() => {
  mocks.state.tls = false; mocks.state.role = "admin";
  mocks.state.nodes = [node(2, { name: "Main outlet", batteryMv: 7400 }), node(3, { name: "Com 34", batteryMv: null })];
  Object.assign(mocks.v2, {
    gateways: vi.fn(async () => ({ gateways: [gw], mqtt: { tls: mocks.state.tls, host: "broker.emqx.io", port: 1883, remoteAddAllowed: mocks.state.tls }, commandsEnabled: true })),
    nodes: vi.fn(async () => ({ nodes: mocks.state.nodes })),
    nearby: vi.fn(async () => ({ nearby: [{ mac: "11:22:33:44:55:66", type: "flow", rssi: -60, channel: 9, lastSeen: "2026-01-01 12:00:00" }] })),
    removeNode: vi.fn(async () => ({ commandId: 7, status: "sent", result: null, number: 2 })),
    addNode: vi.fn(),
    command: vi.fn(async () => ({ id: 7, op: "R", status: "ok", result: "ok", rc: 0, number: 2, created_at: "", resolved_at: "" })),
  });
});

describe("Nodes page", () => {
  it("shows the gateway card with a clickable IP, TLS badge and the node list", async () => {
    render(<MemoryRouter><NodesPage /></MemoryRouter>);
    expect(await screen.findByTestId("gateway-card")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /192\.168\.1\.50/ });
    expect(link).toHaveAttribute("href", "http://192.168.1.50/");
    expect(screen.getByText("MQTT without TLS")).toBeInTheDocument();
    expect(screen.getByText("time synced")).toBeInTheDocument();
    expect((await screen.findAllByText("Main outlet")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("No battery").length).toBeGreaterThan(0);          // null battery is "No battery", never 0 V
    expect(screen.getByText("11:22:33:44:55:66")).toBeInTheDocument();            // nodes nearby, read-only
  });

  it("TLS gate: without TLS, Add node explains why and points to the gateway page; nothing is sent", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><NodesPage /></MemoryRouter>);
    await user.click(await screen.findByRole("button", { name: /add node/i }));
    const box = await screen.findByTestId("tls-required");
    expect(within(box).getByText(/not available from the web app/i)).toBeInTheDocument();
    expect(within(box).getByRole("link", { name: /192\.168\.1\.50\/add/ })).toHaveAttribute("href", "http://192.168.1.50/add");
    expect(screen.queryByLabelText(/pairing code/i)).not.toBeInTheDocument();     // no code field while the link is unencrypted
    expect(mocks.v2.addNode).not.toHaveBeenCalled();
  });

  it("with TLS the add form (manual MAC + code) is available", async () => {
    mocks.state.tls = true;
    const user = userEvent.setup();
    render(<MemoryRouter><NodesPage /></MemoryRouter>);
    await user.click(await screen.findByRole("button", { name: /add node/i }));
    expect(await screen.findByLabelText(/pairing code/i)).toBeInTheDocument();
    expect(screen.queryByTestId("tls-required")).not.toBeInTheDocument();
  });

  it("remove asks for an in-page confirmation, then shows sent -> ok", async () => {
    const confirm = vi.spyOn(window, "confirm");
    const user = userEvent.setup();
    render(<MemoryRouter><NodesPage /></MemoryRouter>);
    const row = await screen.findByTestId("node-row-2");
    await user.click(within(row).getByRole("button", { name: /actions for/i }));
    await user.click(await screen.findByRole("menuitem", { name: /remove/i }));
    const dlg = await screen.findByRole("alertdialog");
    expect(within(dlg).getByText(/Remove Node 2/)).toBeInTheDocument();
    expect(mocks.v2.removeNode).not.toHaveBeenCalled();                            // nothing happens before the confirmation
    await user.click(within(dlg).getByRole("button", { name: /remove node/i }));
    expect(mocks.v2.removeNode).toHaveBeenCalledWith(2);
    await waitFor(() => expect(within(dlg).getByTestId("command-status")).toHaveAttribute("data-phase", "ok"), { timeout: 4000 });
    expect(confirm).not.toHaveBeenCalled();                                        // never window.confirm
  });

  it("a normal user sees the list but no action buttons", async () => {
    mocks.state.role = "user";
    render(<MemoryRouter><NodesPage /></MemoryRouter>);
    await screen.findByTestId("node-row-2");
    expect(screen.queryByRole("button", { name: /actions for/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add node/i })).not.toBeInTheDocument();
  });

  it("TX power: shown per node, dialog shows missing %/RSSI and pushes the chosen level", async () => {
    const user = userEvent.setup();
    mocks.state.nodes = [node(2, { name: "Far", txPowerDbm: 11, rssi: -82, live: { ...node(2).live!, lossPct24h: 14.2, missedPackets24h: 15 } }), node(3, { name: "Near" })];
    mocks.v2.patchNode = vi.fn(async () => ({ commandId: 8, status: "sent", result: null, number: 2 }));
    render(<MemoryRouter><NodesPage /></MemoryRouter>);
    await user.click(await screen.findByTestId("nodes-more-columns"));
    expect(await screen.findByTestId("tx-2")).toHaveTextContent("11 dBm");
    expect(screen.getByTestId("tx-3")).toHaveTextContent("8.5 dBm");               // default when the gateway reported nothing
    await user.click(screen.getAllByTestId("row-menu-2")[0]);
    await user.click(await screen.findByTestId("menu-tx-2"));
    const box = await screen.findByTestId("tx-link-now");
    expect(box).toHaveTextContent("14.2 % (15)");
    expect(box).toHaveTextContent("-82 dBm");
    expect(box).toHaveTextContent(/Weak link/);
    await user.click(screen.getByLabelText("15 dBm"));
    await user.click(screen.getByRole("button", { name: "Set TX power" }));
    await waitFor(() => expect(mocks.v2.patchNode).toHaveBeenCalledWith(2, { txPowerDbm: 15 }));
  });

  it("out of service: grey badge, menu toggle calls the backend", async () => {
    const user = userEvent.setup();
    mocks.state.nodes = [node(2, { name: "Main outlet", state: "out_of_service", outOfService: true, live: null as any }), node(3)];
    mocks.v2.setOutOfService = vi.fn(async () => ({ ok: true, outOfService: false }));
    render(<MemoryRouter><NodesPage /></MemoryRouter>);
    const row = await screen.findByTestId("node-row-2");
    expect(row).toHaveTextContent("out of service");
    await user.click(screen.getAllByTestId("row-menu-2")[0]);
    const item = await screen.findByTestId("menu-service-2");
    expect(item).toHaveTextContent("Back in service");
    await user.click(item);
    await waitFor(() => expect(mocks.v2.setOutOfService).toHaveBeenCalledWith(expect.any(Number), false));
  });

  it("shows an empty state when no node is paired", async () => {
    mocks.state.nodes = [];
    render(<MemoryRouter><NodesPage /></MemoryRouter>);
    expect(await screen.findByText(/No nodes are paired yet/)).toBeInTheDocument();
  });

  it("with two gateways: selector, the online one is the default, nodes grouped per gateway", async () => {
    const other: GatewayV2 = { ...gw, id: 2, gatewayId: "gw-other", state: "offline", nodes: 1, ip: "10.0.0.9" };
    mocks.v2.gateways = vi.fn(async () => ({ gateways: [gw, other].reverse(), mqtt: { tls: false, host: "b", port: 1883, remoteAddAllowed: false }, commandsEnabled: true }));
    mocks.state.nodes = [node(2, { name: "Real" }), node(4, { id: 9, gatewayRef: 2, name: "Stray" })];
    render(<MemoryRouter><NodesPage /></MemoryRouter>);
    const sel = await screen.findByLabelText("Gateway");
    expect(sel).toHaveValue("1");                                              // online gateway, although it is not the last in the list
    expect(screen.getByTestId("gateway-group-gw-test")).toHaveTextContent("Real");
    expect(screen.getByTestId("gateway-group-gw-other")).toHaveTextContent("Stray");
    expect(screen.getByTestId("gateway-group-gw-test")).not.toHaveTextContent("Stray");
  });
});
