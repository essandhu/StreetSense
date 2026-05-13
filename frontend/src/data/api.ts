/**
 * HTTP client + base URL.
 *
 * Phase 1: a thin fetch wrapper. Phase 3+ may pull in `openapi-fetch` to
 * enforce param/return shapes against the OpenAPI schema; not yet needed.
 */

const DEFAULT_API_BASE = "http://localhost:8000";
const DEFAULT_TILE_BASE = "http://localhost:7800";

export const apiBaseUrl = (): string =>
  // Vite exposes env vars prefixed with VITE_ at build time.
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? DEFAULT_API_BASE;

export const tileBaseUrl = (): string =>
  (import.meta.env.VITE_TILE_BASE_URL as string | undefined) ?? DEFAULT_TILE_BASE;

export const fetchJson = async <T>(path: string): Promise<T> => {
  const response = await fetch(`${apiBaseUrl()}${path}`);
  if (!response.ok) {
    throw new Error(`API error ${response.status}: ${path}`);
  }
  return (await response.json()) as T;
};
