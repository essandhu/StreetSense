/**
 * Tests for the `activeCity` slice — Phase 4b Task 4.1 (RED).
 *
 * The slice owns the currently-active city slug. Three responsibilities:
 *
 * 1. **Set / clear reducers.** `setActiveCity(slug)` overwrites the
 *    slug; `clearActiveCity()` resets to the project default (the
 *    Phase 1-4 grandfathered cambridge — see `currentCitySlug()` in
 *    `frontend/src/data/api.ts` and ADR 0010).
 *
 * 2. **URL hydration.** A pure helper reads `?city=<slug>` off a URL
 *    search-params string and resolves it to a slug; missing or
 *    blank falls back to the default. Phase 4 Task 4.5 layers
 *    bidirectional `react-router` sync on top of this; for now the
 *    slice owns just the read path so the App-shell mount step
 *    can hydrate without pulling in router glue.
 *
 * 3. **Default-city fallback.** Initial state is the default slug.
 *    The constant exposed by `frontend/src/data/api.ts`'s
 *    `currentCitySlug()` is intentionally NOT imported here — that
 *    helper is the Phase 4b Task 3.6 forward-compat hook that
 *    Phase 4 Task 4.3 replaces with a selector over THIS slice.
 *    Importing it would create a cycle. The slice defines its own
 *    `DEFAULT_CITY_SLUG` constant; once Task 4.3 lands, the
 *    `currentCitySlug()` body becomes a thin re-export from the
 *    store selector and the cycle never forms.
 *
 * Tests follow the bare-reducer pattern from `delta.test.ts`: no
 * React renderer, no store, just `reducer(state, action)` calls.
 */
import { describe, expect, it } from "vitest";

import reducer, {
  clearActiveCity,
  DEFAULT_CITY_SLUG,
  hydrateActiveCityFromUrl,
  setActiveCity,
  type ActiveCityState,
} from "./activeCity";

const initial = (): ActiveCityState => reducer(undefined, { type: "@@INIT" });

describe("activeCity slice — initial state", () => {
  it("starts with the project default city slug", () => {
    expect(initial()).toEqual({ slug: DEFAULT_CITY_SLUG });
  });

  it("DEFAULT_CITY_SLUG is 'cambridge' (Phase 1-4 grandfathered demo)", () => {
    expect(DEFAULT_CITY_SLUG).toBe("cambridge");
  });
});

describe("activeCity slice — setActiveCity", () => {
  it("setActiveCity overwrites the slug", () => {
    expect(reducer(initial(), setActiveCity("phoenix"))).toEqual({
      slug: "phoenix",
    });
  });

  it("re-setting to the same slug is a no-op (idempotent)", () => {
    const after = reducer({ slug: "phoenix" }, setActiveCity("phoenix"));
    expect(after).toEqual({ slug: "phoenix" });
  });

  it("setActiveCity lower-cases and trims the payload", () => {
    // Slug normalization belongs in one place — the reducer — so URL
    // params, deep-link banners, and the dropdown can all dispatch
    // user-typed text without each enforcing the convention.
    expect(reducer(initial(), setActiveCity("  Phoenix  "))).toEqual({
      slug: "phoenix",
    });
    expect(reducer(initial(), setActiveCity("SAN-FRANCISCO"))).toEqual({
      slug: "san-francisco",
    });
  });
});

describe("activeCity slice — clearActiveCity", () => {
  it("clearActiveCity resets to the default slug", () => {
    expect(reducer({ slug: "phoenix" }, clearActiveCity())).toEqual({
      slug: DEFAULT_CITY_SLUG,
    });
  });

  it("clearing while already on the default is a no-op", () => {
    expect(reducer({ slug: DEFAULT_CITY_SLUG }, clearActiveCity())).toEqual({
      slug: DEFAULT_CITY_SLUG,
    });
  });
});

describe("hydrateActiveCityFromUrl — pure helper", () => {
  it("returns the slug from `?city=<slug>`", () => {
    expect(hydrateActiveCityFromUrl("?city=phoenix")).toBe("phoenix");
  });

  it("accepts a bare query string without the leading `?`", () => {
    expect(hydrateActiveCityFromUrl("city=austin")).toBe("austin");
  });

  it("normalizes case and whitespace", () => {
    expect(hydrateActiveCityFromUrl("?city=%20Phoenix%20")).toBe("phoenix");
    expect(hydrateActiveCityFromUrl("?city=SAN-FRANCISCO")).toBe("san-francisco");
  });

  it("falls back to the default when `city` is missing", () => {
    expect(hydrateActiveCityFromUrl("")).toBe(DEFAULT_CITY_SLUG);
    expect(hydrateActiveCityFromUrl("?")).toBe(DEFAULT_CITY_SLUG);
    expect(hydrateActiveCityFromUrl("?layer=composite")).toBe(DEFAULT_CITY_SLUG);
  });

  it("falls back to the default when `city` is blank", () => {
    // A `?city=` with no value (or only whitespace) is treated as
    // "user opened the deep link directly" rather than as a real
    // slug. The dropdown's selection writes a non-empty slug back,
    // so the blank case really is the empty/garbage path.
    expect(hydrateActiveCityFromUrl("?city=")).toBe(DEFAULT_CITY_SLUG);
    expect(hydrateActiveCityFromUrl("?city=%20")).toBe(DEFAULT_CITY_SLUG);
  });

  it("ignores extra query parameters", () => {
    expect(hydrateActiveCityFromUrl("?layer=glare&city=phoenix&hour=11")).toBe(
      "phoenix",
    );
  });

  it("when `city` appears multiple times, the first value wins", () => {
    // URLSearchParams.get() returns the first match. Asserting this
    // explicitly means a future swap to a different parser (e.g.,
    // `qs`) doesn't silently change semantics — a deep link of the
    // form `?city=phoenix&city=austin` will always select phoenix
    // until the test is updated.
    expect(hydrateActiveCityFromUrl("?city=phoenix&city=austin")).toBe(
      "phoenix",
    );
  });
});

describe("activeCity slice — round-trip: hydrate then dispatch", () => {
  it("dispatches the hydrated slug as setActiveCity", () => {
    // Mirrors how the App shell will mount: read the URL, hydrate,
    // dispatch. Asserting both steps in one test catches a future
    // mismatch where the helper returns a different shape than
    // setActiveCity accepts.
    const slug = hydrateActiveCityFromUrl("?city=austin");
    expect(reducer(initial(), setActiveCity(slug))).toEqual({ slug: "austin" });
  });

  it("hydrating an absent `city` lands on the default slug", () => {
    const slug = hydrateActiveCityFromUrl("");
    expect(reducer(initial(), setActiveCity(slug))).toEqual({
      slug: DEFAULT_CITY_SLUG,
    });
  });
});
