import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ConfidenceIndicator } from "../../domain";
import { ConfidenceDial } from "./ConfidenceDial";

const _conf = (value: number, limiter: ConfidenceIndicator["limiter"]): ConfidenceIndicator => ({
  value,
  limiter,
});

describe("<ConfidenceDial>", () => {
  it("renders the value as a percentage", () => {
    render(<ConfidenceDial confidence={_conf(0.42, "freshness")} />);
    expect(screen.getByTestId("confidence-value-text")).toHaveTextContent("42%");
  });

  it("labels each limiter correctly", () => {
    for (const [limiter, label] of [
      ["freshness", "Freshness"],
      ["coverage", "Coverage"],
      ["model", "Model"],
    ] as const) {
      const { unmount } = render(<ConfidenceDial confidence={_conf(0.5, limiter)} />);
      expect(screen.getByTestId("confidence-limiter-label")).toHaveTextContent(label);
      unmount();
    }
  });

  it("clamps the rendered percentage to [0, 100]", () => {
    const { rerender } = render(<ConfidenceDial confidence={_conf(-0.5, "model")} />);
    expect(screen.getByTestId("confidence-value-text")).toHaveTextContent("0%");
    rerender(<ConfidenceDial confidence={_conf(1.5, "freshness")} />);
    expect(screen.getByTestId("confidence-value-text")).toHaveTextContent("100%");
  });
});
