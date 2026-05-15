import type { Meta, StoryObj } from "@storybook/react-vite";

import type { SubScore, SubScores } from "../../domain";
import { SubScoreChart } from "./SubScoreChart";

const _sub = (value: number, isStub: boolean): SubScore => ({
  value,
  confidence: 1.0,
  is_stub: isStub,
  metadata: {},
});

const _scores = (overrides: Partial<SubScores> = {}): SubScores => ({
  glare_exposure: _sub(0.5, false),
  lane_marking_quality: _sub(0.4, false),
  junction_complexity: _sub(0.0, true),
  historical_correlation: _sub(0.0, true),
  ...overrides,
});

const meta = {
  title: "SegmentDetail/SubScoreChart",
  component: SubScoreChart,
  parameters: { layout: "centered", backgrounds: { default: "dark" } },
} satisfies Meta<typeof SubScoreChart>;
export default meta;

type Story = StoryObj<typeof meta>;

// Phase 4 steady-state: all four sub-scores are real. The stub hatch
// treatment never appears under normal Phase 4 data — it's preserved
// in the component as a defensive fallback for pre-Phase-4 sentinel
// rows that may still be queried via the legacy fallback branch.

/**
 * Phase 4 default — every sub-score is real with a meaningful value.
 * This is what a typical Cambridge segment looks like under the
 * production scoring run.
 */
export const Phase4Default: Story = {
  args: {
    subScores: _scores({
      glare_exposure: _sub(0.7, false),
      lane_marking_quality: _sub(0.45, false),
      junction_complexity: _sub(0.6, false),
      historical_correlation: _sub(0.3, false),
    }),
  },
};

/** A high-risk segment: every sub-score in the upper half of the range. */
export const HighRiskAllReal: Story = {
  args: {
    subScores: _scores({
      glare_exposure: _sub(0.92, false),
      lane_marking_quality: _sub(0.88, false),
      junction_complexity: _sub(0.78, false),
      historical_correlation: _sub(0.85, false),
    }),
  },
};

/** A low-risk segment: every sub-score in the lower half of the range. */
export const LowRiskAllReal: Story = {
  args: {
    subScores: _scores({
      glare_exposure: _sub(0.08, false),
      lane_marking_quality: _sub(0.12, false),
      junction_complexity: _sub(0.05, false),
      historical_correlation: _sub(0.10, false),
    }),
  },
};

/**
 * Legacy mixed-stub state — pre-Phase-4 sentinel branch where two
 * sub-scores are real and two are stubbed. Preserved here so the
 * fallback rendering stays testable in Storybook even though Phase 4
 * production data never produces this combination.
 */
export const LegacyMixed: Story = {
  args: { subScores: _scores() },
};

/** Legacy all-stub branch — pre-Phase-2 archaeology data. */
export const LegacyAllStub: Story = {
  args: {
    subScores: _scores({
      glare_exposure: _sub(0.0, true),
      lane_marking_quality: _sub(0.0, true),
    }),
  },
};
