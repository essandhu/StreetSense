/**
 * CompositeBreakdown — Phase 4 segment-detail decomposition.
 *
 * Splits ``composite_risk`` into its two explainable components from
 * spec AC-7:
 *
 *   composite_risk = local_contribution + propagation_uplift
 *
 * Renders both as proportional bars on a shared axis so the user
 * sees at a glance what fraction of the headline risk comes from
 * the segment's own sub-scores versus the network propagator's
 * uplift. The propagation_uplift bar is labelled with the algorithm
 * name + semver (e.g., "pagerank-diffusion 0.1.0") so the user knows
 * which propagator produced the contribution — closes the
 * explainability invariant from CLAUDE.md (never collapse to a
 * single opaque number).
 *
 * The shared-axis upper bound is the larger of the segment's own
 * composite or a small floor — keeps the bars readable for both
 * low-risk and high-risk segments without rescaling between rows.
 *
 * No D3 here — the bars are vanilla flex CSS. The radial chart
 * upstairs is where D3 lives.
 */

import type { components } from "../../domain/generated/api";

import "./CompositeBreakdown.css";

type PropagationAlgorithm = components["schemas"]["PropagationAlgorithmInfo"];

export type CompositeBreakdownProps = {
  /** Headline composite risk (local + uplift). Surfaced in the chart label. */
  compositeRisk: number;
  /** Per-segment local aggregate before any network propagation. */
  localContribution: number;
  /** Network-propagated uplift contribution. */
  propagationUplift: number;
  /**
   * Propagator that produced ``propagation_uplift``. ``null`` for
   * pre-Phase-4 sentinel rows where no propagator ran; the bar then
   * hides itself and only the local contribution is shown.
   */
  algorithm: PropagationAlgorithm | null | undefined;
};

// Minimum axis bound — keeps short bars visible when composite is
// very small. Picked so 0.05 still gets ~5% of the bar width.
const AXIS_FLOOR = 0.1;

/** Format a number for the on-bar label. Three decimals matches the panel's read-fluency. */
function _fmt(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return value.toFixed(3);
}

/** Percent-of-axis width for a value. Clamped to [0, 100]. */
function _widthPct(value: number, axisMax: number): number {
  if (axisMax <= 0) return 0;
  return Math.max(0, Math.min(100, (value / axisMax) * 100));
}

export function CompositeBreakdown({
  compositeRisk,
  localContribution,
  propagationUplift,
  algorithm,
}: CompositeBreakdownProps) {
  const hasAlgorithm = algorithm != null && algorithm.name !== "";
  const axisMax = Math.max(AXIS_FLOOR, compositeRisk);
  const localWidth = _widthPct(localContribution, axisMax);
  const upliftWidth = _widthPct(propagationUplift, axisMax);

  return (
    <section
      className="composite-breakdown"
      data-testid="composite-breakdown"
      aria-label="Composite risk breakdown"
    >
      <header className="composite-breakdown__header">
        <h3 className="composite-breakdown__title">Composite breakdown</h3>
        <span
          className="composite-breakdown__total"
          data-testid="composite-breakdown-total"
        >
          {_fmt(compositeRisk)}
        </span>
      </header>

      <div className="composite-breakdown__row">
        <div className="composite-breakdown__row-header">
          <span className="composite-breakdown__row-label">Local</span>
          <span
            className="composite-breakdown__row-value"
            data-testid="composite-breakdown-local-value"
          >
            {_fmt(localContribution)}
          </span>
        </div>
        <div className="composite-breakdown__bar" role="presentation">
          <div
            className="composite-breakdown__bar-fill composite-breakdown__bar-fill--local"
            data-testid="composite-breakdown-local-bar"
            style={{ width: `${localWidth}%` }}
          />
        </div>
      </div>

      <div className="composite-breakdown__row">
        <div className="composite-breakdown__row-header">
          <span className="composite-breakdown__row-label">
            Network uplift
            {hasAlgorithm && (
              <span
                className="composite-breakdown__algorithm"
                data-testid="composite-breakdown-algorithm"
              >
                {" "}
                — {algorithm.name} {algorithm.version}
              </span>
            )}
          </span>
          <span
            className="composite-breakdown__row-value"
            data-testid="composite-breakdown-uplift-value"
          >
            {_fmt(propagationUplift)}
          </span>
        </div>
        <div className="composite-breakdown__bar" role="presentation">
          <div
            className="composite-breakdown__bar-fill composite-breakdown__bar-fill--uplift"
            data-testid="composite-breakdown-uplift-bar"
            style={{ width: `${upliftWidth}%` }}
          />
        </div>
        {!hasAlgorithm && (
          <p
            className="composite-breakdown__no-algorithm"
            data-testid="composite-breakdown-no-algorithm"
          >
            Propagator did not run for this row (pre-Phase 4 sentinel).
          </p>
        )}
      </div>
    </section>
  );
}
