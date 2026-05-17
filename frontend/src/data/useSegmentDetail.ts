/**
 * TanStack Query hook for the city-scoped segment-detail endpoint.
 *
 * Phase 4b Task 3.3 moved the segment endpoint to
 * `GET /api/cities/{slug}/segments/{id}?t=...`. Phase 4b Task 4.3
 * threads the active city slug through this hook so the cache and
 * URL re-key on a city switch.
 *
 * Caches per `(citySlug, segmentId, hour)` tuple so two scrubber
 * positions inside the same UTC hour for the same city share a
 * single API call. Switching cities invalidates the cache for
 * the previous slug naturally — TanStack Query treats the
 * different key as a different query.
 *
 * Pre-signed imagery URLs in the response have a short TTL — the
 * staleTime is tuned to stay inside that window.
 */
import { useQuery } from "@tanstack/react-query";

import type { SegmentDetail, SegmentId } from "../domain";
import { cityScopedPath, fetchJson, useActiveCitySlug } from "./api";

/** Snap a `Date` to the start of its UTC hour. */
function _snapHourUtc(t: Date): string {
  const d = new Date(
    Date.UTC(
      t.getUTCFullYear(),
      t.getUTCMonth(),
      t.getUTCDate(),
      t.getUTCHours(),
      0,
      0,
      0,
    ),
  );
  return d.toISOString();
}

export function segmentDetailQueryKey(citySlug: string, segmentId: SegmentId, atHour: string) {
  return ["segmentDetail", citySlug, segmentId, atHour] as const;
}

export async function fetchSegmentDetail(
  citySlug: string,
  segmentId: SegmentId,
  t?: Date | null,
): Promise<SegmentDetail> {
  let path = cityScopedPath(citySlug, `/segments/${segmentId}`);
  if (t) {
    path += `?t=${encodeURIComponent(t.toISOString())}`;
  }
  return fetchJson<SegmentDetail>(path);
}

/** TTL aligned with the API's pre-signed imagery URL window (5 minutes). */
const STALE_TIME_MS = 4 * 60 * 1000;

export function useSegmentDetail(segmentId: SegmentId | null, t: Date | null) {
  const citySlug = useActiveCitySlug();
  const atHour = t ? _snapHourUtc(t) : "latest";
  return useQuery({
    queryKey: segmentId
      ? segmentDetailQueryKey(citySlug, segmentId, atHour)
      : ["segmentDetail-disabled", citySlug],
    queryFn: () => fetchSegmentDetail(citySlug, segmentId as SegmentId, t),
    enabled: segmentId !== null,
    staleTime: STALE_TIME_MS,
  });
}
