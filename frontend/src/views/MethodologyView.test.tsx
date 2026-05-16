/**
 * Smoke test for MethodologyView (Task 5.1).
 *
 * Static page — no data dependencies. The test confirms the page
 * renders and carries the load-bearing references the architecture
 * doc relies on: the four sub-scores by name, the propagation
 * strategy + version, the six reproducibility fields, and the
 * delta-view reading guide.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MethodologyView } from "./MethodologyView";

describe("MethodologyView", () => {
  it("renders the top-level methodology article", () => {
    render(<MethodologyView />);
    expect(screen.getByTestId("methodology-view")).toBeDefined();
  });

  it("names all four sub-scores", () => {
    render(<MethodologyView />);
    expect(screen.getByText(/lane marking quality/i)).toBeDefined();
    expect(screen.getByText(/glare exposure/i)).toBeDefined();
    expect(screen.getByText(/junction complexity/i)).toBeDefined();
    expect(screen.getByText(/historical correlation/i)).toBeDefined();
  });

  it("references the Phase 4 propagation strategy and ADR", () => {
    render(<MethodologyView />);
    const body = screen.getByTestId("methodology-view").textContent ?? "";
    expect(body).toMatch(/pagerank-diffusion-0\.1\.0/);
    expect(body).toMatch(/ADR.?0006/i);
  });

  it("documents all six reproducibility fields", () => {
    render(<MethodologyView />);
    const body = screen.getByTestId("methodology-view").textContent ?? "";
    for (const field of [
      "scoring_run_id",
      "scoring_run_timestamp",
      "perception_model_version",
      "osm_snapshot_date",
      "imagery_capture_window",
      "propagation_algorithm_version",
    ]) {
      expect(body).toContain(field);
    }
  });

  it("explains the delta view color encoding", () => {
    render(<MethodologyView />);
    const body = screen.getByTestId("methodology-view").textContent ?? "";
    // The three direction colors are part of the user-facing read.
    expect(body).toMatch(/red/i);
    expect(body).toMatch(/green/i);
    expect(body).toMatch(/grey|gray/i);
  });
});
