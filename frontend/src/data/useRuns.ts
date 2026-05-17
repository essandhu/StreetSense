/**
 * TanStack Query hook for `GET /api/cities/{slug}/runs`.
 *
 * Phase 4b Task 3.3 moved the runs list under the city namespace;
 * Phase 4b Task 4.3 reads the slug from the activeCity slice so a
 * city switch invalidates the cached run list — each city has its
 * own scoring history, so reusing the previous city's runs in the
 * picker would be a correctness bug, not just a UX nit.
 *
 * Server state — never put run lists in Redux.
 *
 * staleTime is short rather than `Infinity` because the cron-driven
 * weekly scoring (Phase 5) adds new runs over time. Five minutes
 * balances "see new runs quickly" against "don't burn a request on
 * every panel mount."
 */
import { useQuery } from "@tanstack/react-query";

import type { RunListResponse } from "../domain";
import { cityScopedPath, fetchJson, useActiveCitySlug } from "./api";

export const runsQueryKey = (citySlug: string) => ["runs", citySlug] as const;

export async function fetchRuns(citySlug: string): Promise<RunListResponse> {
  return fetchJson<RunListResponse>(cityScopedPath(citySlug, "/runs"));
}

const _STALE_TIME_MS = 5 * 60 * 1000;

export function useRuns() {
  const citySlug = useActiveCitySlug();
  return useQuery({
    queryKey: runsQueryKey(citySlug),
    queryFn: () => fetchRuns(citySlug),
    staleTime: _STALE_TIME_MS,
  });
}
