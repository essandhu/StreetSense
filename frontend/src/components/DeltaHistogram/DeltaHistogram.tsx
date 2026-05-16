/**
 * DeltaHistogram — Phase 5, Task 3.6.
 *
 * Renders the score-change distribution across the (runA, runB)
 * pair. Pure renderer over `histogramLayout`
 * (`domain/deltaHistogram.ts`) — "React owns the DOM, D3 owns the
 * math" per the architecture (§3.5).
 *
 * The currently-selected segment's bar is highlighted in context so
 * the user can see "where does this one segment sit in the
 * city-wide change distribution?" — that's the architecture's
 * articulated value of the histogram, not a generic "look at the
 * shape" affordance.
 */
import { useMemo } from "react";
import { useSelector } from "react-redux";

import { useDelta } from "../../data/useDelta";
import { histogramLayout } from "../../domain/deltaHistogram";
import type { RootState } from "../../state/store";

import "./DeltaHistogram.css";

const _selectDelta = (s: RootState) => s.delta;
const _selectSelectedSegment = (s: RootState) => s.selectedSegment.segmentId;

const WIDTH = 320;
const HEIGHT = 96;

export const DeltaHistogram = () => {
  const { runA, runB } = useSelector(_selectDelta);
  const selectedSegmentId = useSelector(_selectSelectedSegment);
  const query = useDelta(runA, runB);

  const layout = useMemo(() => {
    const rows = query.data?.deltas ?? [];
    let highlightValue: number | null = null;
    if (selectedSegmentId !== null) {
      const hit = rows.find((r) => String(r.segment_id) === String(selectedSegmentId));
      if (hit) highlightValue = hit.composite_delta;
    }
    return histogramLayout(rows, { width: WIDTH, height: HEIGHT, highlightValue });
  }, [query.data, selectedSegmentId]);

  if (!runA || !runB) {
    return (
      <div className="delta-histogram-empty">
        <p>Pick two runs to see the change distribution.</p>
      </div>
    );
  }

  return (
    <svg
      data-testid="delta-histogram"
      className="delta-histogram"
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      width={WIDTH}
      height={HEIGHT}
      role="img"
      aria-label="Distribution of composite-risk deltas across all segments"
    >
      {/* Zero tick — symmetric domain, so this anchors "left = decreases / right = increases". */}
      <line className="zero-tick" x1={layout.zeroX} x2={layout.zeroX} y1={0} y2={HEIGHT} />
      {layout.bins.map((bin, i) => (
        <rect
          key={i}
          className={bin.isHighlighted ? "bar bar-highlighted" : "bar"}
          x={layout.xOfBin(bin)}
          y={layout.yOfCount(bin.count)}
          width={layout.barWidth}
          height={layout.heightOfCount(bin.count)}
        />
      ))}
    </svg>
  );
};
