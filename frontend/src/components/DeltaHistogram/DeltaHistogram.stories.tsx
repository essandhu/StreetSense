import { configureStore } from "@reduxjs/toolkit";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { Provider } from "react-redux";

import { deltaQueryKey } from "../../data/useDelta";
import { RunId, SegmentId, type DeltaResponse, type SegmentDelta } from "../../domain";
import activeCityReducer from "../../state/activeCity";
import deltaReducer, { type DeltaState } from "../../state/delta";
import selectedSegmentReducer from "../../state/selectedSegment";

import { DeltaHistogram } from "./DeltaHistogram";

const RUN_A = RunId("11111111-1111-1111-1111-111111111111");
const RUN_B = RunId("22222222-2222-2222-2222-222222222222");

function _row(idHex: string, composite: number): SegmentDelta {
  const padded = idHex.padEnd(8, "0");
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

/** A bell-ish synthetic distribution centered slightly positive. */
function _bellShapedRows(count: number, mean = 0.05, sd = 0.18): SegmentDelta[] {
  const rows: SegmentDelta[] = [];
  for (let i = 0; i < count; i++) {
    // Box-Muller transform for a normal sample.
    const u1 = (i + 1) / (count + 1);
    const u2 = ((i * 7919) % count) / count;
    const z = Math.sqrt(-2 * Math.log(u1 || 1e-9)) * Math.cos(2 * Math.PI * u2);
    const v = Math.max(-1, Math.min(1, mean + z * sd));
    rows.push(_row(`r${i.toString(16)}`, v));
  }
  return rows;
}

function _payload(deltas: SegmentDelta[]): DeltaResponse {
  return {
    run_a: {
      scoring_run_id: RUN_A,
      scoring_run_timestamp: "2026-05-08T12:00:00Z",
      perception_model_version: "v1",
      osm_snapshot_date: "2026-05-01",
      imagery_capture_window_start: "2026-04-01",
      imagery_capture_window_end: "2026-05-01",
      propagation_algorithm_version: "pagerank-diffusion-0.1.0",
    },
    run_b: {
      scoring_run_id: RUN_B,
      scoring_run_timestamp: "2026-05-15T12:00:00Z",
      perception_model_version: "v1",
      osm_snapshot_date: "2026-05-08",
      imagery_capture_window_start: "2026-04-08",
      imagery_capture_window_end: "2026-05-08",
      propagation_algorithm_version: "pagerank-diffusion-0.1.0",
    },
    deltas,
    page: 1,
    page_size: 1000,
    total: deltas.length,
  } as unknown as DeltaResponse;
}

type WrapArgs = {
  rows: SegmentDelta[];
  highlightSegmentId?: string | null;
};

const _wrap = ({ rows, highlightSegmentId = null }: WrapArgs) => {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  qc.setQueryData(deltaQueryKey("cambridge", RUN_A, RUN_B), _payload(rows));
  const initialDelta: DeltaState = { mode: "delta", runA: RUN_A, runB: RUN_B };
  const store = configureStore({
    reducer: {
      delta: deltaReducer,
      selectedSegment: selectedSegmentReducer,
      activeCity: activeCityReducer,
    },
    preloadedState: {
      delta: initialDelta,
      selectedSegment: {
        segmentId: highlightSegmentId ? SegmentId(highlightSegmentId) : null,
        isPanelOpen: highlightSegmentId !== null,
      },
      activeCity: { slug: "cambridge" },
    },
  });
  return (Story: React.ComponentType) => (
    <QueryClientProvider client={qc}>
      <Provider store={store}>
        <div style={{ padding: 16, background: "#0f0f12", display: "inline-block" }}>
          <Story />
        </div>
      </Provider>
    </QueryClientProvider>
  );
};

const meta = {
  title: "Delta/DeltaHistogram",
  component: DeltaHistogram,
  parameters: { layout: "centered" },
} satisfies Meta<typeof DeltaHistogram>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Synthetic 200-segment bell distribution, no highlight. */
export const BellNoHighlight: Story = {
  decorators: [_wrap({ rows: _bellShapedRows(200) })],
};

/** Same distribution, with one bin highlighted via the selected segment. */
export const BellWithHighlight: Story = {
  decorators: [
    _wrap({
      rows: [..._bellShapedRows(199), _row("aaaaaaaa", 0.62)],
      highlightSegmentId: "aaaaaaaa-0000-0000-0000-000000000000",
    }),
  ],
};

/** Bimodal: two clusters of change (e.g., two different streets gaining/losing risk). */
export const Bimodal: Story = {
  decorators: [
    _wrap({
      rows: [..._bellShapedRows(80, -0.4, 0.08), ..._bellShapedRows(80, 0.45, 0.08)],
    }),
  ],
};

/** Sparse: only a handful of changed segments. */
export const Sparse: Story = {
  decorators: [
    _wrap({
      rows: [
        _row("01010101", -0.32),
        _row("02020202", -0.08),
        _row("03030303", 0.01),
        _row("04040404", 0.21),
        _row("05050505", 0.55),
      ],
    }),
  ],
};

/** Zero deltas — all bars flat baseline. */
export const Empty: Story = {
  decorators: [_wrap({ rows: [] })],
};
