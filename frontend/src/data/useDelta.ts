/**
 * TanStack Query hook for `GET /runs/{run_a}/delta/{run_b}` (Task 3.2).
 *
 * Server state — never put delta payloads in Redux (`state/delta.ts`
 * holds only mode + selection).
 *
 * Disabled when:
 *   - Either run is null (the user hasn't finished picking in the
 *     RunPicker yet — Task 3.3).
 *   - Both runs are the same UUID (the API would 422; cheaper to
 *     short-circuit than to round-trip the rejection).
 *
 * Page size is pinned to the API's maximum (1000) so a single
 * round-trip carries enough rows for both the LargestChangesList
 * (virtualizes >200) and the D3 histogram (needs the full distribution).
 * Cambridge sits comfortably under the cap; if a multi-city future
 * pushes past it, this hook grows a follow-page accumulator without
 * any signature change. The hour-of-day parameter is intentionally
 * omitted in Phase 5 — the API defaults to noon UTC, which matches
 * the weekly-cron cadence and lines up with how the delta tile
 * function picks rows. A `t` argument can be added when the delta
 * view starts honoring the scrubber.
 */
import { useQuery } from "@tanstack/react-query";

import type { DeltaResponse, RunId } from "../domain";
import { fetchJson } from "./api";

const _PAGE_SIZE = 1000;

export function deltaQueryKey(runA: RunId, runB: RunId) {
  return ["delta", runA, runB] as const;
}

export async function fetchDelta(runA: RunId, runB: RunId): Promise<DeltaResponse> {
  const path = `/runs/${runA}/delta/${runB}?page=1&page_size=${_PAGE_SIZE}`;
  return fetchJson<DeltaResponse>(path);
}

/**
 * Delta payloads are keyed on two immutable historical scoring runs —
 * the answer never changes once both rows exist. `Infinity` is honest
 * here (no need to revalidate); the entry still leaves cache on
 * unmount once TanStack Query's gcTime elapses.
 */
const _STALE_TIME_MS = Number.POSITIVE_INFINITY;

export function useDelta(runA: RunId | null, runB: RunId | null) {
  const enabled = runA !== null && runB !== null && runA !== runB;
  return useQuery({
    queryKey:
      enabled && runA !== null && runB !== null ? deltaQueryKey(runA, runB) : ["delta-disabled"],
    queryFn: () => fetchDelta(runA as RunId, runB as RunId),
    enabled,
    staleTime: _STALE_TIME_MS,
  });
}
