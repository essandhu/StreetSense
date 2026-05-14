import { describe, expect, it } from "vitest";

import type { SubScore, SubScores } from "./index";
import {
  SUB_SCORE_DISPLAY_ORDER,
  type ArcDatum,
  arcDatum,
  chartLayout,
} from "./subScoreChart";

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

describe("chartLayout", () => {
  it("produces exactly four arcs in display order", () => {
    const layout = chartLayout(_scores());
    expect(layout.arcs).toHaveLength(SUB_SCORE_DISPLAY_ORDER.length);
    expect(layout.arcs.map((a) => a.name)).toEqual([...SUB_SCORE_DISPLAY_ORDER]);
  });

  it("covers exactly 2π in total", () => {
    const layout = chartLayout(_scores());
    const span = layout.arcs.reduce(
      (acc: number, a: ArcDatum) => acc + (a.endAngle - a.startAngle),
      0,
    );
    expect(span).toBeCloseTo(2 * Math.PI, 10);
  });

  it("propagates is_stub from input so stub vs real arcs are distinguishable", () => {
    const layout = chartLayout(_scores());
    expect(layout.arcs.find((a) => a.name === "glare_exposure")?.isStub).toBe(false);
    expect(layout.arcs.find((a) => a.name === "junction_complexity")?.isStub).toBe(true);
  });

  it("emits a non-empty SVG path for every arc", () => {
    const layout = chartLayout(_scores());
    for (const a of layout.arcs) {
      expect(a.path.length).toBeGreaterThan(0);
      expect(a.path.startsWith("M")).toBe(true);
    }
  });
});

describe("arcDatum", () => {
  it("respects index/total spacing", () => {
    const datum = arcDatum("glare_exposure", _sub(0.5, false), 1, 4);
    expect(datum.startAngle).toBeCloseTo(Math.PI / 2);
    expect(datum.endAngle).toBeCloseTo(Math.PI);
  });
});
