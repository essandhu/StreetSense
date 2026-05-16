/**
 * TanStack Query hook for `GET /runs` (Task 3.3 backend prep — frontend half).
 *
 * Server state — backs the RunPicker dropdowns (Task 3.3). The list is
 * the discovery path for run UUIDs: the delta endpoint requires the
 * caller to already know two runs, so without this hook the picker
 * would have nothing to enumerate.
 *
 * staleTime is short rather than `Infinity` because the cron-driven
 * weekly scoring (Phase 4 of this track) adds new runs over time —
 * unlike a (runA, runB) delta payload which is immutable once both
 * rows exist, the list grows. Five minutes balances "see new runs
 * quickly after they land" against "don't burn a request on every
 * panel mount."
 */
import { useQuery } from "@tanstack/react-query";

import type { RunListResponse } from "../domain";
import { fetchJson } from "./api";

export const runsQueryKey = () => ["runs"] as const;

export async function fetchRuns(): Promise<RunListResponse> {
  return fetchJson<RunListResponse>("/runs");
}

const _STALE_TIME_MS = 5 * 60 * 1000;

export function useRuns() {
  return useQuery({
    queryKey: runsQueryKey(),
    queryFn: fetchRuns,
    staleTime: _STALE_TIME_MS,
  });
}
