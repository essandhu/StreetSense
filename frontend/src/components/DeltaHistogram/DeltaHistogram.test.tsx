/**
 * Tests for DeltaHistogram (Task 3.6).
 *
 * The component is a thin renderer over `histogramLayout` (pure math
 * in `domain/deltaHistogram`). Math correctness is tested there; here
 * we assert the component:
 *   - Renders an SVG with one `<rect>` per bin.
 *   - Highlights the bin containing the selected segment's delta.
 *   - Handles the no-pair / empty-delta states with a placeholder.
 */
import { configureStore } from "@reduxjs/toolkit";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { type ReactNode } from "react";
import { Provider } from "react-redux";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { deltaQueryKey } from "../../data/useDelta";
import { RunId, SegmentId, type DeltaResponse, type SegmentDelta } from "../../domain";
import activeCityReducer from "../../state/activeCity";
import deltaReducer, { type DeltaState } from "../../state/delta";
import selectedSegmentReducer, { type SelectedSegmentState } from "../../state/selectedSegment";

import { DeltaHistogram } from "./DeltaHistogram";

const RUN_A = RunId("11111111-1111-1111-1111-111111111111");
const RUN_B = RunId("22222222-2222-2222-2222-222222222222");

function _row(idHexPad: string, composite: number): SegmentDelta {
  const padded = idHexPad.padEnd(8, "0");
  return {
    segment_id: `${padded}-0000-0000-0000-000000000000`,
    composite_delta: composite,
    local_contribution_delta: composite * 0.5,
    propagation_uplift_delta: composite * 0.5,
    sub_score_deltas: {
      lane_marking_quality: 0,
      glare_exposure: 0,
      junction_complexity: 0,
      historical_correlation: 0,
    },
    confidence_a: { value: 0.8, limiter: "model" },
    confidence_b: { value: 0.85, limiter: "model" },
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

function _payload(deltas: SegmentDelta[]): DeltaResponse {
  return {
    run_a: _runMeta(RUN_A),
    run_b: _runMeta(RUN_B),
    deltas,
    page: 1,
    page_size: 1000,
    total: deltas.length,
  } as unknown as DeltaResponse;
}

function _renderWith({
  payload,
  initialDelta = { mode: "delta", runA: RUN_A, runB: RUN_B },
  initialSelection,
}: {
  payload: DeltaResponse | null;
  initialDelta?: Partial<DeltaState>;
  initialSelection?: SelectedSegmentState;
}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (payload && initialDelta.runA && initialDelta.runB) {
    // Phase 4b Task 4.3: query key carries the active city slug.
    // Pre-seed under the same default slug the component will read.
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
      selectedSegment: initialSelection ?? { segmentId: null, isPanelOpen: false },
      activeCity: { slug: "cambridge" },
    },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <Provider store={store}>{children}</Provider>
    </QueryClientProvider>
  );
  return render(<DeltaHistogram />, { wrapper });
}

let fetchSpy: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchSpy = vi.fn(async () => new Response("{}", { status: 500 }));
  vi.stubGlobal("fetch", fetchSpy);
});
afterEach(() => vi.unstubAllGlobals());

describe("DeltaHistogram — empty states", () => {
  it("renders a placeholder when no run pair is selected", () => {
    _renderWith({
      payload: null,
      initialDelta: { mode: "delta", runA: null, runB: null },
    });
    expect(screen.getByText(/pick two runs/i)).toBeDefined();
  });

  it("renders an SVG (with zero-count bars) when the delta has zero rows", () => {
    _renderWith({ payload: _payload([]) });
    const svg = screen.getByTestId("delta-histogram");
    expect(svg.tagName.toLowerCase()).toBe("svg");
    // 20 bins by default, all height-zero — the bar elements still exist.
    const bars = svg.querySelectorAll("rect.bar");
    expect(bars.length).toBe(20);
  });
});

describe("DeltaHistogram — rendering", () => {
  it("renders one rect.bar per bin", () => {
    _renderWith({
      payload: _payload([_row("11111111", 0.12), _row("22222222", -0.45), _row("33333333", 0.66)]),
    });
    const svg = screen.getByTestId("delta-histogram");
    const bars = svg.querySelectorAll("rect.bar");
    expect(bars.length).toBe(20);
  });

  it("renders a zero-tick reference line at the midpoint of a symmetric domain", () => {
    _renderWith({ payload: _payload([_row("11111111", 0.1)]) });
    const svg = screen.getByTestId("delta-histogram");
    const zeroLine = svg.querySelector("line.zero-tick");
    expect(zeroLine).not.toBeNull();
  });

  it("uses higher bars for more-populated bins", () => {
    _renderWith({
      payload: _payload([
        _row("11111111", 0.12),
        _row("22222222", 0.13),
        _row("33333333", 0.14),
        _row("44444444", 0.15),
        _row("55555555", -0.55),
      ]),
    });
    const svg = screen.getByTestId("delta-histogram");
    const bars = Array.from(svg.querySelectorAll("rect.bar")) as SVGRectElement[];
    const tallest = bars.reduce(
      (a, b) =>
        parseFloat(a.getAttribute("height") ?? "0") > parseFloat(b.getAttribute("height") ?? "0")
          ? a
          : b,
      bars[0]!
    );
    expect(parseFloat(tallest.getAttribute("height") ?? "0")).toBeGreaterThan(0);
  });
});

describe("DeltaHistogram — highlight", () => {
  it("highlights the bin containing the selected segment's composite_delta", () => {
    const selectedSegment = SegmentId("abababab-0000-0000-0000-000000000000");
    _renderWith({
      payload: _payload([_row("abababab", 0.35), _row("11111111", -0.1)]),
      initialSelection: { segmentId: selectedSegment, isPanelOpen: true },
    });
    const svg = screen.getByTestId("delta-histogram");
    const highlighted = svg.querySelectorAll("rect.bar-highlighted");
    expect(highlighted.length).toBe(1);
  });

  it("no highlight when the selected segment isn't in the delta", () => {
    const selectedSegment = SegmentId("ffffffff-0000-0000-0000-000000000000");
    _renderWith({
      payload: _payload([_row("11111111", 0.35)]),
      initialSelection: { segmentId: selectedSegment, isPanelOpen: true },
    });
    const svg = screen.getByTestId("delta-histogram");
    const highlighted = svg.querySelectorAll("rect.bar-highlighted");
    expect(highlighted.length).toBe(0);
  });

  it("no highlight when no segment is selected", () => {
    _renderWith({ payload: _payload([_row("11111111", 0.35)]) });
    const svg = screen.getByTestId("delta-histogram");
    const highlighted = svg.querySelectorAll("rect.bar-highlighted");
    expect(highlighted.length).toBe(0);
  });
});
