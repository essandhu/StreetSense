/**
 * LargestChangesList — Phase 5, Task 3.5.
 *
 * Reads `useDelta()` for the currently selected (runA, runB) pair,
 * sorts by ``|composite_delta|`` descending, and renders the top N
 * rows. Clicking a row dispatches ``openSegment(segmentId)`` so the
 * existing Phase 3 :class:`SegmentDetailPanel` carries the
 * downstream rendering (radial chart, confidence dial, imagery
 * thumbnails) — the list is a *selector*, not a parallel detail
 * surface.
 *
 * Centering the map on the clicked segment is intentionally NOT
 * implemented here. The path requires segment-centroid data the
 * current ``SegmentDetail`` response doesn't ship; capturing as a
 * polish follow-up rather than blocking the headline delta-view
 * shipment. See ``conductor/tracks/phase-5-delta-deployment/index.md``
 * §"Discovered during implementation".
 *
 * Virtualization: capped to top 100 rows at the source. Plan §3.5
 * suggested ``react-virtual`` >200; the cap means we never approach
 * that boundary, so no new dependency lands.
 */
import { useMemo } from "react";
import { useSelector } from "react-redux";

import { useDelta } from "../../data/useDelta";
import { SegmentId } from "../../domain";
import { useAppDispatch } from "../../state/hooks";
import { openSegment } from "../../state/selectedSegment";
import type { RootState } from "../../state/store";

import "./LargestChangesList.css";

const _selectDelta = (state: RootState) => state.delta;
const TOP_N = 100;

function _formatSignedDelta(value: number): string {
  // Three decimals — matches the noise floor below which the delta layer
  // paints neutral (DEAD_ZONE = 0.01). The sign prefix makes
  // "risk went up" vs. "risk went down" legible at a glance.
  const fixed = value.toFixed(3);
  return value >= 0 && fixed !== "-0.000" ? `+${fixed}` : fixed;
}

function _shortId(uuid: string): string {
  return uuid.slice(0, 8);
}

export const LargestChangesList = () => {
  const dispatch = useAppDispatch();
  const { runA, runB } = useSelector(_selectDelta);
  const query = useDelta(runA, runB);

  const sorted = useMemo(() => {
    const rows = query.data?.deltas ?? [];
    return [...rows]
      .sort((a, b) => Math.abs(b.composite_delta) - Math.abs(a.composite_delta))
      .slice(0, TOP_N);
  }, [query.data]);

  if (!runA || !runB) {
    return (
      <div data-testid="largest-changes-list" className="largest-changes-list">
        <p className="placeholder">Pick two runs to see the largest segment changes.</p>
      </div>
    );
  }

  if (query.isSuccess && sorted.length === 0) {
    return (
      <div data-testid="largest-changes-list" className="largest-changes-list">
        <p className="placeholder">No segment changes between these runs.</p>
      </div>
    );
  }

  return (
    <div data-testid="largest-changes-list" className="largest-changes-list">
      <ol className="rows">
        {sorted.map((row, idx) => {
          const direction =
            row.composite_delta > 0 ? "up" : row.composite_delta < 0 ? "down" : "flat";
          return (
            <li key={String(row.segment_id)}>
              <button
                type="button"
                className={`row row-${direction}`}
                data-segment-id={String(row.segment_id)}
                onClick={() => dispatch(openSegment(SegmentId(String(row.segment_id))))}
              >
                <span className="rank">{idx + 1}</span>
                <span className="seg-id" title={String(row.segment_id)}>
                  {_shortId(String(row.segment_id))}
                </span>
                <span className={`delta delta-${direction}`}>
                  {_formatSignedDelta(row.composite_delta)}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
};
