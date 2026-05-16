/**
 * Tests for the pure delta-histogram math (Task 3.6).
 *
 * Covers binning correctness, highlight assignment, edge inclusion
 * (last bin is inclusive on its right edge), and layout invariants
 * the downstream component relies on (bar width uniform, zero tick
 * in the middle of a symmetric domain, max count drives bar height).
 */
import { describe, expect, it } from "vitest";

import { histogramLayout, type HistogramBin } from "./deltaHistogram";

const _deltas = (...values: number[]) => values.map((composite_delta) => ({ composite_delta }));

const _idxOfHighlight = (bins: HistogramBin[]) => bins.findIndex((b) => b.isHighlighted);

describe("histogramLayout — bin structure", () => {
  it("produces a fixed number of bins (default 20) regardless of input size", () => {
    expect(histogramLayout([]).bins.length).toBe(20);
    expect(histogramLayout(_deltas(0)).bins.length).toBe(20);
    expect(histogramLayout(_deltas(0.1, 0.2, 0.3, 0.4, 0.5)).bins.length).toBe(20);
  });

  it("respects an explicit binCount option", () => {
    expect(histogramLayout([], { binCount: 10 }).bins.length).toBe(10);
    expect(histogramLayout([], { binCount: 50 }).bins.length).toBe(50);
  });

  it("bins are uniform width over the domain", () => {
    const layout = histogramLayout([], { binCount: 20, domainMin: -1, domainMax: 1 });
    const widths = layout.bins.map((b) => b.x1 - b.x0);
    const first = widths[0]!;
    for (const w of widths) {
      expect(w).toBeCloseTo(first, 10);
    }
    expect(first).toBeCloseTo(0.1, 10);
  });

  it("first bin starts at domainMin, last bin ends at domainMax", () => {
    const layout = histogramLayout([], { binCount: 20, domainMin: -1, domainMax: 1 });
    expect(layout.bins[0]!.x0).toBeCloseTo(-1, 10);
    expect(layout.bins[layout.bins.length - 1]!.x1).toBeCloseTo(1, 10);
  });
});

describe("histogramLayout — counts", () => {
  it("empty input → all bins have count 0", () => {
    const layout = histogramLayout([]);
    expect(layout.bins.every((b) => b.count === 0)).toBe(true);
    expect(layout.maxCount).toBe(0);
  });

  it("counts each value into exactly one bin", () => {
    const layout = histogramLayout(_deltas(-0.5, -0.5, 0, 0.5, 0.5, 0.5), {
      binCount: 20,
      domainMin: -1,
      domainMax: 1,
    });
    const total = layout.bins.reduce((s, b) => s + b.count, 0);
    expect(total).toBe(6);
  });

  it("maxCount reports the tallest bin's count", () => {
    // Values chosen to all sit comfortably inside one bin, avoiding
    // float precision on threshold boundaries (e.g., 0.1 vs.
    // threshold 0.10000000000000009).
    const layout = histogramLayout(_deltas(0.12, 0.13, 0.14, 0.15, 0.16, -0.45), {
      binCount: 20,
    });
    expect(layout.maxCount).toBe(5);
  });

  it("values exactly at the upper domain edge land in the last bin", () => {
    const layout = histogramLayout(_deltas(1), {
      binCount: 10,
      domainMin: -1,
      domainMax: 1,
    });
    const lastBin = layout.bins[layout.bins.length - 1]!;
    expect(lastBin.count).toBe(1);
  });
});

describe("histogramLayout — highlight", () => {
  it("no highlightValue → no bin is marked highlighted", () => {
    const layout = histogramLayout(_deltas(0.1, 0.2, 0.3));
    expect(layout.bins.every((b) => !b.isHighlighted)).toBe(true);
  });

  it("null highlightValue → no bin is marked highlighted", () => {
    const layout = histogramLayout(_deltas(0.1), { highlightValue: null });
    expect(layout.bins.every((b) => !b.isHighlighted)).toBe(true);
  });

  it("highlights exactly one bin — the one containing highlightValue", () => {
    const layout = histogramLayout(_deltas(0), {
      binCount: 20,
      domainMin: -1,
      domainMax: 1,
      highlightValue: 0.35,
    });
    const highlighted = layout.bins.filter((b) => b.isHighlighted);
    expect(highlighted.length).toBe(1);
    const [bin] = highlighted;
    expect(bin!.x0).toBeLessThanOrEqual(0.35);
    expect(bin!.x1).toBeGreaterThanOrEqual(0.35);
  });

  it("a highlightValue exactly on the upper domain edge highlights the last bin", () => {
    const layout = histogramLayout([], {
      binCount: 10,
      domainMin: -1,
      domainMax: 1,
      highlightValue: 1,
    });
    expect(_idxOfHighlight(layout.bins)).toBe(layout.bins.length - 1);
  });

  it("a highlightValue outside the domain is not highlighted", () => {
    const layout = histogramLayout([], {
      domainMin: -1,
      domainMax: 1,
      highlightValue: 2,
    });
    expect(layout.bins.every((b) => !b.isHighlighted)).toBe(true);
  });
});

describe("histogramLayout — render scaffolding", () => {
  it("zeroX sits at the middle of a symmetric domain", () => {
    const layout = histogramLayout([], { width: 400, domainMin: -1, domainMax: 1 });
    expect(layout.zeroX).toBeCloseTo(200, 6);
  });

  it("bar width is uniform across all bins", () => {
    const layout = histogramLayout([], { width: 320, binCount: 20 });
    expect(layout.barWidth).toBeGreaterThan(0);
    expect(layout.barWidth).toBeLessThanOrEqual(320 / 20);
  });

  it("yOfCount(0) is at the chart bottom, yOfCount(maxCount) is at the top", () => {
    const layout = histogramLayout(_deltas(0, 0, 0, 0.5), {
      binCount: 20,
      height: 100,
    });
    expect(layout.yOfCount(0)).toBeCloseTo(100, 6);
    expect(layout.yOfCount(layout.maxCount)).toBeCloseTo(0, 6);
  });

  it("heightOfCount is monotonic in count", () => {
    const layout = histogramLayout(_deltas(0, 0, 0.5), { binCount: 20 });
    expect(layout.heightOfCount(0)).toBeLessThan(layout.heightOfCount(1));
    expect(layout.heightOfCount(1)).toBeLessThan(layout.heightOfCount(2));
  });
});
