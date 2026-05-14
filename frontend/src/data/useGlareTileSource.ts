/**
 * Glare tile source — Phase 2 hook.
 *
 * Computes the snapped ISO-8601 timestamp from the Redux `scrubber`
 * state and a fixed reference year (2025 — the year the scoring run
 * wrote rows for; see `scoring/cli.py:DEFAULT_REFERENCE_DAY`). Returns
 * the tile URL for the time-parameterized vector tile source
 * `public.road_segments_tile_t` with `?t=...` appended.
 *
 * Wrapped as a TanStack Query hook so the cache and re-fetch behavior
 * are routed through one place. `staleTime: Infinity` because the 24
 * hourly tiles don't change within a scoring run — re-fetching is
 * wasted work. When the scrubber hour changes, the query key changes,
 * which triggers a fresh fetch through deck.gl's `MVTLayer`.
 *
 * URL shape:
 *   {tileBase}/tiles/public.road_segments_tile_t/{z}/{x}/{y}.pbf?t={iso}
 */

import { useQuery } from "@tanstack/react-query";
import { useSelector } from "react-redux";

import type { ScrubberState } from "../state/scrubber";
import type { RootState } from "../state/store";

import { tileBaseUrl } from "./api";

const LAYER = "public.road_segments_tile_t";

// The reference year is the year the scoring run wrote rows for. Phase
// 2's `scoring/cli.py` runs scores against day-of-year 80..172 etc. of
// 2025; the frontend has to match.
const REFERENCE_YEAR = 2025;

export type GlareTileSource = {
  url: string;
  layer: string;
  t: string;
};

const dayOfYearToDate = (year: number, dayOfYear: number): { month: number; day: number } => {
  // Civil day calendar; clamp dayOfYear to [1, 365] for safety.
  const clamped = Math.min(365, Math.max(1, Math.round(dayOfYear)));
  const date = new Date(Date.UTC(year, 0, clamped));
  return { month: date.getUTCMonth() + 1, day: date.getUTCDate() };
};

const pad = (n: number): string => (n < 10 ? `0${n}` : `${n}`);

const buildSnappedIso = (state: ScrubberState): string => {
  const hour = Math.min(23, Math.max(0, Math.round(state.hourOfDay)));
  const { month, day } = dayOfYearToDate(REFERENCE_YEAR, state.dayOfYear);
  return `${REFERENCE_YEAR}-${pad(month)}-${pad(day)}T${pad(hour)}:00:00Z`;
};

/**
 * Build the tile URL given a scrubber state. Exported for unit tests
 * so the URL math is testable without React / Redux setup.
 */
export const glareTileUrl = (state: ScrubberState): string => {
  const iso = buildSnappedIso(state);
  return `${tileBaseUrl()}/tiles/${LAYER}/{z}/{x}/{y}.pbf?t=${encodeURIComponent(iso)}`;
};

const scrubberSelector = (state: RootState): ScrubberState => state.scrubber;

export const useGlareTileSource = () => {
  const scrubber = useSelector(scrubberSelector);
  const t = buildSnappedIso(scrubber);

  return useQuery<GlareTileSource>({
    queryKey: ["glare-tile-source", LAYER, t],
    // The "fetch" is just URL construction — no network. TanStack Query
    // is here for cache + invalidation semantics; the work is in deck.gl
    // re-fetching the tile bytes when the URL changes.
    queryFn: () =>
      Promise.resolve({
        url: glareTileUrl(scrubber),
        layer: LAYER,
        t,
      }),
    staleTime: Infinity,
  });
};
