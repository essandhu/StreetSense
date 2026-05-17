/**
 * Tests for LargestChangesList (Task 3.5).
 *
 * Reads `useDelta()` for the current run pair, sorts by
 * |composite_delta| DESC, and renders the top N rows. Clicking a row
 * dispatches `openSegment(segmentId)` so the existing Phase 3
 * SegmentDetailPanel surfaces the row's context.
 */
import { configureStore } from "@reduxjs/toolkit";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { type ReactNode } from "react";
import { Provider } from "react-redux";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RunId, type DeltaResponse, type SegmentDelta } from "../../domain";
import { deltaQueryKey } from "../../data/useDelta";
import activeCityReducer from "../../state/activeCity";
import deltaReducer, { type DeltaState } from "../../state/delta";
import selectedSegmentReducer from "../../state/selectedSegment";

import { LargestChangesList } from "./LargestChangesList";

const RUN_A = RunId("11111111-1111-1111-1111-111111111111");
const RUN_B = RunId("22222222-2222-2222-2222-222222222222");

function _row(idHexPad: string, composite: number, opts: Partial<SegmentDelta> = {}): SegmentDelta {
  const padded = idHexPad.padEnd(8, "0");
  return {
    segment_id: `${padded}-0000-0000-0000-000000000000`,
    composite_delta: composite,
    local_contribution_delta: composite * 0.6,
    propagation_uplift_delta: composite * 0.4,
    sub_score_deltas: {
      lane_marking_quality: 0,
      glare_exposure: 0,
      junction_complexity: 0,
      historical_correlation: 0,
    },
    confidence_a: { value: 0.8, limiter: "model" },
    confidence_b: { value: 0.85, limiter: "model" },
    ...opts,
  } as SegmentDelta;
}

function _runMeta(id: string) {
  return {
    scoring_run_id: id,
    scoring_run_timestamp: "2026-05-01T12:00:00Z",
    perception_model_version: "v1",
    osm_snapshot_date: "2026-05-01",
    imagery_capture_window_start: "2026-04-01",
    imagery_capture_window_end: "2026-05-01",
    propagation_algorithm_version: "pagerank-diffusion-0.1.0",
  };
}

function _deltaPayload(deltas: SegmentDelta[]): DeltaResponse {
  return {
    run_a: _runMeta(RUN_A),
    run_b: _runMeta(RUN_B),
    deltas,
    page: 1,
    page_size: 1000,
    total: deltas.length,
  } as unknown as DeltaResponse;
}

function _renderWithStores({
  payload,
  initialDelta = { mode: "delta", runA: RUN_A, runB: RUN_B },
}: {
  payload: DeltaResponse | null;
  initialDelta?: Partial<DeltaState>;
}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (payload && initialDelta.runA && initialDelta.runB) {
    // Phase 4b Task 4.3: query key carries the active city slug.
    qc.setQueryData(
      deltaQueryKey("cambridge", initialDelta.runA, initialDelta.runB),
      payload,
    );
  }
  const store = configureStore({
    reducer: {
      delta: deltaReducer,
      selectedSegment: selectedSegmentReducer,
      activeCity: activeCityReducer,
    },
    preloadedState: {
      delta: {
        mode: initialDelta.mode ?? "delta",
        runA: initialDelta.runA ?? null,
        runB: initialDelta.runB ?? null,
      },
      activeCity: { slug: "cambridge" },
    },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <Provider store={store}>{children}</Provider>
    </QueryClientProvider>
  );
  return { store, ...render(<LargestChangesList />, { wrapper }) };
}

let fetchSpy: ReturnType<typeof vi.fn>;
beforeEach(() => {
  // Most tests pre-seed the query cache, but a safety net so the hook
  // never tries to hit a real network if a test forgets.
  fetchSpy = vi.fn(async () => new Response("{}", { status: 500 }));
  vi.stubGlobal("fetch", fetchSpy);
});
afterEach(() => vi.unstubAllGlobals());

describe("LargestChangesList — empty / disabled states", () => {
  it("renders a placeholder when no run pair is selected", () => {
    _renderWithStores({ payload: null, initialDelta: { mode: "delta", runA: null, runB: null } });
    expect(screen.getByText(/pick two runs/i)).toBeDefined();
  });

  it("renders an empty-list message when the delta has zero rows", () => {
    _renderWithStores({ payload: _deltaPayload([]) });
    expect(screen.getByText(/no segment changes/i)).toBeDefined();
  });
});

describe("LargestChangesList — sorting", () => {
  it("orders rows by |composite_delta| descending", async () => {
    const rows = [
      _row("11111111", 0.05),
      _row("22222222", -0.42),
      _row("33333333", 0.18),
      _row("44444444", -0.01),
      _row("55555555", 0.3),
    ];
    _renderWithStores({ payload: _deltaPayload(rows) });
    const list = await screen.findByTestId("largest-changes-list");
    const items = within(list).getAllByRole("button");
    // Expected order by |composite_delta| desc: 22222222, 55555555, 33333333, 11111111, 44444444
    expect(items[0]?.dataset.segmentId).toBe("22222222-0000-0000-0000-000000000000");
    expect(items[1]?.dataset.segmentId).toBe("55555555-0000-0000-0000-000000000000");
    expect(items[2]?.dataset.segmentId).toBe("33333333-0000-0000-0000-000000000000");
    expect(items[3]?.dataset.segmentId).toBe("11111111-0000-0000-0000-000000000000");
    expect(items[4]?.dataset.segmentId).toBe("44444444-0000-0000-0000-000000000000");
  });

  it("caps the rendered list to the top 100 by default", async () => {
    const rows = Array.from({ length: 250 }, (_, i) => {
      const hex = i.toString(16).padStart(8, "0");
      return _row(hex, (250 - i) / 1000);
    });
    _renderWithStores({ payload: _deltaPayload(rows) });
    const list = await screen.findByTestId("largest-changes-list");
    const items = within(list).getAllByRole("button");
    expect(items.length).toBe(100);
  });
});

describe("LargestChangesList — row content", () => {
  it("each row shows the signed composite_delta with at most three decimals", async () => {
    const rows = [_row("aaaaaaaa", 0.1234), _row("bbbbbbbb", -0.0567)];
    _renderWithStores({ payload: _deltaPayload(rows) });
    const list = await screen.findByTestId("largest-changes-list");
    const text = list.textContent ?? "";
    // 0.1234 → "+0.123" and -0.0567 → "-0.057" (or similar — 3 dp).
    expect(text).toMatch(/\+?0\.123/);
    expect(text).toMatch(/-0\.057/);
  });

  it("each row exposes data-segment-id for downstream coupling", async () => {
    const rows = [_row("cafef00d", 0.42)];
    _renderWithStores({ payload: _deltaPayload(rows) });
    const list = await screen.findByTestId("largest-changes-list");
    const button = within(list).getByRole("button");
    expect(button.dataset.segmentId).toBe("cafef00d-0000-0000-0000-000000000000");
  });
});

describe("LargestChangesList — selection", () => {
  it("clicking a row dispatches openSegment(segmentId)", async () => {
    const rows = [_row("deadbeef", 0.5), _row("baadf00d", -0.1)];
    const { store } = _renderWithStores({ payload: _deltaPayload(rows) });
    const list = await screen.findByTestId("largest-changes-list");
    const buttons = within(list).getAllByRole("button");
    fireEvent.click(buttons[0]!);
    await waitFor(() => {
      const sel = store.getState().selectedSegment;
      expect(sel.segmentId).toBe("deadbeef-0000-0000-0000-000000000000");
      expect(sel.isPanelOpen).toBe(true);
    });
  });
});
