/**
 * ConfidenceDial — small SVG ring filling from 0 to 1, labeling the limiter.
 *
 * Pure presentational. Props in, JSX out. The limiter is read directly
 * from the API's ConfidenceIndicator; the UI surface for "why is
 * confidence low" lives here.
 */
import type { ConfidenceIndicator, ConfidenceLimiter } from "../../domain";

export interface ConfidenceDialProps {
  confidence: ConfidenceIndicator;
  size?: number;
  /** Override the limiter human label (otherwise defaults below). */
  limiterLabels?: Partial<Record<ConfidenceLimiter, string>>;
  className?: string;
}

const DEFAULT_LIMITER_LABELS: Record<ConfidenceLimiter, string> = {
  freshness: "Freshness",
  coverage: "Coverage",
  model: "Model",
};

const STROKE = 10;

export function ConfidenceDial({
  confidence,
  size = 96,
  limiterLabels,
  className,
}: ConfidenceDialProps) {
  const radius = (size - STROKE) / 2;
  const circumference = 2 * Math.PI * radius;
  const v = Math.max(0, Math.min(1, confidence.value));
  const dash = circumference * v;
  const label = (limiterLabels ?? DEFAULT_LIMITER_LABELS)[confidence.limiter];

  return (
    <div className={className}>
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        role="img"
        aria-label={`Confidence ${(v * 100).toFixed(0)} percent, limited by ${label}`}
      >
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="#2a2a2a"
          strokeWidth={STROKE}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="#2da14a"
          strokeWidth={STROKE}
          strokeDasharray={`${dash} ${circumference - dash}`}
          strokeLinecap="round"
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
        <text
          x="50%"
          y="50%"
          dominantBaseline="central"
          textAnchor="middle"
          fontSize="20"
          fill="#e0e0e0"
          fontWeight="600"
          data-testid="confidence-value-text"
        >
          {(v * 100).toFixed(0)}%
        </text>
      </svg>
      <div
        data-testid="confidence-limiter-label"
        style={{ textAlign: "center", fontSize: 12, color: "#9a9a9a", marginTop: 4 }}
      >
        {label}
      </div>
    </div>
  );
}
