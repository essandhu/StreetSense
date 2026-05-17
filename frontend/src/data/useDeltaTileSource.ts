/**
 * Delta tile source — Phase 5, Task 3.7.
 *
 * Builds the URL for the delta vector-tile function added in
 * migration 0016 (`public.road_segments_tile_delta`). The URL
 * carries `run_a`, `run_b`, and an optional `t` (hour-of-day) so the
 * tile JOIN picks the matching pair of `segment_scores` rows.
 *
 * Wrapped as a TanStack Query hook for symmetry with
 * `useTileSourceConfig` / `useGlareTileSource` — the cache here
 * gives us a stable URL handle even though the "fetch" is just
 * string construction. The real work (fetching tile bytes) happens
 * inside deck.gl's `MVTLayer`.
 *
 * Returns `null` data when the run pair is incomplete or equal —
 * the component using this hook then declines to mount the overlay.
 * Mirrors `useDelta`'s short-circuit so a half-picked state doesn't
 * make a 4xx tile request the moment the dropdowns are touched.
 */

import { useQuery } from "@tanstack/react-query";

import type { RunId } from "../domain";
import { DEFAULT_CITY_SLUG } from "../state/activeCity";

import { useActiveCitySlug, tileBaseUrl } from "./api";

const LAYER = "public.road_segments_tile_delta";

export type DeltaTileSource = {
  url: string;
  layer: string;
  runA: RunId;
  runB: RunId;
};

/**
 * Build the delta tile URL given two run IDs. Exported so the URL
 * math stays testable without TanStack Query setup.
 *
 * Phase 4b (migration 0019): the delta tile function takes a required
 * ``city_slug``; ``deltaTileUrl`` accepts it as a third parameter,
 * defaulted to ``DEFAULT_CITY_SLUG`` so pure-function callers don't
 * need a Redux store. React-tree callers should pass the value from
 * :func:`useActiveCitySlug` explicitly.
 */
export const deltaTileUrl = (
  runA: RunId,
  runB: RunId,
  citySlug: string = DEFAULT_CITY_SLUG,
): string => {
  const base = `${tileBaseUrl()}/tiles/${LAYER}/{z}/{x}/{y}.pbf`;
  const params = new URLSearchParams();
  params.set("run_a", String(runA));
  params.set("run_b", String(runB));
  params.set("city_slug", citySlug);
  return `${base}?${params.toString()}`;
};

export const useDeltaTileSource = (runA: RunId | null, runB: RunId | null) => {
  // Phase 4b Task 4.3: slug from the activeCity slice.
  const citySlug = useActiveCitySlug();
  const enabled = runA !== null && runB !== null && runA !== runB;
  return useQuery<DeltaTileSource | null>({
    queryKey: ["delta-tile-source", LAYER, citySlug, String(runA ?? ""), String(runB ?? "")],
    queryFn: () => {
      if (!enabled || runA === null || runB === null) return Promise.resolve(null);
      return Promise.resolve({
        url: deltaTileUrl(runA, runB, citySlug),
        layer: LAYER,
        runA,
        runB,
      });
    },
    staleTime: Infinity,
    enabled,
  });
};
