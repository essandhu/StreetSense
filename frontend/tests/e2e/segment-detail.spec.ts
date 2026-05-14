/**
 * Phase 3.6.14 — segment-detail E2E (map-click path).
 *
 * Boots the local stack (Postgres + MinIO + API + dev server) with
 * the seeded Cambridge dataset, clicks a road segment on the map,
 * and asserts the panel + chart + close-button flow.
 *
 * Open-to-render *latency* is measured in the dedicated benchmark
 * (``benchmarks/frontend/segment_detail_open.spec.ts``) — that uses
 * the bench window hook for a deterministic measurement. Here we
 * only care that the click hit-testing path works end-to-end.
 */
import { expect, test } from "@playwright/test";

test.describe("Segment detail flow", () => {
  test("click segment → panel → chart → thumbnail → lightbox", async ({ page }) => {
    await page.goto("/");
    await page.waitForSelector("canvas", { timeout: 20_000 });

    const canvas = page.locator("canvas").first();
    const box = await canvas.boundingBox();
    if (!box) throw new Error("MapLibre canvas not found");

    // Sweep a small grid; road density at the default zoom is
    // sparse enough that the first click often misses. Each attempt
    // is fast (sub-second) because we cap the wait per click.
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
          timeout: 750,
        });
        opened = true;
        break;
      } catch {
        // Next offset.
      }
    }
    expect(opened, "no click landed on a road segment").toBe(true);

    // 4 arcs always render.
    const arcs = page.locator(
      '[data-testid="segment-detail-panel"] svg [data-arc-name]',
    );
    await expect(arcs).toHaveCount(4);

    // Confidence dial label is one of the three limiters.
    const limiterLabel = await page.getByTestId("confidence-limiter-label").textContent();
    expect(["Freshness", "Coverage", "Model"]).toContain(limiterLabel?.trim());

    // Thumbnails (the clicked segment may or may not have imagery —
    // most Cambridge segments fell to the stub fallback in Phase 3's
    // budget-bounded ingest; assert the lightbox flow only when
    // thumbnails are present).
    const thumbnails = page.getByTestId("segment-detail-thumbnail");
    const count = await thumbnails.count();
    if (count > 0) {
      await thumbnails.first().click();
      const lightbox = page.getByTestId("segment-detail-lightbox");
      await expect(lightbox).toBeVisible();
      const naturalWidth = await lightbox
        .locator("img")
        .evaluate((img: HTMLImageElement) => img.naturalWidth);
      expect(naturalWidth).toBeGreaterThan(0);
      await page.keyboard.press("Escape");
      await expect(lightbox).toBeHidden();
    }

    // Close button closes the panel.
    await page.getByTestId("segment-detail-close").click();
    await expect(page.getByTestId("segment-detail-panel")).toBeHidden();
  });
});
