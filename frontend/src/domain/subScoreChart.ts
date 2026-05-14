/**
 * Pure chart-geometry for the segment-detail radial sub-score chart.
 *
 * No React, no D3 ceremony beyond `d3-shape.arc` (stateless). This
 * module produces `ArcDatum`s that the `<SubScoreChart>` component
 * renders as `<path d={...}>` elements.
 *
 * Layout: four equal sectors (one per sub-score) with constant angular
 * extent (`π/2`). The visual encoding is the *fill* (driven by
 * `value`) and a stub treatment (driven by `isStub`).
 */
import { arc as d3arc } from "d3-shape";

import type { SubScore, SubScores } from "./index";

/** Deterministic display order for the four sub-scores. */
export const SUB_SCORE_DISPLAY_ORDER = [
  "glare_exposure",
  "lane_marking_quality",
  "junction_complexity",
  "historical_correlation",
] as const;

export type SubScoreKey = (typeof SUB_SCORE_DISPLAY_ORDER)[number];

export interface ArcDatum {
  /** Sub-score key (e.g., "glare_exposure"). */
  name: SubScoreKey;
  /** Sector start angle in radians (clockwise from 12 o'clock). */
  startAngle: number;
  /** Sector end angle in radians. */
  endAngle: number;
  innerRadius: number;
  outerRadius: number;
  /** Risk value in [0, 1]. */
  value: number;
  /** Stub treatment if true. */
  isStub: boolean;
  /** SVG `d` attribute, ready to set on `<path>`. */
  path: string;
}

export interface ChartLayout {
  arcs: ArcDatum[];
  /** Square viewBox side length. */
  size: number;
  /** Center of the SVG viewBox (= size / 2). */
  center: number;
}

const DEFAULT_SIZE = 240;
const DEFAULT_INNER_RADIUS = 40;
const DEFAULT_OUTER_RADIUS = 96;

/** Build one arc datum at the given index of `total`. */
export function arcDatum(
  name: SubScoreKey,
  subScore: SubScore,
  index: number,
  total: number,
  options: { innerRadius?: number; outerRadius?: number } = {},
): ArcDatum {
  const innerRadius = options.innerRadius ?? DEFAULT_INNER_RADIUS;
  const outerRadius = options.outerRadius ?? DEFAULT_OUTER_RADIUS;
  const sliceAngle = (2 * Math.PI) / total;
  const startAngle = index * sliceAngle;
  const endAngle = (index + 1) * sliceAngle;

  // d3-shape's arc generator is stateless when called with explicit
  // accessors; passing the datum inline keeps this purely functional.
  const generator = d3arc<ArcDatum>()
    .innerRadius(innerRadius)
    .outerRadius(outerRadius)
    .startAngle((d) => d.startAngle)
    .endAngle((d) => d.endAngle);

  const datum: Omit<ArcDatum, "path"> = {
    name,
    startAngle,
    endAngle,
    innerRadius,
    outerRadius,
    value: subScore.value,
    isStub: subScore.is_stub,
  };
  const path = generator(datum as ArcDatum) ?? "";
  return { ...datum, path };
}

/**
 * Build the full chart layout from a `SubScores` object.
 *
 * The arc array is always length 4, in `SUB_SCORE_DISPLAY_ORDER`.
 * Total angular extent is exactly `2π`.
 */
export function chartLayout(
  subScores: SubScores,
  options: { size?: number; innerRadius?: number; outerRadius?: number } = {},
): ChartLayout {
  const size = options.size ?? DEFAULT_SIZE;
  const radii: { innerRadius?: number; outerRadius?: number } = {};
  if (options.innerRadius !== undefined) radii.innerRadius = options.innerRadius;
  if (options.outerRadius !== undefined) radii.outerRadius = options.outerRadius;
  const arcs = SUB_SCORE_DISPLAY_ORDER.map((name, i) =>
    arcDatum(name, subScores[name], i, SUB_SCORE_DISPLAY_ORDER.length, radii),
  );
  return { arcs, size, center: size / 2 };
}
