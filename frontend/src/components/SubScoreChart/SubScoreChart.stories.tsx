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

export const AllReal: Story = {
  args: {
    subScores: _scores({
      glare_exposure: _sub(0.7, false),
      lane_marking_quality: _sub(0.45, false),
      junction_complexity: _sub(0.6, false),
      historical_correlation: _sub(0.3, false),
    }),
  },
};

export const AllStub: Story = {
  args: {
    subScores: _scores({
      glare_exposure: _sub(0.0, true),
      lane_marking_quality: _sub(0.0, true),
    }),
  },
};

export const MixedGlareAndLaneReal: Story = {
  args: { subScores: _scores() },
};

export const EdgeCaseHighRisk: Story = {
  args: {
    subScores: _scores({
      glare_exposure: _sub(0.98, false),
      lane_marking_quality: _sub(0.92, false),
    }),
  },
};

export const EdgeCaseLowRisk: Story = {
  args: {
    subScores: _scores({
      glare_exposure: _sub(0.02, false),
      lane_marking_quality: _sub(0.05, false),
    }),
  },
};
