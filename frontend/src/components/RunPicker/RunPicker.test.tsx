/**
 * Tests for RunPicker (Task 3.3).
 *
 * Two dropdowns + a swap button. Source list comes from `useRuns()`;
 * selection state lives in the `delta` Redux slice. The picker
 * dispatches `setRunA`, `setRunB`, and `swapRuns` — never reads
 * server data via Redux.
 */
import { configureStore } from "@reduxjs/toolkit";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { Provider } from "react-redux";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RunId, type RunListResponse } from "../../domain";
import activeCityReducer from "../../state/activeCity";
import deltaReducer, { type DeltaState } from "../../state/delta";

import { RunPicker } from "./RunPicker";

const RUN_NEW_ID = "11111111-1111-1111-1111-111111111111";
const RUN_OLD_ID = "22222222-2222-2222-2222-222222222222";

function _runMeta(id: string, tsIso: string) {
  return {
    scoring_run_id: id,
    scoring_run_timestamp: tsIso,
    perception_model_version: "stand-in-onnx-0.1.0",
    osm_snapshot_date: "2026-05-01",
    imagery_capture_window_start: "2025-11-01",
    imagery_capture_window_end: "2026-05-01",
    propagation_algorithm_version: "pagerank-diffusion-0.1.0",
  };
}

const _PAYLOAD_TWO_RUNS: RunListResponse = {
  runs: [
    _runMeta(RUN_NEW_ID, "2026-05-08T12:00:00Z"),
    _runMeta(RUN_OLD_ID, "2026-05-01T12:00:00Z"),
  ],
} as RunListResponse;

const _PAYLOAD_EMPTY: RunListResponse = { runs: [] } as RunListResponse;

let fetchSpy: ReturnType<typeof vi.fn>;
function _mockRunsResponse(payload: RunListResponse) {
  fetchSpy = vi.fn(async () => {
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchSpy);
}

beforeEach(() => {
  _mockRunsResponse(_PAYLOAD_TWO_RUNS);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function _renderWithStores(deltaInitial?: Partial<DeltaState>) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const store = configureStore({
    // Phase 4b Task 4.3: useRuns reads the active city slug from
    // the activeCity slice, so it must be wired into the store.
    reducer: { delta: deltaReducer, activeCity: activeCityReducer },
    preloadedState: deltaInitial
      ? {
          delta: {
            mode: deltaInitial.mode ?? "delta",
            runA: deltaInitial.runA ?? null,
            runB: deltaInitial.runB ?? null,
          },
          activeCity: { slug: "cambridge" },
        }
      : undefined,
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <Provider store={store}>{children}</Provider>
    </QueryClientProvider>
  );
  return { store, ...render(<RunPicker />, { wrapper }) };
}

describe("RunPicker — loading and empty states", () => {
  it("renders a loading affordance while runs are pending", async () => {
    _mockRunsResponse(_PAYLOAD_TWO_RUNS); // ensure fresh spy
    _renderWithStores();
    // Pre-resolution: dropdown is empty but the component is present.
    expect(screen.getByTestId("run-picker")).toBeDefined();
    await waitFor(() => expect(screen.getByLabelText(/run a/i)).toBeDefined());
  });

  it("renders an empty-list message when the server returns zero runs", async () => {
    _mockRunsResponse(_PAYLOAD_EMPTY);
    _renderWithStores();
    await waitFor(() => expect(screen.getByText(/no scoring runs/i)).toBeDefined());
  });
});

/**
 * Wait until the runs dropdown has finished loading — both `<select>`
 * elements have all three options (placeholder + 2 runs). The label
 * exists immediately because the component renders the `<select>`
 * shell before useRuns resolves; this helper waits for the resolved
 * state so subsequent assertions see populated options.
 */
async function _awaitRunsLoaded(): Promise<{
  runA: HTMLSelectElement;
  runB: HTMLSelectElement;
}> {
  const runA = (await screen.findByLabelText(/run a/i)) as HTMLSelectElement;
  const runB = screen.getByLabelText(/run b/i) as HTMLSelectElement;
  await waitFor(() => expect(runA.options.length).toBeGreaterThan(1));
  return { runA, runB };
}

describe("RunPicker — dropdowns", () => {
  it("renders two dropdowns labeled run A and run B with the full list of runs", async () => {
    _renderWithStores();
    const { runA, runB } = await _awaitRunsLoaded();
    // Each option for a real run + the placeholder = 3 options total.
    expect(runA.options.length).toBe(3);
    expect(runB.options.length).toBe(3);
    // Both real run IDs appear as option values.
    const aValues = Array.from(runA.options).map((o) => o.value);
    expect(aValues).toContain(RUN_NEW_ID);
    expect(aValues).toContain(RUN_OLD_ID);
  });

  it("reflects current deltaSlice selection in the dropdowns", async () => {
    _renderWithStores({ mode: "delta", runA: RunId(RUN_NEW_ID), runB: RunId(RUN_OLD_ID) });
    const { runA, runB } = await _awaitRunsLoaded();
    expect(runA.value).toBe(RUN_NEW_ID);
    expect(runB.value).toBe(RUN_OLD_ID);
  });

  it("dispatches setRunA on dropdown A change", async () => {
    const { store } = _renderWithStores({ mode: "delta", runA: null, runB: null });
    const { runA } = await _awaitRunsLoaded();
    fireEvent.change(runA, { target: { value: RUN_NEW_ID } });
    expect(store.getState().delta.runA).toBe(RUN_NEW_ID);
  });

  it("dispatches setRunB on dropdown B change", async () => {
    const { store } = _renderWithStores({ mode: "delta", runA: null, runB: null });
    const { runB } = await _awaitRunsLoaded();
    fireEvent.change(runB, { target: { value: RUN_OLD_ID } });
    expect(store.getState().delta.runB).toBe(RUN_OLD_ID);
  });

  it("clearing a dropdown selection sets the slice value to null", async () => {
    const { store } = _renderWithStores({
      mode: "delta",
      runA: RunId(RUN_NEW_ID),
      runB: RunId(RUN_OLD_ID),
    });
    const { runA } = await _awaitRunsLoaded();
    fireEvent.change(runA, { target: { value: "" } });
    expect(store.getState().delta.runA).toBeNull();
  });
});

describe("RunPicker — swap", () => {
  it("renders a swap button", async () => {
    _renderWithStores();
    await waitFor(() => expect(screen.getByRole("button", { name: /swap/i })).toBeDefined());
  });

  it("clicking swap dispatches swapRuns", async () => {
    const { store } = _renderWithStores({
      mode: "delta",
      runA: RunId(RUN_NEW_ID),
      runB: RunId(RUN_OLD_ID),
    });
    await screen.findByLabelText(/run a/i);
    fireEvent.click(screen.getByRole("button", { name: /swap/i }));
    expect(store.getState().delta.runA).toBe(RUN_OLD_ID);
    expect(store.getState().delta.runB).toBe(RUN_NEW_ID);
  });

  it("swap button is disabled when both runs are null", async () => {
    _renderWithStores({ mode: "delta", runA: null, runB: null });
    await screen.findByLabelText(/run a/i);
    const swap = screen.getByRole("button", { name: /swap/i }) as HTMLButtonElement;
    expect(swap.disabled).toBe(true);
  });
});
