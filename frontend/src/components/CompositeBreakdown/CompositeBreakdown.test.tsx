/**
 * CompositeBreakdown unit tests — Phase 4 segment-detail decomposition.
 *
 * Asserts the AC-7/AC-8 contracts:
 * - The component surfaces composite_risk, local_contribution, and
 *   propagation_uplift as separate readable values.
 * - The propagator's name + semver is labelled when present.
 * - Pre-Phase-4 sentinel rows (algorithm == null) hide the label and
 *   surface a fallback note.
 * - Bar widths are proportional to the values on a shared axis.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CompositeBreakdown } from "./CompositeBreakdown";

describe("<CompositeBreakdown>", () => {
  it("renders local, uplift, and composite values to three decimals", () => {
    render(
      <CompositeBreakdown
        compositeRisk={0.65}
        localContribution={0.5}
        propagationUplift={0.15}
        algorithm={{ name: "pagerank-diffusion", version: "0.1.0" }}
      />,
    );
    expect(screen.getByTestId("composite-breakdown-total")).toHaveTextContent("0.650");
    expect(screen.getByTestId("composite-breakdown-local-value")).toHaveTextContent("0.500");
    expect(screen.getByTestId("composite-breakdown-uplift-value")).toHaveTextContent("0.150");
  });

  it("labels the uplift bar with the algorithm name + version", () => {
    render(
      <CompositeBreakdown
        compositeRisk={0.42}
        localContribution={0.30}
        propagationUplift={0.12}
        algorithm={{ name: "pagerank-diffusion", version: "0.1.0" }}
      />,
    );
    const algo = screen.getByTestId("composite-breakdown-algorithm");
    expect(algo.textContent).toMatch(/pagerank-diffusion\s+0\.1\.0/);
  });

  it("hides the algorithm label and shows the fallback note when algorithm is null", () => {
    render(
      <CompositeBreakdown
        compositeRisk={0.42}
        localContribution={0.42}
        propagationUplift={0.0}
        algorithm={null}
      />,
    );
    expect(screen.queryByTestId("composite-breakdown-algorithm")).toBeNull();
    expect(screen.getByTestId("composite-breakdown-no-algorithm")).toBeInTheDocument();
  });

  it("renders proportional bar widths on a shared axis", () => {
    render(
      <CompositeBreakdown
        compositeRisk={1.0}
        localContribution={0.75}
        propagationUplift={0.25}
        algorithm={{ name: "pagerank-diffusion", version: "0.1.0" }}
      />,
    );
    const local = screen.getByTestId("composite-breakdown-local-bar") as HTMLElement;
    const uplift = screen.getByTestId("composite-breakdown-uplift-bar") as HTMLElement;
    // axisMax = max(0.1, 1.0) = 1.0 → 75% and 25%.
    expect(local.style.width).toBe("75%");
    expect(uplift.style.width).toBe("25%");
  });

  it("clamps tiny composites to the axis floor so short bars stay visible", () => {
    render(
      <CompositeBreakdown
        compositeRisk={0.05}
        localContribution={0.05}
        propagationUplift={0.0}
        algorithm={{ name: "pagerank-diffusion", version: "0.1.0" }}
      />,
    );
    const local = screen.getByTestId("composite-breakdown-local-bar") as HTMLElement;
    // axisMax = max(0.1, 0.05) = 0.1 → 50%.
    expect(local.style.width).toBe("50%");
  });

  it("renders non-finite inputs as a dash rather than NaN", () => {
    render(
      <CompositeBreakdown
        compositeRisk={NaN}
        localContribution={NaN}
        propagationUplift={NaN}
        algorithm={null}
      />,
    );
    expect(screen.getByTestId("composite-breakdown-total")).toHaveTextContent("—");
    expect(screen.getByTestId("composite-breakdown-local-value")).toHaveTextContent("—");
  });
});
