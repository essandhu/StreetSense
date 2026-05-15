import type { Meta, StoryObj } from "@storybook/react-vite";

import { CompositeBreakdown } from "./CompositeBreakdown";

const meta = {
  title: "SegmentDetail/CompositeBreakdown",
  component: CompositeBreakdown,
  parameters: { layout: "centered", backgrounds: { default: "dark" } },
} satisfies Meta<typeof CompositeBreakdown>;
export default meta;

type Story = StoryObj<typeof meta>;

/** Steady-state Phase 4: real algorithm + meaningful uplift. */
export const Phase4Default: Story = {
  args: {
    compositeRisk: 0.65,
    localContribution: 0.5,
    propagationUplift: 0.15,
    algorithm: { name: "pagerank-diffusion", version: "0.1.0" },
  },
};

/** A high-risk segment where most of the composite is local. */
export const LocalDominated: Story = {
  args: {
    compositeRisk: 0.78,
    localContribution: 0.72,
    propagationUplift: 0.06,
    algorithm: { name: "pagerank-diffusion", version: "0.1.0" },
  },
};

/** A segment whose risk is mostly network-amplified (the propagator earns its keep). */
export const UpliftDominated: Story = {
  args: {
    compositeRisk: 0.62,
    localContribution: 0.18,
    propagationUplift: 0.44,
    algorithm: { name: "pagerank-diffusion", version: "0.1.0" },
  },
};

/** Pre-Phase-4 sentinel row: no propagator ran, no algorithm label. */
export const PreScoreSentinel: Story = {
  args: {
    compositeRisk: 0.35,
    localContribution: 0.35,
    propagationUplift: 0.0,
    algorithm: null,
  },
};

/** Boundary case: very low risk should still render visible bars. */
export const VeryLowRisk: Story = {
  args: {
    compositeRisk: 0.05,
    localContribution: 0.04,
    propagationUplift: 0.01,
    algorithm: { name: "pagerank-diffusion", version: "0.1.0" },
  },
};
