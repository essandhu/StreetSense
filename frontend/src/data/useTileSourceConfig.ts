/**
 * Hot-swappable tile source URL.
 *
 * Wrapped as a TanStack Query hook even though Phase 1 has a single static
 * source: when Phase 5 adds multi-city support, this hook switches its
 * inputs without the Map component changing.
 */

import { useQuery } from "@tanstack/react-query";

import { useActiveCitySlug, tileBaseUrl } from "./api";

const DEFAULT_LAYER = "public.road_segments_tile";

export type TileSourceConfig = {
  url: string;
  layer: string;
};

const buildTileUrl = (layer: string, citySlug: string): string =>
  // Phase 4b (migration 0019): the tile function requires `city_slug`.
  // pg_tileserv passes named query params straight through to the
  // function, so the slug rides as a query string alongside any
  // future ?t=... / ?run_a=... args added by sibling hooks.
  `${tileBaseUrl()}/tiles/${layer}/{z}/{x}/{y}.pbf?city_slug=${encodeURIComponent(citySlug)}`;

export const useTileSourceConfig = () => {
  // Phase 4b Task 4.3: slug comes from the activeCity slice via a
  // selector hook. A `setActiveCity` dispatch re-renders this
  // component, the new slug enters the query key, TanStack Query
  // invalidates the previous entry, deck.gl swaps the source URL.
  const citySlug = useActiveCitySlug();
  return useQuery<TileSourceConfig>({
    queryKey: ["tile-source", DEFAULT_LAYER, citySlug],
    queryFn: () =>
      Promise.resolve({
        url: buildTileUrl(DEFAULT_LAYER, citySlug),
        layer: DEFAULT_LAYER,
      }),
    staleTime: Infinity,
  });
};
