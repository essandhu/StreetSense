/**
 * Phase 4 — composite layer + breakdown E2E (spec AC-8).
 *
 * Boots the local stack (Postgres + MinIO + API + dev server) with
 * the seeded Cambridge dataset and asserts the headline Phase 4
 * frontend contracts:
 *
 *  - The composite-risk layer is the default-on layer at app boot.
 *  - The layer toggle is visible and switching between layers takes
 *    effect (the deck.gl canvas redraws with a different color
 *    expression).
 *  - Clicking a segment opens the panel with the composite breakdown
 *    visible (composite total, local bar, uplift bar, algorithm
 *    label).
 *
 * Preconditions for a real green run:
 *
 *  - ``docker compose up -d`` (postgres + tileserv running).
 *  - ``make seed`` (Cambridge OSM into Postgres).
 *  - ``make scoring-run`` writes Phase 4 rows with
 *    ``propagation_algorithm_version = "pagerank-diffusion-0.1.0"``.
 *
 * Open-to-render latency is measured separately in the bench suite
 * (``playwright.bench.config.ts``); this spec only asserts wiring.
 */

import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const sampleCenterPixels = async (page: Page): Promise<string> => {
  return await page.evaluate(() => {
    const canvases = Array.from(document.querySelectorAll("canvas")) as HTMLCanvasElement[];
    const out: string[] = [];
    for (const canvas of canvases) {
      const w = canvas.width;
      const h = canvas.height;
      if (w === 0 || h === 0) continue;
      const tmp = document.createElement("canvas");
      tmp.width = w;
      tmp.height = h;
      const tctx = tmp.getContext("2d");
      if (!tctx) continue;
      tctx.drawImage(canvas, 0, 0);
      const sample = tctx.getImageData(
        Math.floor(w / 2) - 8,
        Math.floor(h / 2) - 8,
        16,
        16,
      ).data;
      let hash = 0;
      for (let i = 0; i < sample.length; i++) {
        hash = (hash * 31 + sample[i]!) | 0;
      }
      out.push(hash.toString(36));
    }
    return out.join("|");
  });
};

test.describe("Phase 4 composite layer + breakdown", () => {
  test("layer toggle is visible and composite is the default", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    const toggle = page.getByTestId("layer-toggle");
    await expect(toggle).toBeVisible();

    // Composite is checked by default per spec AC-8.
    const compositeRadio = page.getByTestId("layer-toggle-composite");
    await expect(compositeRadio).toBeChecked();

    // The other four are not checked but are present and selectable.
    for (const layerId of ["glare", "lane", "junction", "historical"]) {
      const radio = page.getByTestId(`layer-toggle-${layerId}`);
      await expect(radio).toBeVisible();
      await expect(radio).not.toBeChecked();
    }
  });

  test("switching layers redraws the canvas with a different color expression", async ({
    page,
  }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(800);

    // Composite default.
    const composite = await sampleCenterPixels(page);

    // Switch to glare; allow the new MVTLayer to paint.
    await page.getByTestId("layer-toggle-glare").click();
    await page.waitForTimeout(600);
    const glare = await sampleCenterPixels(page);

    // Switch to lane.
    await page.getByTestId("layer-toggle-lane").click();
    await page.waitForTimeout(600);
    const lane = await sampleCenterPixels(page);

    // Switch back to composite.
    await page.getByTestId("layer-toggle-composite").click();
    await page.waitForTimeout(600);
    const compositeAgain = await sampleCenterPixels(page);

    // Composite ≠ glare ≠ lane proves the accessor flip took effect.
    // (The three sub-score attributes have distinct distributions on
    // any real Cambridge segment, so the bucket assignments differ.)
    expect(composite).not.toEqual(glare);
    expect(glare).not.toEqual(lane);
    // Toggling back to composite returns to a state identical to the
    // initial composite paint (same tile, same accessor, no
    // intervening scrubber move).
    expect(compositeAgain).toEqual(composite);
  });

  test("clicking a segment opens the panel with the composite breakdown", async ({
    page,
  }) => {
    await page.goto("/");
    await page.waitForSelector("canvas", { timeout: 20_000 });

    const canvas = page.locator("canvas").first();
    const box = await canvas.boundingBox();
    if (!box) throw new Error("MapLibre canvas not found");

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

    // Composite breakdown is rendered with the four required parts.
    const breakdown = page.getByTestId("composite-breakdown");
    await expect(breakdown).toBeVisible();
    await expect(page.getByTestId("composite-breakdown-total")).toBeVisible();
    await expect(page.getByTestId("composite-breakdown-local-bar")).toBeVisible();
    await expect(page.getByTestId("composite-breakdown-uplift-bar")).toBeVisible();

    // Algorithm label is one of the registered Phase 4 strategies. The
    // production scoring run uses ``pagerank-diffusion`` per ADR 0006;
    // any of the three registered strategies is acceptable if a
    // future operator switches the strategy_id.
    const algorithmLocator = page.getByTestId("composite-breakdown-algorithm");
    const sentinelLocator = page.getByTestId("composite-breakdown-no-algorithm");

    // Either an algorithm label or the sentinel fallback note is present
    // (one or the other; pre-Phase-4 sentinel rows take the no-algorithm
    // branch).
    const algoVisible = await algorithmLocator.isVisible().catch(() => false);
    const sentinelVisible = await sentinelLocator.isVisible().catch(() => false);
    expect(algoVisible || sentinelVisible).toBe(true);

    if (algoVisible) {
      const label = (await algorithmLocator.textContent()) ?? "";
      expect(label).toMatch(
        /(pagerank-diffusion|influence-diffusion|weighted-shortest-path)\s+\d+\.\d+\.\d+/,
      );
    }
  });
});
