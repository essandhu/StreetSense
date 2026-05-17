/**
 * HTTP client + base URL.
 *
 * Phase 1: a thin fetch wrapper. Phase 3+ may pull in `openapi-fetch` to
 * enforce param/return shapes against the OpenAPI schema; not yet needed.
 */
import { useSelector } from "react-redux";

import type { RootState } from "../state/store";

const DEFAULT_API_BASE = "http://localhost:8000";
const DEFAULT_TILE_BASE = "http://localhost:7800";

export const apiBaseUrl = (): string =>
  // Vite exposes env vars prefixed with VITE_ at build time.
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? DEFAULT_API_BASE;

export const tileBaseUrl = (): string =>
  (import.meta.env.VITE_TILE_BASE_URL as string | undefined) ?? DEFAULT_TILE_BASE;

/**
 * The active city slug from the `activeCity` Redux slice.
 *
 * Phase 4b Task 4.3 replaces the previous static
 * `currentCitySlug()` helper with a selector hook. Every
 * city-scoped TanStack Query / tile URL builder reads its slug
 * through this hook so a `setActiveCity` dispatch naturally
 * invalidates every cache and re-binds every tile source.
 *
 * Pure URL builders that can't be hooks (test fixtures, isolated
 * benchmarks, ad-hoc deep-link composers) should import
 * `DEFAULT_CITY_SLUG` from `frontend/src/state/activeCity` and
 * pass it explicitly — they SHOULD NOT call this hook outside a
 * React render.
 */
export const useActiveCitySlug = (): string =>
  useSelector((state: RootState) => state.activeCity.slug);

export const fetchJson = async <T>(path: string): Promise<T> => {
  const response = await fetch(`${apiBaseUrl()}${path}`);
  if (!response.ok) {
    throw new Error(`API error ${response.status}: ${path}`);
  }
  return (await response.json()) as T;
};

/**
 * Build the path prefix for a city-scoped API call. All Phase 4b
 * read endpoints sit under `/api/cities/{slug}/...`; this helper
 * keeps the prefix in one place so the slug is URL-encoded
 * consistently and a future change (e.g., to a versioned namespace)
 * touches one line.
 */
export const cityScopedPath = (slug: string, suffix: string): string =>
  `/api/cities/${encodeURIComponent(slug)}${suffix.startsWith("/") ? suffix : `/${suffix}`}`;
