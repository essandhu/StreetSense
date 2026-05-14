import type { Meta, StoryObj } from "@storybook/react-vite";

import { ConfidenceDial } from "./ConfidenceDial";

const meta = {
  title: "SegmentDetail/ConfidenceDial",
  component: ConfidenceDial,
  parameters: { layout: "centered", backgrounds: { default: "dark" } },
} satisfies Meta<typeof ConfidenceDial>;
export default meta;

type Story = StoryObj<typeof meta>;

export const FreshnessLimited: Story = {
  args: { confidence: { value: 0.34, limiter: "freshness" } },
};

export const CoverageLimited: Story = {
  args: { confidence: { value: 0.18, limiter: "coverage" } },
};

export const ModelLimited: Story = {
  args: { confidence: { value: 0.4, limiter: "model" } },
};

export const HighConfidence: Story = {
  args: { confidence: { value: 0.92, limiter: "freshness" } },
};

export const LowConfidence: Story = {
  args: { confidence: { value: 0.07, limiter: "coverage" } },
};
