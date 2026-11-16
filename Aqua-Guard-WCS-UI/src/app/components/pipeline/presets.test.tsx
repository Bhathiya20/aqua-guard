import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ v2: {} as any }));
vi.mock("../../lib/v2", async (orig) => ({ ...(await orig<typeof import("../../lib/v2")>()), v2: mocks.v2 }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { PresetDialog } from "./PresetDialog";

const PRESETS = [
  { id: "calibration_rig", name: "Calibration rig", description: "Nodes in series into a container.", physicalSetup: ["Connect in series", "Hose into a bucket", "Open slowly"], slots: ["Node A", "Node B", "Node C"], minNodes: 1, maxNodes: 3 },
  { id: "leak_bench", name: "Leak bench", description: "Strong zone with a leak tap.", physicalSetup: ["Supply to Node A", "T fitting", "Leak tap into a bucket"], slots: ["Node A (before the leak)", "Node B (after the leak)"], minNodes: 2, maxNodes: 2 },
];
const node = (n: number) => ({ id: n + 10, number: n, type: "flow", registryState: "paired", state: "online", name: `N${n}`, label: `Node ${n}` });

beforeEach(() => {
  Object.assign(mocks.v2, {
    pipelinePresets: vi.fn(async () => ({ presets: PRESETS })),
    nodes: vi.fn(async () => ({ nodes: [node(1), node(2), node(3)] })),
    createFromPreset: vi.fn(async () => ({ id: 42, version: 1, activeVersion: null, valid: true, messages: [], missingNodes: 0 })),
  });
});

const renderDlg = (names: string[] = []) => render(
  <MemoryRouter initialEntries={["/pipelines"]}><Routes>
    <Route path="/pipelines" element={<PresetDialog open onClose={() => {}} existingNames={names} />} />
    <Route path="/pipelines/:id" element={<div>editor 42</div>} />
  </Routes></MemoryRouter>);

describe("New from preset", () => {
  it("shows what each preset tests and how to build it", async () => {
    const user = userEvent.setup();
    renderDlg();
    expect(await screen.findByTestId("preset-details")).toHaveTextContent("Nodes in series into a container.");
    await user.click(screen.getByTestId("preset-leak_bench"));
    expect(screen.getByTestId("preset-details")).toHaveTextContent("Strong zone with a leak tap.");
    expect(screen.getByText("Leak tap into a bucket")).toBeInTheDocument();
    expect(screen.getByLabelText("Node A (before the leak)")).toBeInTheDocument();
  });

  it("creates an inactive pipeline with the chosen nodes in slot order and opens it", async () => {
    const user = userEvent.setup();
    renderDlg();
    await user.click(await screen.findByTestId("preset-leak_bench"));
    await user.selectOptions(screen.getByLabelText("Node A (before the leak)"), "13");
    await user.selectOptions(screen.getByLabelText("Node B (after the leak)"), "11");
    await user.click(screen.getByTestId("preset-create"));
    await waitFor(() => expect(mocks.v2.createFromPreset).toHaveBeenCalledWith({ preset: "leak_bench", nodeIds: [13, 11], name: "Leak bench" }));
    expect(await screen.findByText("editor 42")).toBeInTheDocument();
  });

  it("the calibration rig lets you choose how many nodes; a node can be used only once; duplicate names are flagged", async () => {
    const user = userEvent.setup();
    renderDlg(["Calibration rig"]);
    await screen.findByTestId("preset-details");
    expect(screen.getByTestId("preset-dup-name")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Number of nodes"), "3");
    expect(screen.getByLabelText("Node C")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Node A"), "11");
    await user.selectOptions(screen.getByLabelText("Node B"), "11");
    expect(screen.getByRole("alert")).toHaveTextContent("used once");
    expect(screen.getByTestId("preset-create")).toBeDisabled();
  });
});
