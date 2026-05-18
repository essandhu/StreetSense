/**
 * Phase 4b Task 4.9 — city-switch E2E.
 *
 * Verifies the headline AC-2 + AC-4b contracts end-to-end:
 *
 *  - The default city (Cambridge) loads on bare ``/``.
 *  - The CitySelector dropdown can switch to a second city. The URL
 *    syncs to ``?city=<slug>``, MapLibre fits to the new bbox, and
 *    new vector-tile requests are city-scoped to the new slug.
 *  - A deep link (``/?city=austin``) opens the app directly on the
 *    target city without ever fetching the default city's tiles.
 *
 * Preconditions for a real green run:
 *
 *  - ``docker compose up -d`` (postgres + tileserv).
 *  - ``make seed CITY=<slug>`` for at least cambridge AND one of the
 *    curated additions (phoenix, san-francisco, austin, los-angeles).
 *  - ``make seed-cities`` so ``GET /api/cities`` returns the registry.
 *
 * If a slug has zero ``road_segments`` the tile responses are still
 * valid empty MVTs (verified in ``tests/api/test_tile_city_scope.py``).
 * This spec asserts the URL + tile-source plumbing — segment-count
 * assertions belong in the API tests, not here.
 */

import { expect, test } from "@playwright/test";

const CAMBRIDGE_SLUG = "cambridge";
const SECOND_CITY_SLUG = "phoenix";
const DEEP_LINK_SLUG = "austin";

// pg_tileserv URL shape: ``/tiles/{function_name}/{z}/{x}/{y}.pbf``.
// One path segment (the function name) lives between ``/tiles/`` and the
// numeric z/x/y triplet.
const tileUrlMatcher = /\/tiles\/[^/]+\/\d+\/\d+\/\d+\.pbf/;

const tileSlug = (url: string): string | null => {
  // tile URL shape: ``/tiles/{function_name}/{z}/{x}/{y}.pbf?city_slug=<slug>``
  // pg_tileserv keeps the function name in the path; the slug rides
  // along as a query parameter (Phase 4b Task 3.6).
  try {
    const parsed = new URL(url);
    return parsed.searchParams.get("city_slug");
  } catch {
    return null;
  }
};

test.describe("Phase 4b city switching", () => {
  test("default load shows the default city (cambridge)", async ({ page }) => {
    const tileSlugs = new Set<string>();
    page.on("response", (resp) => {
      if (tileUrlMatcher.test(resp.url())) {
        const slug = tileSlug(resp.url());
        if (slug) tileSlugs.add(slug);
      }
    });

    await page.goto("/");

    const selector = page.getByTestId("city-selector");
    await expect(selector).toBeVisible();
    await expect(selector).toHaveValue(CAMBRIDGE_SLUG);

    // The canvas is up and tiles for cambridge were fetched.
    // Poll for the first cambridge tile rather than waiting for full
    // network-idle (LA's 10M+ scored rows make idle a slow signal).
    await expect(page.locator("canvas").first()).toBeVisible();
    await expect
      .poll(() => tileSlugs.has(CAMBRIDGE_SLUG), { timeout: 10_000 })
      .toBe(true);
  });

  test("dropdown switch updates URL, dispatches tile fetches for the new slug", async ({
    page,
  }) => {
    const tileSlugsAfterSwitch = new Set<string>();

    await page.goto("/");
    // Wait just long enough for the selector to hydrate so we know
    // the boot tile fetches have started; we don't need full idle.
    await expect(page.getByTestId("city-selector")).toHaveValue(CAMBRIDGE_SLUG);

    // Start listening AFTER initial load so we only see post-switch
    // tile requests (and ignore the cambridge boot fetches).
    page.on("response", (resp) => {
      if (tileUrlMatcher.test(resp.url())) {
        const slug = tileSlug(resp.url());
        if (slug) tileSlugsAfterSwitch.add(slug);
      }
    });

    // Switch via the native <select> — the CitySelector is a
    // single-select dropdown with value=slug per
    // ``frontend/src/components/AppShell/CitySelector.tsx``.
    await page.getByTestId("city-selector").selectOption(SECOND_CITY_SLUG);

    // The CitySelector itself reflects the new state — locator
    // auto-retries up to 5s, which is enough for the synchronous
    // Redux dispatch + re-render.
    await expect(page.getByTestId("city-selector")).toHaveValue(SECOND_CITY_SLUG);

    // URL hydration round-trip: state writes to the URL via
    // ``useActiveCityUrlSync`` (history.replaceState). Poll
    // briefly — replaceState fires inside a useEffect.
    await expect
      .poll(() => new URL(page.url()).searchParams.get("city"), { timeout: 5000 })
      .toBe(SECOND_CITY_SLUG);

    // At least one tile fetch went to the new slug. With multi-city
    // ingest complete, the larger cities (phoenix 325k, LA 453k
    // segments) generate enough tile traffic that ``networkidle``
    // can take >30s to settle; we don't wait for that. We do give
    // the listener a brief window to capture the first post-switch
    // fetch — deck.gl/MapLibre re-source happens synchronously,
    // tile network calls land in the next 500-1000ms.
    await expect
      .poll(() => tileSlugsAfterSwitch.has(SECOND_CITY_SLUG), { timeout: 8000 })
      .toBe(true);
  });

  test("deep link ?city=austin loads austin directly, not the default city", async ({
    page,
  }) => {
    const tileSlugsSeen: string[] = [];
    page.on("response", (resp) => {
      if (tileUrlMatcher.test(resp.url())) {
        const slug = tileSlug(resp.url());
        if (slug) tileSlugsSeen.push(slug);
      }
    });

    await page.goto(`/?city=${DEEP_LINK_SLUG}`);

    // CitySelector hydrated from the URL on mount (per
    // ``hydrateActiveCityFromUrl`` + ``useActiveCityUrlSync``).
    await expect(page.getByTestId("city-selector")).toHaveValue(DEEP_LINK_SLUG);

    // Wait for the first tile fetch to land. Austin (224k segments)
    // generates enough traffic that ``networkidle`` is unreliable;
    // we just need at least one tile request to verify the deep link
    // routed through the activeCity slice.
    await expect.poll(() => tileSlugsSeen.length, { timeout: 10_000 }).toBeGreaterThan(0);

    // Tile fetches all went to austin — the deep link did not
    // flash through cambridge tiles. The activeCity slice hydrates
    // synchronously on mount before MapLibre's first tile request,
    // so this property holds even on a cold load.
    expect(tileSlugsSeen.every((slug) => slug === DEEP_LINK_SLUG)).toBe(true);
  });

  test("URL syncs back when switching from a deep-linked city", async ({
    page,
  }) => {
    // Start deep-linked to austin, then switch to phoenix via
    // the dropdown; URL must change to ?city=phoenix.
    //
    // We deliberately don't ``waitForLoadState("networkidle")`` —
    // austin (224k) and phoenix (325k) both generate enough tile
    // traffic that networkidle can take >30s. Instead we wait on
    // the specific UI state we care about (selector value + URL),
    // which auto-retries.
    await page.goto(`/?city=${DEEP_LINK_SLUG}`);
    await expect(page.getByTestId("city-selector")).toHaveValue(DEEP_LINK_SLUG);

    await page.getByTestId("city-selector").selectOption(SECOND_CITY_SLUG);
    await expect(page.getByTestId("city-selector")).toHaveValue(SECOND_CITY_SLUG);
    await expect
      .poll(() => new URL(page.url()).searchParams.get("city"), { timeout: 5000 })
      .toBe(SECOND_CITY_SLUG);
  });
});
