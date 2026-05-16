/**
 * Domain types — pure types, no React, no fetching.
 *
 * Re-exports the OpenAPI-generated schemas from `generated/` and adds the
 * branded ID types we use throughout the codebase. Branded types prevent
 * accidental crossover (passing a `RunId` where a `SegmentId` is expected
 * is a TypeScript error).
 */

import type { components } from "./generated/api";

// --- Branded ID types ----------------------------------------------------
declare const __brand: unique symbol;
type Brand<T, B> = T & { readonly [__brand]: B };

export type SegmentId = Brand<string, "SegmentId">;
export type RunId = Brand<string, "RunId">;

/** Cast a UUID string to a SegmentId. The caller asserts it's a valid UUID. */
export const SegmentId = (uuid: string): SegmentId => uuid as SegmentId;
export const RunId = (uuid: string): RunId => uuid as RunId;

// --- Re-exports of API schemas ------------------------------------------
export type SegmentDetail = components["schemas"]["SegmentDetail"];
export type SubScores = components["schemas"]["SubScores"];
export type SubScore = components["schemas"]["SubScore"];
export type ConfidenceIndicator = components["schemas"]["ConfidenceIndicator"];
export type ImageryReference = components["schemas"]["ImageryReference"];
export type FreshnessReport = components["schemas"]["FreshnessReport"];
export type FreshnessEntry = components["schemas"]["FreshnessEntry"];

// Phase 5 — delta endpoint shapes. Field order mirrors `SubScores` so a
// frontend iterating fields sees the same order in both single-run and
// delta responses.
export type ScoringRunMetadata = components["schemas"]["ScoringRunMetadata"];
export type SubScoreDeltas = components["schemas"]["SubScoreDeltas"];
export type SegmentDelta = components["schemas"]["SegmentDelta"];
export type DeltaResponse = components["schemas"]["DeltaResponse"];
export type RunListResponse = components["schemas"]["RunListResponse"];

/** Limiter values surfaced by the API's ConfidenceIndicator. */
export type ConfidenceLimiter = ConfidenceIndicator["limiter"];

// --- Pure helpers --------------------------------------------------------

/** The 5-step palette buckets used by the GPU-side color expression. */
export const RISK_BUCKETS = [0, 1, 2, 3, 4] as const;
export type RiskBucket = (typeof RISK_BUCKETS)[number];

/**
 * Map a composite risk in [0, 1) to a 5-step palette bucket. Mirrors the
 * SQL VIEW's `risk_stub_bucket` arithmetic for any code that needs to
 * cross-reference the two without round-tripping through PostgreSQL.
 */
export const riskBucketFromComposite = (composite: number): RiskBucket => {
  const bounded = Math.max(0, Math.min(0.999_999, composite));
  return Math.floor(bounded * RISK_BUCKETS.length) as RiskBucket;
};
