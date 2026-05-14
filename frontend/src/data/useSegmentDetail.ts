/**
 * TanStack Query hook for `GET /segments/{id}?t=...`.
 *
 * Caches per `(segmentId, hour)` pair so two scrubber positions
 * inside the same hour share a single API call. Pre-signed imagery
 * URLs in the response have a short TTL — the staleTime is tuned to
 * stay inside that window.
 */
import { useQuery } from "@tanstack/react-query";

import type { SegmentDetail, SegmentId } from "../domain";
import { fetchJson } from "./api";

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

export function segmentDetailQueryKey(segmentId: SegmentId, atHour: string) {
  return ["segmentDetail", segmentId, atHour] as const;
}

export async function fetchSegmentDetail(
  segmentId: SegmentId,
  t?: Date | null,
): Promise<SegmentDetail> {
  let path = `/segments/${segmentId}`;
  if (t) {
    path += `?t=${encodeURIComponent(t.toISOString())}`;
  }
  return fetchJson<SegmentDetail>(path);
}

/** TTL aligned with the API's pre-signed imagery URL window (5 minutes). */
const STALE_TIME_MS = 4 * 60 * 1000;

export function useSegmentDetail(segmentId: SegmentId | null, t: Date | null) {
  const atHour = t ? _snapHourUtc(t) : "latest";
  return useQuery({
    queryKey: segmentId
      ? segmentDetailQueryKey(segmentId, atHour)
      : ["segmentDetail-disabled"],
    queryFn: () => fetchSegmentDetail(segmentId as SegmentId, t),
    enabled: segmentId !== null,
    staleTime: STALE_TIME_MS,
  });
}
