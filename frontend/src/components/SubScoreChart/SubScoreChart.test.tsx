import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

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

describe("<SubScoreChart>", () => {
  it("renders exactly four <path> elements", () => {
    const { container } = render(<SubScoreChart subScores={_scores()} />);
    const paths = container.querySelectorAll("path");
    expect(paths).toHaveLength(4);
  });

  it("marks stub arcs with data-stub='true' and real arcs with 'false'", () => {
    const { container } = render(<SubScoreChart subScores={_scores()} />);
    const stubArcs = container.querySelectorAll('[data-stub="true"]');
    const realArcs = container.querySelectorAll('[data-stub="false"]');
    expect(stubArcs).toHaveLength(2);
    expect(realArcs).toHaveLength(2);
  });

  it("reflects the underlying value via data-value", () => {
    const { container } = render(
      <SubScoreChart
        subScores={_scores({
          glare_exposure: _sub(0.85, false),
        })}
      />,
    );
    const glare = container.querySelector('[data-arc-name="glare_exposure"]') as
      | HTMLElement
      | null;
    expect(glare).not.toBeNull();
    expect(glare?.getAttribute("data-value")).toBe("0.85");
  });

  it("real-score arcs use a colored fill; stub arcs use the hatch pattern", () => {
    const { container } = render(<SubScoreChart subScores={_scores()} />);
    const stubFill = container
      .querySelector('[data-stub="true"]')
      ?.getAttribute("fill");
    expect(stubFill).toBe("url(#stub-hatch)");
    const realFill = container
      .querySelector('[data-stub="false"]')
      ?.getAttribute("fill");
    expect(realFill).toMatch(/^#[0-9a-f]{6}$/i);
  });
});
