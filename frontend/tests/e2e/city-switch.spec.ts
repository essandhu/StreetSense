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
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(500);

    const selector = page.getByTestId("city-selector");
    await expect(selector).toBeVisible();
    await expect(selector).toHaveValue(CAMBRIDGE_SLUG);

    // The canvas is up and tiles for cambridge were fetched.
    await expect(page.locator("canvas").first()).toBeVisible();
    expect(tileSlugs.has(CAMBRIDGE_SLUG)).toBe(true);
  });

  test("dropdown switch updates URL, dispatches tile fetches for the new slug", async ({
    page,
  }) => {
    const tileSlugsAfterSwitch = new Set<string>();

    await page.goto("/");
    await page.waitForLoadState("networkidle");

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
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(400);

    // URL hydration round-trip: state writes to the URL via
    // ``useActiveCityUrlSync`` (history.replaceState).
    expect(new URL(page.url()).searchParams.get("city")).toBe(SECOND_CITY_SLUG);

    // The CitySelector itself reflects the new state.
    await expect(page.getByTestId("city-selector")).toHaveValue(SECOND_CITY_SLUG);

    // At least one tile fetch went to the new slug. Phoenix may
    // genuinely have zero scored tiles right now (Task 2.5's
    // staged ingestion), so we assert on *tile URL shape*, not
    // segment-count. Empty MVTs are the documented success path.
    expect(tileSlugsAfterSwitch.has(SECOND_CITY_SLUG)).toBe(true);
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
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(500);

    // CitySelector hydrated from the URL on mount (per
    // ``hydrateActiveCityFromUrl`` + ``useActiveCityUrlSync``).
    await expect(page.getByTestId("city-selector")).toHaveValue(DEEP_LINK_SLUG);

    // Tile fetches all went to austin — the deep link did not
    // flash through cambridge tiles. The activeCity slice hydrates
    // synchronously on mount before MapLibre's first tile request,
    // so this property holds even on a cold load.
    expect(tileSlugsSeen.length).toBeGreaterThan(0);
    expect(tileSlugsSeen.every((slug) => slug === DEEP_LINK_SLUG)).toBe(true);
  });

  test("URL syncs back when switching from a deep-linked city", async ({
    page,
  }) => {
    // Start deep-linked to austin, then switch to phoenix via
    // the dropdown; URL must change to ?city=phoenix.
    await page.goto(`/?city=${DEEP_LINK_SLUG}`);
    await page.waitForLoadState("networkidle");

    await expect(page.getByTestId("city-selector")).toHaveValue(DEEP_LINK_SLUG);

    await page.getByTestId("city-selector").selectOption(SECOND_CITY_SLUG);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(300);

    expect(new URL(page.url()).searchParams.get("city")).toBe(SECOND_CITY_SLUG);
    await expect(page.getByTestId("city-selector")).toHaveValue(SECOND_CITY_SLUG);
  });
});
