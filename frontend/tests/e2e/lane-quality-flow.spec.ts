/**
 * Phase 3.7.1 — end-to-end lane-quality flow.
 *
 * Demonstrable output: clicking a Cambridge road segment opens the
 * panel, the radial chart shows a real lane-marking arc, the
 * confidence dial labels one of the three limiters, and clicking a
 * thumbnail loads a full-resolution Mapillary image.
 *
 * This spec is *resilient*: it tries several click offsets to land
 * on a segment because the road density varies by zoom and the
 * baseline pixel target isn't deterministic across MapLibre versions.
 *
 * Prerequisites:
 *   - `docker compose up -d`
 *   - `make seed`
 *   - `make ingest-imagery`     (needs MAPILLARY_ACCESS_TOKEN in env)
 *   - `make seed-model`
 *   - `make scoring-run`
 *   - `make api`
 *   - `pnpm dev`               (Playwright spins this up via webServer)
 */
import { expect, test } from "@playwright/test";

test("lane-quality flow against seeded Cambridge", async ({ page }) => {
  // Network logging — useful for diagnosing tile / segment-detail timeouts.
  page.on("response", async (resp) => {
    if (resp.status() >= 500) {
      console.warn(`[${resp.status()}] ${resp.url()}`);
    }
  });

  await page.goto("/");
  await page.waitForSelector("canvas", { timeout: 30_000 });

  // The lane-marking-quality layer rides the existing tile pipeline
  // (no separate UI toggle). Click somewhere in central Cambridge.
  const canvas = page.locator("canvas").first();
  const box = await canvas.boundingBox();
  if (!box) throw new Error("No canvas");
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;

  const offsets: Array<[number, number]> = [];
  for (let dx = -80; dx <= 80; dx += 20) {
    for (let dy = -80; dy <= 80; dy += 20) {
      offsets.push([dx, dy]);
    }
  }

  let opened = false;
  for (const [dx, dy] of offsets) {
    await page.mouse.click(cx + dx, cy + dy);
    try {
      await expect(page.getByTestId("segment-detail-panel")).toBeVisible({
        timeout: 1500,
      });
      opened = true;
      break;
    } catch {
      // Try the next.
    }
  }
  expect(opened, "panel did not open after exhaustive click grid").toBe(true);

  // The radial chart has a real lane-marking arc (data-stub="false").
  const laneArc = page.locator(
    '[data-arc-name="lane_marking_quality"][data-stub="false"]',
  );
  await expect(laneArc).toBeVisible();

  // The confidence dial labels one of the three known limiters.
  const limiter = await page.getByTestId("confidence-limiter-label").textContent();
  expect(["Freshness", "Coverage", "Model"]).toContain(limiter?.trim());

  // At least one source-imagery thumbnail.
  const thumbs = page.getByTestId("segment-detail-thumbnail");
  const count = await thumbs.count();
  expect(count).toBeGreaterThan(0);

  // Click → lightbox → full-resolution image loads.
  await thumbs.first().click();
  const lightbox = page.getByTestId("segment-detail-lightbox");
  await expect(lightbox).toBeVisible();
  const natural = await lightbox
    .locator("img")
    .evaluate((img: HTMLImageElement) => img.naturalWidth);
  expect(natural).toBeGreaterThan(0);
});
