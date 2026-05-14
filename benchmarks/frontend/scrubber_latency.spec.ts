/**
 * Phase 2.5.9 — scrubber-to-render latency benchmark (Playwright).
 *
 * Scripts hourly increments to the scrubber and measures, for each
 * step, the elapsed time from the dispatch on the hour input to the
 * *first tile fetch that carries the new `t=` parameter* completing.
 * That's the deck.gl `MVTLayer.data` URL swap — the moment the new
 * tile bytes land in the GPU pipeline. Asserts:
 *
 *   p95 < 500 ms     (CLAUDE.md / spec.md AC-4)
 *
 * Result JSON written to benchmarks/frontend/results/phase-2/.
 *
 * Run:
 *   pnpm exec playwright test ./benchmarks/frontend/scrubber_latency.spec.ts
 */

import { expect, test } from "@playwright/test";
import * as fs from "node:fs";
import * as path from "node:path";

const RESULTS_DIR = path.resolve(__dirname, "results", "phase-2");
const BUDGET_P95_MS = 500;
const WARMUP_STEPS = 5;
const MEASURED_STEPS = 40;
const PER_STEP_TIMEOUT_MS = 5000;

const quantile = (sorted: number[], q: number): number => {
  if (sorted.length === 0) return 0;
  const idx = Math.max(0, Math.min(sorted.length - 1, Math.ceil(q * sorted.length) - 1));
  return sorted[idx]!;
};

const isoForHour = (hour: number): string =>
  `2025-03-21T${hour.toString().padStart(2, "0")}:00:00Z`;

test("scrubber-to-render latency holds p95 < 500 ms", async ({ page }) => {
  test.setTimeout(180_000);
  // The frontend defaults to dayOfYear=80 (= 2025-03-21) and hourOfDay=11.
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  // Wait for the first overlay tile fetch to settle so subsequent
  // scrubs measure incremental cost, not first-frame cost.
  await page.waitForTimeout(800);

  const samples: number[] = [];
  const totalSteps = WARMUP_STEPS + MEASURED_STEPS;

  for (let step = 0; step < totalSteps; step++) {
    // Cycle through hours that produce real scrubs (avoid the default
    // 11 to force a real URL change on the first iteration).
    const targetHour = (step + 1) % 24;
    const targetIsoFragment = encodeURIComponent(isoForHour(targetHour));

    // Wait for the first .pbf tile request whose URL contains the
    // target hour — that's the new MVTLayer fetching tiles for the
    // new `t`. The waiter is set up *before* the dispatch.
    const responsePromise = page.waitForResponse(
      (resp) => {
        const url = resp.url();
        return (
          url.includes("/tiles/public.road_segments_tile_t/") &&
          url.includes(".pbf") &&
          url.includes(targetIsoFragment)
        );
      },
      { timeout: PER_STEP_TIMEOUT_MS },
    );

    const t0 = Date.now();
    await page.evaluate((hour) => {
      const input = document.querySelector('input[aria-label="hour"]') as HTMLInputElement | null;
      if (!input) throw new Error('input[aria-label="hour"] not found');
      const proto = Object.getPrototypeOf(input);
      const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
      setter?.call(input, String(hour));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }, targetHour);

    let latency: number;
    try {
      await responsePromise;
      latency = Date.now() - t0;
    } catch {
      latency = PER_STEP_TIMEOUT_MS;
    }

    // First few iterations exercise the cold cache (tile bytes not yet
    // populated in pg_tileserv's process / Postgres shared buffers).
    // Skip them so the measured p95 reflects the steady-state scrubbing
    // experience — which is what the spec budget targets.
    if (step >= WARMUP_STEPS) {
      samples.push(latency);
    }

    // Let the layer settle before the next scrub.
    await page.waitForTimeout(120);
  }

  const sorted = [...samples].sort((a, b) => a - b);
  const median = quantile(sorted, 0.5);
  const p95 = quantile(sorted, 0.95);
  const p99 = quantile(sorted, 0.99);
  const max = sorted[sorted.length - 1] ?? 0;
  const mean = samples.reduce((a, b) => a + b, 0) / samples.length;

  fs.mkdirSync(RESULTS_DIR, { recursive: true });
  const resultPath = path.join(
    RESULTS_DIR,
    `scrubber_latency-${new Date().toISOString().replace(/:/g, "-")}.json`,
  );
  fs.writeFileSync(
    resultPath,
    JSON.stringify(
      {
        timestamp: new Date().toISOString(),
        sample_count: samples.length,
        samples_ms: samples,
        median_ms: median,
        mean_ms: mean,
        p95_ms: p95,
        p99_ms: p99,
        max_ms: max,
        budget_p95_ms: BUDGET_P95_MS,
      },
      null,
      2,
    ),
  );

  expect(p95).toBeLessThan(BUDGET_P95_MS);
});
