/**
 * Hot-swappable tile source URL.
 *
 * Wrapped as a TanStack Query hook even though Phase 1 has a single static
 * source: when Phase 5 adds multi-city support, this hook switches its
 * inputs without the Map component changing.
 */

import { useQuery } from "@tanstack/react-query";

import { tileBaseUrl } from "./api";

const DEFAULT_LAYER = "public.road_segments_tile";

export type TileSourceConfig = {
  url: string;
  layer: string;
};

const buildTileUrl = (layer: string): string => `${tileBaseUrl()}/tiles/${layer}/{z}/{x}/{y}.pbf`;

export const useTileSourceConfig = () =>
  useQuery<TileSourceConfig>({
    queryKey: ["tile-source", DEFAULT_LAYER],
    queryFn: () =>
      Promise.resolve({
        url: buildTileUrl(DEFAULT_LAYER),
        layer: DEFAULT_LAYER,
      }),
    staleTime: Infinity,
  });
