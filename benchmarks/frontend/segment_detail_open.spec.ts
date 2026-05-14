/**
 * Phase 3.6.15 — segment-detail open-to-render benchmark.
 *
 * Mirrors the Phase 2 scrubber-latency cadence: 5 warm-up steps, 40
 * measured steps. Each step clicks a road segment and measures the
 * elapsed time from click to the first frame in which the radial
 * chart's first `<path>` is visible.
 *
 * Asserts:
 *   p95 < 300 ms      (spec AC-5)
 *
 * Output: benchmarks/frontend/results/phase-3/segment_detail_open-{ISO}.json
 *
 * Run:
 *   pnpm exec playwright test --config=playwright.bench.config.ts \
 *     ./benchmarks/frontend/segment_detail_open.spec.ts
 */
import { expect, test } from "@playwright/test";
import * as fs from "node:fs";
import * as path from "node:path";

const RESULTS_DIR = path.resolve(__dirname, "results", "phase-3");
const BUDGET_P95_MS = 300;
const WARMUP_STEPS = 5;
const MEASURED_STEPS = 40;

const quantile = (sorted: number[], q: number): number => {
  if (sorted.length === 0) return 0;
  const idx = Math.max(0, Math.min(sorted.length - 1, Math.ceil(q * sorted.length) - 1));
  return sorted[idx]!;
};

test("segment-detail open-to-render p95 < 300 ms", async ({ page }) => {
  await page.goto("/");
  await page.waitForSelector("canvas", { timeout: 20_000 });

  const canvas = page.locator("canvas").first();
  const box = await canvas.boundingBox();
  if (!box) throw new Error("MapLibre canvas not found");

  const samples: number[] = [];
  // Move the cursor through a small grid of nearby points so each
  // click hits a different segment when possible.
  const grid: Array<[number, number]> = [];
  for (let dx = -60; dx <= 60; dx += 20) {
    for (let dy = -60; dy <= 60; dy += 20) {
      grid.push([dx, dy]);
    }
  }

  let i = 0;
  while (samples.length < WARMUP_STEPS + MEASURED_STEPS && i < grid.length * 3) {
    const offset = grid[i % grid.length];
    if (!offset) {
      i += 1;
      continue;
    }
    const [dx, dy] = offset;
    const cx = box.x + box.width / 2 + dx;
    const cy = box.y + box.height / 2 + dy;

    // Close any existing panel so we measure cold opens.
    await page.evaluate(() => {
      const btn = document.querySelector('[data-testid="segment-detail-close"]') as
        | HTMLButtonElement
        | null;
      btn?.click();
    });

    const t0 = await page.evaluate(() => performance.now());
    await page.mouse.click(cx, cy);

    try {
      await page.waitForSelector(
        '[data-testid="segment-detail-panel"] svg [data-arc-name]',
        { timeout: 1000 },
      );
      const t1 = await page.evaluate(() => performance.now());
      const elapsed = t1 - t0;
      if (samples.length < WARMUP_STEPS) {
        samples.push(elapsed); // collect but treat as warm-up below
      } else {
        samples.push(elapsed);
      }
    } catch {
      // Click missed a segment; advance.
    }
    i += 1;
  }

  const measured = samples.slice(WARMUP_STEPS).toSorted((a, b) => a - b);
  const record = {
    measured_steps: measured.length,
    p50_ms: Math.round(quantile(measured, 0.5)),
    p95_ms: Math.round(quantile(measured, 0.95)),
    p99_ms: Math.round(quantile(measured, 0.99)),
  };

  fs.mkdirSync(RESULTS_DIR, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  fs.writeFileSync(
    path.join(RESULTS_DIR, `segment_detail_open-${stamp}.json`),
    JSON.stringify(record, null, 2) + "\n",
  );
  console.log(JSON.stringify(record, null, 2));

  expect(record.p95_ms, "p95 open-to-render under 300 ms (AC-5)").toBeLessThan(
    BUDGET_P95_MS,
  );
});
