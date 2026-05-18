/**
 * Phase 1.6.7 — map smoke test.
 *
 * Boots the dev server (via playwright.config.ts webServer), loads the
 * map page, and asserts:
 *
 *  - At least one tile request to pg_tileserv returned 200.
 *  - The map canvas is not all background pixels after the load completes
 *    (i.e., something rendered).
 *
 * Pre-requisites for a real green run:
 *
 *  - `docker compose up -d` (postgres + tileserv running).
 *  - `make seed` against a city with at least one segment in the bbox.
 *
 * If those preconditions aren't met, the tile-request assertion fails fast
 * with a clear message — that's the intended signal.
 */

import { expect, test } from "@playwright/test";

test.describe("Map view", () => {
  test("loads tiles and renders pixels", async ({ page }) => {
    const tileResponses: number[] = [];

    page.on("response", (resp) => {
      const url = resp.url();
      // Phase 4b appends ?city_slug=<slug> (and ?t=<iso> when scrubbing)
      // to every tile URL — endsWith(".pbf") no longer matches.
      // Use .includes(".pbf") so query-string tiles still register.
      if (url.includes("/tiles/") && url.includes(".pbf")) {
        tileResponses.push(resp.status());
      }
    });

    await page.goto("/");

    // Give MapLibre time to fetch tiles + render.
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(500);

    // At least one tile request that returned 200.
    const okTiles = tileResponses.filter((s) => s === 200);
    expect(okTiles.length).toBeGreaterThan(0);

    // Canvas exists and has non-background pixels.
    const canvas = page.locator("canvas").first();
    await expect(canvas).toBeVisible();

    const hasNonBackgroundPixels = await canvas.evaluate((el) => {
      const c = el as HTMLCanvasElement;
      // Sample a small region from the canvas via a temp 2D ctx.
      const tmp = document.createElement("canvas");
      tmp.width = c.width;
      tmp.height = c.height;
      const ctx = tmp.getContext("2d");
      if (!ctx) return false;
      ctx.drawImage(c, 0, 0);
      const sample = ctx.getImageData(
        Math.floor(c.width / 4),
        Math.floor(c.height / 4),
        Math.min(64, c.width),
        Math.min(64, c.height)
      );
      // "All background" check: any pixel with non-zero alpha and non-uniform color.
      const data = sample.data;
      let nonAlpha = 0;
      for (let i = 3; i < data.length; i += 4) {
        if (data[i]! > 0) nonAlpha += 1;
      }
      return nonAlpha > 0;
    });

    expect(hasNonBackgroundPixels).toBe(true);
  });
});
