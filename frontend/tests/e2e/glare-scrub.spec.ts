/**
 * Phase 2.6.1 — glare scrubbing E2E.
 *
 * Boots a local stack with the seeded Cambridge dataset, loads the map,
 * scrubs through four representative UTC times (sunrise, solar noon,
 * golden hour, midnight), and asserts:
 *
 *  - The deck.gl canvas pixels change between scrub steps (proof the
 *    glare overlay actually redraws).
 *  - Pan/zoom during scrubbing stays under the 100 ms p95 frame budget
 *    (CLAUDE.md / spec.md AC-4 invariant — Phase 1 contract preserved
 *    under the new overlay).
 *
 * Preconditions for a real green run:
 *
 *  - `docker compose up -d` (postgres + tileserv running).
 *  - `make seed` (Cambridge OSM into Postgres).
 *  - `make scoring-run` (24 hourly glare samples on the reference day).
 */

import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

// UTC times that put the Cambridge sun at four distinct geometries on
// the summer solstice (the scoring-run default reference day).
const SCRUB_STEPS = [
  { label: "midnight", hour: 5 }, //  ~01:00 EDT — sun below horizon
  { label: "sunrise", hour: 10 }, // ~06:00 EDT — sun low east
  { label: "solar_noon", hour: 17 }, // ~13:00 EDT — sun south, high
  { label: "golden_hour", hour: 23 }, // ~19:00 EDT — sun low west
];

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
      // Hash to a compact string for change-detection comparison.
      let hash = 0;
      for (let i = 0; i < sample.length; i++) {
        hash = (hash * 31 + sample[i]!) | 0;
      }
      out.push(hash.toString(36));
    }
    return out.join("|");
  });
};

const setHour = async (page: Page, hour: number): Promise<void> => {
  await page.evaluate((h) => {
    const input = document.querySelector('input[aria-label="hour"]') as HTMLInputElement | null;
    if (!input) throw new Error('input[aria-label="hour"] not found');
    const proto = Object.getPrototypeOf(input);
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    setter?.call(input, String(h));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }, hour);
};

test.describe("Glare scrubber", () => {
  test("deck.gl canvas pixels change between hour-of-day samples", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    // Wait for first overlay frame.
    await page.waitForTimeout(800);

    const hashes: string[] = [];
    for (const step of SCRUB_STEPS) {
      await setHour(page, step.hour);
      // Allow the new tile to fetch + render.
      await page.waitForTimeout(800);
      const h = await sampleCenterPixels(page);
      hashes.push(h);
    }

    // All four hashes are distinct (the overlay actually redraws).
    const unique = new Set(hashes);
    expect.soft(unique.size, `expected 4 distinct overlay states, got ${unique.size}`).toBe(
      SCRUB_STEPS.length,
    );
  });

  test("pan/zoom under scrubbing keeps p95 frame budget < 100 ms", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // Install frame-time recorder before we start interacting.
    await page.evaluate(() => {
      interface FrameWindow extends Window {
        __frameTimes?: number[];
      }
      const w = window as FrameWindow;
      w.__frameTimes = [];
      let last = performance.now();
      const tick = (now: number) => {
        w.__frameTimes!.push(now - last);
        last = now;
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });

    const canvas = page.locator("canvas").first();
    const box = await canvas.boundingBox();
    if (!box) throw new Error("canvas not visible");
    const cx = box.x + box.width / 2;
    const cy = box.y + box.height / 2;

    // Interleave scrubbing with pan/zoom — the stress case.
    for (let i = 0; i < SCRUB_STEPS.length; i++) {
      await setHour(page, SCRUB_STEPS[i]!.hour);
      await page.mouse.move(cx, cy);
      await page.mouse.down();
      await page.mouse.move(cx + 120, cy + 60, { steps: 20 });
      await page.mouse.up();
      await page.waitForTimeout(150);
      await page.mouse.wheel(0, -200);
      await page.waitForTimeout(150);
      await page.mouse.wheel(0, 200);
      await page.waitForTimeout(150);
    }

    const samples = await page.evaluate(() => {
      interface FrameWindow extends Window {
        __frameTimes?: number[];
      }
      return (window as FrameWindow).__frameTimes?.slice(1) ?? [];
    });

    expect(samples.length).toBeGreaterThan(30);

    const sorted = [...samples].sort((a, b) => a - b);
    const quantile = (arr: number[], q: number): number => {
      if (arr.length === 0) return 0;
      const idx = Math.max(0, Math.min(arr.length - 1, Math.ceil(q * arr.length) - 1));
      return arr[idx]!;
    };
    const p95 = quantile(sorted, 0.95);
    expect(p95).toBeLessThan(100);
  });
});
