/**
 * Pure math for the delta histogram (Task 3.6).
 *
 * "React owns the DOM, D3 owns the math" — this module produces
 * `HistogramLayout` records that the `<DeltaHistogram>` component
 * renders as plain `<rect>` elements. No React, no SVG strings,
 * no D3 selections here.
 *
 * The histogram answers "across all segments in this delta, where
 * does the change distribution sit?" — a long left tail of
 * decreases, a long right tail of increases, and a typical bell
 * around the dead-zone. The selected segment's bar is highlighted in
 * context (per architecture §3.5), driven by the
 * `highlightValue` parameter.
 */
import { bin as d3bin } from "d3-array";
import { scaleLinear } from "d3-scale";

import type { SegmentDelta } from "./index";

export interface HistogramBin {
  /** Left edge of the bin (inclusive). */
  x0: number;
  /** Right edge of the bin (exclusive on all but the last bin). */
  x1: number;
  /** Number of segments whose composite_delta falls in this bin. */
  count: number;
  /** True when `highlightValue` (e.g., the selected segment's delta) is in this bin. */
  isHighlighted: boolean;
}

export interface HistogramLayout {
  bins: HistogramBin[];
  /** Width of the histogram viewBox. */
  width: number;
  /** Height of the histogram viewBox. */
  height: number;
  /** Tightest count across the bins — used to scale bar heights. */
  maxCount: number;
  /** SVG `x` of each bin in viewBox units (matches `bins` order). */
  xOfBin: (bin: HistogramBin) => number;
  /** SVG `width` of each bar in viewBox units (uniform across bins). */
  barWidth: number;
  /** SVG `y` of the top of a bar of `count`. */
  yOfCount: (count: number) => number;
  /** SVG `height` of a bar of `count`. */
  heightOfCount: (count: number) => number;
  /** SVG `x` of the zero-delta tick. */
  zeroX: number;
}

export interface HistogramOptions {
  width?: number;
  height?: number;
  binCount?: number;
  /** Inclusive domain start for binning. Default: -1. */
  domainMin?: number;
  /** Inclusive domain end for binning. Default: 1. */
  domainMax?: number;
  /**
   * The selected segment's `composite_delta` (or null). The bin
   * containing this value is marked `isHighlighted: true` — the
   * component renders that bar in an accent color.
   */
  highlightValue?: number | null;
}

const DEFAULT_WIDTH = 320;
const DEFAULT_HEIGHT = 96;
const DEFAULT_BIN_COUNT = 20;
const DEFAULT_DOMAIN_MIN = -1;
const DEFAULT_DOMAIN_MAX = 1;

/**
 * Build the histogram layout from a list of deltas.
 *
 * Bin edges are uniform over `[domainMin, domainMax]` so the zero
 * tick lines up cleanly when domain is symmetric — important for
 * the "left tail = decreases, right tail = increases" reading.
 *
 * Empty input produces zero-count bins (still drawn as a flat
 * baseline) rather than no bins — the chart should still occupy
 * its slot during empty / loading states.
 */
export function histogramLayout(
  deltas: ReadonlyArray<Pick<SegmentDelta, "composite_delta">>,
  options: HistogramOptions = {}
): HistogramLayout {
  const width = options.width ?? DEFAULT_WIDTH;
  const height = options.height ?? DEFAULT_HEIGHT;
  const binCount = options.binCount ?? DEFAULT_BIN_COUNT;
  const domainMin = options.domainMin ?? DEFAULT_DOMAIN_MIN;
  const domainMax = options.domainMax ?? DEFAULT_DOMAIN_MAX;
  const highlightValue = options.highlightValue ?? null;

  // Pre-generate uniform thresholds so empty input still produces
  // a full bin sequence (d3.bin() yields an empty array on empty
  // input otherwise).
  const step = (domainMax - domainMin) / binCount;
  const thresholds = Array.from({ length: binCount - 1 }, (_, i) => domainMin + (i + 1) * step);

  const binner = d3bin<{ composite_delta: number }, number>()
    .value((d) => d.composite_delta)
    .domain([domainMin, domainMax])
    .thresholds(thresholds);

  const raw = binner([...deltas]);

  const bins: HistogramBin[] = Array.from({ length: binCount }, (_, i) => {
    const x0 = domainMin + i * step;
    const x1 = domainMin + (i + 1) * step;
    const match = raw[i];
    const count = match?.length ?? 0;
    const isHighlighted =
      highlightValue !== null &&
      Number.isFinite(highlightValue) &&
      _containsValue({ x0, x1, isLast: i === binCount - 1 }, highlightValue);
    return { x0, x1, count, isHighlighted };
  });

  const maxCount = bins.reduce((m, b) => Math.max(m, b.count), 0);

  const xScale = scaleLinear().domain([domainMin, domainMax]).range([0, width]);
  const yScale = scaleLinear()
    .domain([0, Math.max(1, maxCount)])
    .range([height, 0]);

  const barWidth = (width / binCount) * 0.9;
  return {
    bins,
    width,
    height,
    maxCount,
    xOfBin: (bin) => xScale(bin.x0) + (xScale(bin.x1) - xScale(bin.x0) - barWidth) / 2,
    barWidth,
    yOfCount: (count) => yScale(count),
    heightOfCount: (count) => height - yScale(count),
    zeroX: xScale(0),
  };
}

function _containsValue(
  range: { x0: number; x1: number; isLast: boolean },
  value: number
): boolean {
  if (range.isLast) {
    return value >= range.x0 && value <= range.x1;
  }
  return value >= range.x0 && value < range.x1;
}
