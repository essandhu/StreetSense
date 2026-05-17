import { configureStore } from "@reduxjs/toolkit";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { Provider } from "react-redux";

import { RunId, type RunListResponse } from "../../domain";
import { runsQueryKey } from "../../data/useRuns";
import activeCityReducer from "../../state/activeCity";
import deltaReducer, { type DeltaState } from "../../state/delta";

import { RunPicker } from "./RunPicker";

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

const _SAMPLE_RUNS: RunListResponse = {
  runs: [
    _runMeta("11111111-1111-1111-1111-111111111111", "2026-05-08T12:00:00Z"),
    _runMeta("22222222-2222-2222-2222-222222222222", "2026-05-01T12:00:00Z"),
    _runMeta("33333333-3333-3333-3333-333333333333", "2026-04-24T12:00:00Z"),
  ],
} as RunListResponse;

const _EMPTY_RUNS: RunListResponse = { runs: [] } as RunListResponse;

type DecoratorArgs = {
  runs?: RunListResponse;
  initialDelta?: Partial<DeltaState>;
};

/**
 * Mounts the picker against a synthetic QueryClient cache (so no
 * fetch fires) and a Redux store seeded with the requested delta
 * state. Each story builds its own client + store so cross-story
 * state does not leak.
 */
const _wrap = ({ runs, initialDelta }: DecoratorArgs) => {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  qc.setQueryData(runsQueryKey("cambridge"), runs ?? _SAMPLE_RUNS);
  const store = configureStore({
    reducer: { delta: deltaReducer, activeCity: activeCityReducer },
    preloadedState: {
      delta: {
        mode: initialDelta?.mode ?? "delta",
        runA: initialDelta?.runA ?? null,
        runB: initialDelta?.runB ?? null,
      },
      activeCity: { slug: "cambridge" },
    },
  });
  return (Story: React.ComponentType) => (
    <QueryClientProvider client={qc}>
      <Provider store={store}>
        <div style={{ padding: 24, background: "#111114", minHeight: 120 }}>
          <Story />
        </div>
      </Provider>
    </QueryClientProvider>
  );
};

const meta = {
  title: "Delta/RunPicker",
  component: RunPicker,
  parameters: { layout: "centered" },
} satisfies Meta<typeof RunPicker>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Both runs unpicked — the start state when the user enters delta mode. */
export const NoSelection: Story = {
  decorators: [_wrap({})],
};

/** Both runs selected — the steady-state delta view. */
export const BothSelected: Story = {
  decorators: [
    _wrap({
      initialDelta: {
        runA: RunId("11111111-1111-1111-1111-111111111111"),
        runB: RunId("22222222-2222-2222-2222-222222222222"),
      },
    }),
  ],
};

/** Only one run picked — swap is enabled, useDelta hook stays idle. */
export const OnePicked: Story = {
  decorators: [
    _wrap({
      initialDelta: {
        runA: RunId("11111111-1111-1111-1111-111111111111"),
        runB: null,
      },
    }),
  ],
};

/** Server returned an empty list — happens on a fresh deploy pre-bootstrap. */
export const EmptyServer: Story = {
  decorators: [_wrap({ runs: _EMPTY_RUNS })],
};
