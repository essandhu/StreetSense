/**
 * Delta layer accessor unit tests — Phase 5, Task 3.4.
 *
 * The delta layer paints per-segment ``composite_delta`` with:
 *
 *   * Color  — diverging ramp: dark red (risk up a lot)
 *              → light red → neutral grey → light green → dark green
 *              (risk down a lot).
 *   * Width  — magnitude proxy: bigger ``|composite_delta|`` → thicker
 *              line. Floored so near-zero deltas are still visible and
 *              capped so an outlier doesn't make a 50px line.
 *
 * Tests here lock in the bucketing math the deck.gl shader actually
 * hits: stubs, missing values, NaN, the five color buckets, and the
 * width floor/cap. Visual choices (exact hex values) are intentionally
 * NOT asserted — those are tuneable; the property tests assert
 * structural invariants the UI relies on (e.g., positive deltas
 * differ from negative deltas, magnitude is monotonic, neutral is
 * neither red nor green).
 */
import { describe, expect, it } from "vitest";

import {
  DELTA_COLOR_NEUTRAL,
  DELTA_WIDTH_MAX,
  DELTA_WIDTH_MIN,
  deltaColorAccessor,
  deltaWidthAccessor,
} from "./deltaLayer";

const _f = (composite_delta: unknown) => ({
  properties: { composite_delta } as Record<string, unknown>,
});

describe("deltaColorAccessor — neutral fallbacks", () => {
  it("missing composite_delta paints the neutral color", () => {
    expect(deltaColorAccessor({ properties: {} })).toEqual(DELTA_COLOR_NEUTRAL);
  });

  it("non-finite composite_delta paints the neutral color", () => {
    expect(deltaColorAccessor(_f(NaN))).toEqual(DELTA_COLOR_NEUTRAL);
    expect(deltaColorAccessor(_f(Infinity))).toEqual(DELTA_COLOR_NEUTRAL);
    expect(deltaColorAccessor(_f("0.05"))).toEqual(DELTA_COLOR_NEUTRAL);
  });

  it("composite_delta of exactly zero paints the neutral color", () => {
    expect(deltaColorAccessor(_f(0))).toEqual(DELTA_COLOR_NEUTRAL);
  });

  it("composite_delta within the dead-zone paints the neutral color", () => {
    // The dead-zone is a small symmetric band around zero so trivial
    // noise (±epsilon scoring fluctuations) doesn't paint the map.
    const tiny = 0.001;
    expect(deltaColorAccessor(_f(tiny))).toEqual(DELTA_COLOR_NEUTRAL);
    expect(deltaColorAccessor(_f(-tiny))).toEqual(DELTA_COLOR_NEUTRAL);
  });
});

describe("deltaColorAccessor — diverging ramp", () => {
  it("positive composite_delta paints a different color than negative", () => {
    const up = deltaColorAccessor(_f(0.1));
    const down = deltaColorAccessor(_f(-0.1));
    expect(up).not.toEqual(down);
    expect(up).not.toEqual(DELTA_COLOR_NEUTRAL);
    expect(down).not.toEqual(DELTA_COLOR_NEUTRAL);
  });

  it("color for very-positive differs from color for slightly-positive", () => {
    const slight = deltaColorAccessor(_f(0.06));
    const heavy = deltaColorAccessor(_f(0.5));
    expect(slight).not.toEqual(heavy);
  });

  it("color for very-negative differs from color for slightly-negative", () => {
    const slight = deltaColorAccessor(_f(-0.06));
    const heavy = deltaColorAccessor(_f(-0.5));
    expect(slight).not.toEqual(heavy);
  });

  it("positive deltas are red-dominant (red channel ≥ green channel)", () => {
    const color = deltaColorAccessor(_f(0.3));
    expect(color[0]).toBeGreaterThanOrEqual(color[1]);
  });

  it("negative deltas are green-dominant (green channel ≥ red channel)", () => {
    const color = deltaColorAccessor(_f(-0.3));
    expect(color[1]).toBeGreaterThanOrEqual(color[0]);
  });

  it("super-saturating large positive delta returns a valid opaque color", () => {
    const color = deltaColorAccessor(_f(5.0));
    expect(color).toHaveLength(4);
    expect(color[3]).toBeGreaterThan(0);
  });

  it("super-saturating large negative delta returns a valid opaque color", () => {
    const color = deltaColorAccessor(_f(-5.0));
    expect(color).toHaveLength(4);
    expect(color[3]).toBeGreaterThan(0);
  });

  it("sign reversal (±x) produces distinct colors at every magnitude", () => {
    for (const x of [0.06, 0.15, 0.3, 1.0]) {
      expect(deltaColorAccessor(_f(x))).not.toEqual(deltaColorAccessor(_f(-x)));
    }
  });
});

describe("deltaWidthAccessor — floor and cap", () => {
  it("missing composite_delta returns the minimum width", () => {
    expect(deltaWidthAccessor({ properties: {} })).toBe(DELTA_WIDTH_MIN);
  });

  it("non-finite composite_delta returns the minimum width", () => {
    expect(deltaWidthAccessor(_f(NaN))).toBe(DELTA_WIDTH_MIN);
    expect(deltaWidthAccessor(_f("0.5"))).toBe(DELTA_WIDTH_MIN);
  });

  it("zero composite_delta returns the minimum width (still visible)", () => {
    expect(deltaWidthAccessor(_f(0))).toBe(DELTA_WIDTH_MIN);
  });

  it("very large composite_delta caps at the maximum width", () => {
    expect(deltaWidthAccessor(_f(100))).toBe(DELTA_WIDTH_MAX);
    expect(deltaWidthAccessor(_f(-100))).toBe(DELTA_WIDTH_MAX);
  });

  it("width is symmetric in sign — |delta| drives width, not delta itself", () => {
    for (const x of [0.05, 0.1, 0.3, 0.5]) {
      expect(deltaWidthAccessor(_f(x))).toBe(deltaWidthAccessor(_f(-x)));
    }
  });

  it("width is monotonic non-decreasing in |composite_delta|", () => {
    const widths = [0, 0.05, 0.1, 0.2, 0.5, 1.0].map((d) => deltaWidthAccessor(_f(d)));
    for (let i = 1; i < widths.length; i++) {
      expect(widths[i]).toBeGreaterThanOrEqual(widths[i - 1]!);
    }
  });

  it("width is bounded by [DELTA_WIDTH_MIN, DELTA_WIDTH_MAX] for arbitrary inputs", () => {
    for (const x of [-1000, -1, -0.1, 0, 0.1, 1, 1000]) {
      const w = deltaWidthAccessor(_f(x));
      expect(w).toBeGreaterThanOrEqual(DELTA_WIDTH_MIN);
      expect(w).toBeLessThanOrEqual(DELTA_WIDTH_MAX);
    }
  });
});
