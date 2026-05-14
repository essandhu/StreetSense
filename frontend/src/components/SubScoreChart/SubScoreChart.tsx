/**
 * D3 radial sub-score chart.
 *
 * React owns the `<svg>`; D3 owns the math (via `domain/subScoreChart.ts`).
 * One `useMemo` to compute the layout from props; pure render. No
 * imperative D3 selection — the React-owns-DOM pattern from CLAUDE.md.
 */
import { useMemo } from "react";

import type { SubScores } from "../../domain";
import { type ChartLayout, chartLayout } from "../../domain/subScoreChart";

export interface SubScoreChartProps {
  subScores: SubScores;
  size?: number;
  /** Optional className to style from a parent (e.g., the panel). */
  className?: string;
}

/** Map a risk value in [0, 1] to a fill color. Higher value = redder. */
function fillFor(value: number, isStub: boolean): string {
  if (isStub) return "url(#stub-hatch)";
  // 3-stop interpolation: cool green → amber → red.
  // Avoid pulling d3-interpolate just for this — manual linear lerp.
  const v = Math.max(0, Math.min(1, value));
  if (v < 0.5) {
    const t = v / 0.5;
    // green (#2da14a) → amber (#e8a13a)
    return lerpHex("#2da14a", "#e8a13a", t);
  }
  const t = (v - 0.5) / 0.5;
  return lerpHex("#e8a13a", "#c83b3b", t);
}

function lerpHex(a: string, b: string, t: number): string {
  const ar = parseInt(a.slice(1, 3), 16);
  const ag = parseInt(a.slice(3, 5), 16);
  const ab = parseInt(a.slice(5, 7), 16);
  const br = parseInt(b.slice(1, 3), 16);
  const bg = parseInt(b.slice(3, 5), 16);
  const bb = parseInt(b.slice(5, 7), 16);
  const r = Math.round(ar + (br - ar) * t);
  const g = Math.round(ag + (bg - ag) * t);
  const bl = Math.round(ab + (bb - ab) * t);
  return `#${r.toString(16).padStart(2, "0")}${g.toString(16).padStart(2, "0")}${bl
    .toString(16)
    .padStart(2, "0")}`;
}

export function SubScoreChart({ subScores, size = 240, className }: SubScoreChartProps) {
  const layout: ChartLayout = useMemo(
    () => chartLayout(subScores, { size }),
    [subScores, size],
  );
  return (
    <svg
      width={layout.size}
      height={layout.size}
      viewBox={`0 0 ${layout.size} ${layout.size}`}
      role="img"
      aria-label="Sub-score radial chart"
      className={className}
    >
      <defs>
        {/* Hatched fill for stub arcs — visually distinct from real values. */}
        <pattern
          id="stub-hatch"
          width="6"
          height="6"
          patternUnits="userSpaceOnUse"
          patternTransform="rotate(45)"
        >
          <rect width="6" height="6" fill="#3a3a3a" />
          <line x1="0" y1="0" x2="0" y2="6" stroke="#5a5a5a" strokeWidth="2" />
        </pattern>
      </defs>
      <g transform={`translate(${layout.center}, ${layout.center})`}>
        {layout.arcs.map((arc) => (
          <path
            key={arc.name}
            d={arc.path}
            fill={fillFor(arc.value, arc.isStub)}
            stroke="#1c1c1c"
            strokeWidth={1.5}
            data-arc-name={arc.name}
            data-stub={arc.isStub ? "true" : "false"}
            data-value={arc.value}
          />
        ))}
      </g>
    </svg>
  );
}
