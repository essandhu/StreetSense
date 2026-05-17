/**
 * TanStack Query hook for `GET /api/cities/{slug}/runs/{run_a}/delta/{run_b}`.
 *
 * Phase 4b Task 3.3 moved the delta endpoint under the city
 * namespace; Phase 4b Task 4.3 reads the slug from the activeCity
 * slice and includes it in the cache key.
 *
 * Server state — never put delta payloads in Redux (`state/delta.ts`
 * holds only mode + selection).
 *
 * Disabled when:
 *   - Either run is null (the user hasn't finished picking in the
 *     RunPicker yet).
 *   - Both runs are the same UUID (the API would 422; cheaper to
 *     short-circuit than to round-trip the rejection).
 *
 * Page size is pinned to the API's maximum (1000) so a single
 * round-trip carries enough rows for both the LargestChangesList
 * (virtualizes >200) and the D3 histogram (needs the full distribution).
 * Cambridge sits comfortably under the cap. The hour-of-day parameter
 * is intentionally omitted in Phase 5 — the API defaults to noon UTC,
 * which matches the weekly-cron cadence and lines up with how the
 * delta tile function picks rows. A `t` argument can be added when the
 * delta view starts honoring the scrubber.
 */
import { useQuery } from "@tanstack/react-query";

import type { DeltaResponse, RunId } from "../domain";
import { cityScopedPath, fetchJson, useActiveCitySlug } from "./api";

const _PAGE_SIZE = 1000;

export function deltaQueryKey(citySlug: string, runA: RunId, runB: RunId) {
  return ["delta", citySlug, runA, runB] as const;
}

export async function fetchDelta(
  citySlug: string,
  runA: RunId,
  runB: RunId,
): Promise<DeltaResponse> {
  const path = cityScopedPath(
    citySlug,
    `/runs/${runA}/delta/${runB}?page=1&page_size=${_PAGE_SIZE}`,
  );
  return fetchJson<DeltaResponse>(path);
}

/**
 * Delta payloads are keyed on two immutable historical scoring runs
 * within one city — the answer never changes once both rows exist.
 * `Infinity` is honest here (no need to revalidate); the entry still
 * leaves cache on unmount once TanStack Query's gcTime elapses.
 */
const _STALE_TIME_MS = Number.POSITIVE_INFINITY;

export function useDelta(runA: RunId | null, runB: RunId | null) {
  const citySlug = useActiveCitySlug();
  const enabled = runA !== null && runB !== null && runA !== runB;
  return useQuery({
    queryKey:
      enabled && runA !== null && runB !== null
        ? deltaQueryKey(citySlug, runA, runB)
        : ["delta-disabled", citySlug],
    queryFn: () => fetchDelta(citySlug, runA as RunId, runB as RunId),
    enabled,
    staleTime: _STALE_TIME_MS,
  });
}
