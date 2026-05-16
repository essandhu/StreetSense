/**
 * Delta layer accessors — Phase 5, Task 3.4.
 *
 * Pure, framework-free helpers the deck.gl ``MVTLayer`` uses to paint
 * the delta tile (`road_segments_tile_delta` from migration 0016):
 *
 *   * :func:`deltaColorAccessor` — diverging color ramp on
 *     ``composite_delta``: dark red (risk up a lot) → light red →
 *     neutral grey → light green → dark green (risk down a lot).
 *   * :func:`deltaWidthAccessor` — magnitude proxy on
 *     ``|composite_delta|``: floored so a near-zero delta is still
 *     visible and capped so an outlier doesn't paint a 50px line.
 *
 * Mirrors ``layerColor.ts``'s pattern: framework-free, testable in
 * isolation, palette as RGBA tuples (deck.gl shader needs numbers,
 * not CSS strings).
 *
 * Visual choices (the exact 5-step palette, the magnitude thresholds,
 * the dead-zone width) are tuneable — the unit tests assert
 * *structural* invariants (positive vs. negative are distinct,
 * magnitude is monotonic, neutral is neither red nor green) rather
 * than literal hex values, so the palette can shift without test
 * churn.
 *
 * Convention reminder (from ``api/schemas.py``): positive
 * ``composite_delta`` means risk went **up** from ``run_a`` to
 * ``run_b``; negative means risk went **down**. Red-for-up matches
 * the existing single-run palette where red is "worse".
 */

export type Rgba = [number, number, number, number];

/**
 * Diverging 5-step palette. Anchored at neutral grey so a zero-ish
 * delta blends into the unselected basemap colors rather than
 * shouting.
 */
const NEG_HEAVY: Rgba = [27, 120, 55, 220]; // deep green — risk down a lot
const NEG_LIGHT: Rgba = [127, 191, 123, 220]; // light green — risk down
const NEUTRAL: Rgba = [180, 180, 188, 180]; //  neutral grey — no real change
const POS_LIGHT: Rgba = [231, 138, 138, 220]; // light red — risk up
const POS_HEAVY: Rgba = [165, 0, 38, 220]; //   deep red — risk up a lot

export const DELTA_COLOR_NEUTRAL: Rgba = NEUTRAL;

/**
 * Magnitude thresholds. ``DEAD_ZONE`` is the symmetric band around
 * zero treated as "no real change" — covers stochastic ±epsilon
 * scoring noise. ``HEAVY`` is the magnitude at which the heavy
 * (saturated) palette ends apply.
 */
const DEAD_ZONE = 0.01;
const HEAVY = 0.15;

export const DELTA_WIDTH_MIN = 1.5; // matches deck.gl ``lineWidthMinPixels``
export const DELTA_WIDTH_MAX = 8.0;

/** Magnitude at which width saturates to ``DELTA_WIDTH_MAX``. */
const WIDTH_SATURATE_AT = 0.3;

function _readComposite(feature: { properties?: Record<string, unknown> }): number | null {
  const raw = feature.properties?.composite_delta;
  if (typeof raw !== "number" || !Number.isFinite(raw)) return null;
  return raw;
}

export function deltaColorAccessor(feature: { properties?: Record<string, unknown> }): Rgba {
  const delta = _readComposite(feature);
  if (delta === null) return NEUTRAL;
  const mag = Math.abs(delta);
  if (mag < DEAD_ZONE) return NEUTRAL;
  if (delta > 0) {
    return mag >= HEAVY ? POS_HEAVY : POS_LIGHT;
  }
  return mag >= HEAVY ? NEG_HEAVY : NEG_LIGHT;
}

export function deltaWidthAccessor(feature: { properties?: Record<string, unknown> }): number {
  const delta = _readComposite(feature);
  if (delta === null) return DELTA_WIDTH_MIN;
  const mag = Math.abs(delta);
  if (mag >= WIDTH_SATURATE_AT) return DELTA_WIDTH_MAX;
  // Linear remap [0, WIDTH_SATURATE_AT] → [DELTA_WIDTH_MIN, DELTA_WIDTH_MAX].
  const span = DELTA_WIDTH_MAX - DELTA_WIDTH_MIN;
  return DELTA_WIDTH_MIN + (mag / WIDTH_SATURATE_AT) * span;
}
