/**
 * Layer color accessors — Phase 4.
 *
 * Pure, framework-free color-bucketing for the deck.gl MVTLayer.
 * Split out of ``LayerOverlay.tsx`` so the component file only
 * exports components (Vite Fast Refresh constraint) and so the
 * bucketing math is testable in isolation.
 */

import type { LayerId } from "../../state/activeLayer";

// Five-step cool → hot palette as RGBA tuples — same ordering as the
// Phase 1 stub palette in Map.tsx; the deck.gl shader needs numbers
// instead of CSS strings.
const PALETTE: ReadonlyArray<[number, number, number, number]> = [
  [44, 123, 182, 220], //   0 – coolest
  [171, 217, 233, 220],
  [255, 255, 191, 220],
  [253, 174, 97, 220],
  [215, 25, 28, 220], //    4 – hottest
];

export const STUB_COLOR: [number, number, number, number] = [80, 80, 90, 80]; // neutral dim

type LayerConfig = {
  /** Numeric field on the MVT feature in [0, upperBound]. */
  valueKey: string;
  /** Optional boolean stub flag; when true, render the stub color. */
  stubKey?: string;
  /** Upper bound used for normalization. Composite can exceed 1.0; sub-scores cannot. */
  upperBound: number;
};

export const LAYER_CONFIG: Readonly<Record<LayerId, LayerConfig>> = {
  composite: {
    valueKey: "composite_risk",
    // composite_risk = local_contribution + propagation_uplift; the
    // upper bound depends on composite weights and the propagator's
    // normalize flag. Clamp to 1.0 for the 5-step palette; values
    // above 1.0 saturate at the hottest bucket.
    upperBound: 1.0,
  },
  glare: {
    valueKey: "glare_score",
    stubKey: "is_stub_glare",
    upperBound: 1.0,
  },
  lane: {
    valueKey: "lane_marking_quality",
    stubKey: "is_stub_lane_marking",
    upperBound: 1.0,
  },
  junction: {
    // The Phase 4 tile function does not currently project the
    // junction-complexity score onto the tile output (the migration
    // 0015 RETURNS TABLE shape stops at lane_marking_quality plus
    // composite/uplift). When the tile gains a ``junction_complexity``
    // column, this reads it. Until then, junction renders as the
    // stub color via the missing-value fallback path.
    valueKey: "junction_complexity",
    stubKey: "is_stub_junction_complexity",
    upperBound: 1.0,
  },
  historical: {
    // Same caveat as junction — the historical-correlation field is
    // not currently projected onto the tile. This entry is wired so
    // the toggle reflects the four real Phase 4 sub-scores; the data
    // arrives when the tile shape is extended.
    valueKey: "historical_correlation",
    stubKey: "is_stub_historical",
    upperBound: 1.0,
  },
};

/**
 * Build a deck.gl color accessor for the active layer. Exported so
 * the bucketing math (NaN, negative, stub, missing) can be tested
 * in isolation.
 */
export const colorAccessorForLayer = (
  layer: LayerId,
): ((feature: { properties?: Record<string, unknown> }) => [
  number,
  number,
  number,
  number,
]) => {
  const config = LAYER_CONFIG[layer];
  return (feature) => {
    const props = feature.properties ?? {};
    if (config.stubKey && props[config.stubKey] === true) {
      return STUB_COLOR;
    }
    const raw = props[config.valueKey];
    if (typeof raw !== "number" || !Number.isFinite(raw)) {
      return STUB_COLOR;
    }
    // Clamp to [0, upperBound], then map to a 5-bucket index.
    const clamped = Math.min(config.upperBound, Math.max(0.0, raw));
    const normalized = clamped / config.upperBound;
    const idx = Math.min(PALETTE.length - 1, Math.floor(normalized * PALETTE.length));
    return PALETTE[idx] ?? STUB_COLOR;
  };
};
