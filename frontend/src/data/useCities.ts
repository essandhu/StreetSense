/**
 * TanStack Query hook for `GET /api/cities` — Phase 4b Task 4.4.
 *
 * Server state — the list of every configured city. Backs the
 * CitySelector dropdown (Task 4.4) and provides the `bbox` /
 * `timezone` fields the MapLibre `fitBounds` (Task 4.6) and scrubber
 * local-noon reset (Task 4.7) read on a city switch.
 *
 * Cached aggressively (`staleTime: Infinity`): the cities table is
 * curated and changes only on a config + redeploy cycle. The API
 * also ships an ETag, so even if the cache evicts, a revalidation
 * round-trip is a cheap 304.
 *
 * The query is intentionally NOT keyed on the active city — the city
 * list is global, not per-city. Switching the active city does not
 * invalidate this cache (a switching user still sees the same list
 * of options in the dropdown).
 */
import { useQuery } from "@tanstack/react-query";

import type { City, CityListResponse } from "../domain";

import { fetchJson } from "./api";

export const citiesQueryKey = () => ["cities"] as const;

export async function fetchCities(): Promise<CityListResponse> {
  return fetchJson<CityListResponse>("/api/cities");
}

export function useCities() {
  return useQuery({
    queryKey: citiesQueryKey(),
    queryFn: fetchCities,
    staleTime: Number.POSITIVE_INFINITY,
  });
}

/**
 * Convenience selector that finds one city in a CityListResponse by
 * slug, or returns `null`. Exported so the MapLibre and scrubber
 * effects (Tasks 4.6, 4.7) don't each re-implement the lookup.
 */
export const findCityBySlug = (
  response: CityListResponse | undefined,
  slug: string,
): City | null => response?.cities.find((c) => c.slug === slug) ?? null;
