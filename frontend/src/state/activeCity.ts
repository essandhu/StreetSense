/**
 * `activeCity` slice — Phase 4b Task 4.2.
 *
 * Owns the currently-active city slug. The slug travels through:
 *
 *   URL `?city=<slug>` ↔ this slice ↔ TanStack Query keys ↔ tile URLs ↔
 *   MapLibre `fitBounds` ↔ scrubber's local-noon seed
 *
 * with the slice as the canonical in-app source. Two responsibilities
 * live here; the other bindings are layered on top:
 *
 * - **Reducers.** `setActiveCity(slug)` (normalized) and
 *   `clearActiveCity()` (reset to default).
 * - **URL hydration helper.** `hydrateActiveCityFromUrl(search)` reads
 *   `?city=<slug>` off a query string for the App-shell mount step.
 *   Task 4.5 layers bidirectional `react-router` sync on top — the
 *   slice itself stays router-agnostic so unit tests stay cheap.
 *
 * Slug normalization (trim + lowercase) is centralized in the reducer
 * so every dispatcher — URL hydration, dropdown, deep-link banner —
 * gets the same canonical form without each one re-implementing it.
 *
 * Default-city fallback: `DEFAULT_CITY_SLUG` is 'cambridge' — the
 * Phase 1-4 grandfathered demo city (ADR 0010), and the only city
 * with ingested segment data through Phase 4b. The constant is
 * defined here (not imported from `frontend/src/data/api.ts`'s
 * `currentCitySlug()`) because Task 4.3 rewrites that helper to
 * READ from this slice via a selector — importing it would create
 * a cycle.
 *
 * UI state only — TanStack Query owns the city-scoped server data
 * (Task 4.3 makes every query key include the slug so cache
 * invalidation on city switch is automatic).
 */
import { createSlice, type PayloadAction } from "@reduxjs/toolkit";

export const DEFAULT_CITY_SLUG = "cambridge";

export type ActiveCityState = {
  slug: string;
};

const initialState: ActiveCityState = {
  slug: DEFAULT_CITY_SLUG,
};

const normalize = (raw: string): string => raw.trim().toLowerCase();

const slice = createSlice({
  name: "activeCity",
  initialState,
  reducers: {
    setActiveCity(state, action: PayloadAction<string>) {
      state.slug = normalize(action.payload);
    },
    clearActiveCity(state) {
      state.slug = DEFAULT_CITY_SLUG;
    },
  },
});

/**
 * Read `?city=<slug>` off a URL search string and resolve it to a
 * slug. Falls back to `DEFAULT_CITY_SLUG` when the param is missing
 * or blank.
 *
 * Accepts either a full `?city=...` query string (with the leading
 * `?`) or a bare `city=...` form. Returns a normalized (trimmed,
 * lowercased) slug; the dispatching site can pass the result
 * straight to `setActiveCity` without re-normalizing.
 *
 * When `?city` appears multiple times, the first value wins (matches
 * `URLSearchParams.get()` semantics — see the test for the explicit
 * assertion).
 */
export const hydrateActiveCityFromUrl = (search: string): string => {
  // URLSearchParams handles both leading-? and bare forms uniformly,
  // and decodes percent-escapes for us. Empty input is also fine —
  // `.get()` returns null.
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const raw = params.get("city");
  if (raw === null) return DEFAULT_CITY_SLUG;
  const normalized = normalize(raw);
  return normalized.length > 0 ? normalized : DEFAULT_CITY_SLUG;
};

export const { setActiveCity, clearActiveCity } = slice.actions;
export default slice.reducer;
