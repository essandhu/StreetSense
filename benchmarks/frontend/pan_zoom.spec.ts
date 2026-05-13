/**
 * Phase 1.6.8 — pan/zoom benchmark (Playwright).
 *
 * Scripts a pan + zoom sequence, measures per-frame timing via
 * requestAnimationFrame, and asserts:
 *
 *  - median frame time < 16 ms (60 fps)
 *  - p95 frame time     < 100 ms (CLAUDE.md / spec.md AC-4)
 *
 * Result JSON written to benchmarks/frontend/results/.
 *
 * Run:
 *   pnpm exec playwright test ../benchmarks/frontend/pan_zoom.spec.ts
 *
 * Or wire into a `pnpm bench:pan_zoom` script when the integration phase
 * arrives.
 */

import { expect, test } from "@playwright/test";
import * as fs from "node:fs";
import * as path from "node:path";

const RESULTS_DIR = path.resolve(__dirname, "results");
const BUDGET_MEDIAN_MS = 16;
const BUDGET_P95_MS = 100;

const quantile = (sorted: number[], q: number): number => {
  if (sorted.length === 0) return 0;
  const idx = Math.max(0, Math.min(sorted.length - 1, Math.ceil(q * sorted.length) - 1));
  return sorted[idx]!;
};

test("pan/zoom holds sub-100ms frame budget", async ({ page }) => {
  await page.goto("/");
  await page.waitForLoadState("networkidle");

  // Install a frame-time recorder before we start panning.
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

  // Pan / zoom sequence: drag across the canvas, then zoom in/out a few times.
  const canvas = page.locator("canvas").first();
  const box = await canvas.boundingBox();
  if (!box) throw new Error("canvas not visible");

  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;

  // Pans
  for (let i = 0; i < 5; i++) {
    await page.mouse.move(cx, cy);
    await page.mouse.down();
    await page.mouse.move(cx + 200, cy + 100, { steps: 30 });
    await page.mouse.up();
    await page.waitForTimeout(150);
  }

  // Zooms
  for (let i = 0; i < 3; i++) {
    await page.mouse.move(cx, cy);
    await page.mouse.wheel(0, -300);
    await page.waitForTimeout(150);
    await page.mouse.wheel(0, 300);
    await page.waitForTimeout(150);
  }

  // Collect samples; drop the very first frame (warm-up).
  const samples = await page.evaluate(() => {
    interface FrameWindow extends Window {
      __frameTimes?: number[];
    }
    return (window as FrameWindow).__frameTimes?.slice(1) ?? [];
  });

  expect(samples.length).toBeGreaterThan(30);

  const sorted = [...samples].sort((a, b) => a - b);
  const median = quantile(sorted, 0.5);
  const p95 = quantile(sorted, 0.95);
  const max = sorted[sorted.length - 1] ?? 0;
  const mean = samples.reduce((a, b) => a + b, 0) / samples.length;

  // Persist result.
  fs.mkdirSync(RESULTS_DIR, { recursive: true });
  const resultPath = path.join(
    RESULTS_DIR,
    `pan_zoom-${new Date().toISOString().replace(/:/g, "-")}.json`
  );
  fs.writeFileSync(
    resultPath,
    JSON.stringify(
      {
        timestamp: new Date().toISOString(),
        sample_count: samples.length,
        median_ms: median,
        mean_ms: mean,
        p95_ms: p95,
        max_ms: max,
        budget_median_ms: BUDGET_MEDIAN_MS,
        budget_p95_ms: BUDGET_P95_MS,
      },
      null,
      2
    )
  );

  expect(median).toBeLessThan(BUDGET_MEDIAN_MS);
  expect(p95).toBeLessThan(BUDGET_P95_MS);
});
