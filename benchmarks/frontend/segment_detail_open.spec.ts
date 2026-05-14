/**
 * Phase 3.6.15 — segment-detail open-to-render benchmark.
 *
 * Measures elapsed time from the Redux `openSegment` dispatch to the
 * first frame in which the radial chart's first `<path>` is visible.
 * Bypasses MapLibre click hit-testing so the measurement is the
 * panel/chart render path, not the laptop's rendered road density.
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
const PER_STEP_TIMEOUT_MS = 5_000;

const quantile = (sorted: number[], q: number): number => {
  if (sorted.length === 0) return 0;
  const idx = Math.max(0, Math.min(sorted.length - 1, Math.ceil(q * sorted.length) - 1));
  return sorted[idx]!;
};

test.setTimeout(120_000);

test("segment-detail open-to-render p95 < 300 ms", async ({ page, request }) => {
  // Fetch a few segment ids from the API so we can pretend to "click" them.
  const apiBase =
    process.env.VITE_API_BASE_URL ?? process.env.PLAYWRIGHT_API_BASE ?? "http://127.0.0.1:8001";
  // We don't have an index endpoint — but pg_tileserv exposes
  // `road_segments_tile_t_rows` and our DB has 36k seeded segments. The
  // simplest path: hit /admin/freshness to confirm the API is up, then
  // pull a handful of seed UUIDs from a known query path. Since the
  // benchmark only needs a list of valid ids, we sample from a fixture
  // file written by the harness if present, otherwise generate one
  // segment id and reuse it.
  const freshness = await request.get(`${apiBase}/admin/freshness`);
  expect(freshness.ok()).toBe(true);

  const idsFromFile = process.env.PLAYWRIGHT_SEGMENT_IDS;
  let segmentIds: string[] = [];
  if (idsFromFile && fs.existsSync(idsFromFile)) {
    segmentIds = fs
      .readFileSync(idsFromFile, "utf-8")
      .split(/\r?\n/)
      .map((l) => l.trim())
      .filter(Boolean);
  }
  if (segmentIds.length === 0) {
    // Use one UUID we hand-extract from a tile probe. Cheap: hit a
    // segment-detail endpoint with a known random id, follow until one
    // returns 200.
    for (let attempt = 0; attempt < 20; attempt += 1) {
      const probeId = crypto.randomUUID();
      const probe = await request.get(`${apiBase}/segments/${probeId}`);
      if (probe.ok()) {
        segmentIds.push(probeId);
        break;
      }
    }
  }
  expect(segmentIds.length, "no segment ids available to benchmark").toBeGreaterThan(0);

  await page.goto("/");
  await page.waitForSelector("canvas", { timeout: 20_000 });

  const samples: number[] = [];
  const totalSteps = WARMUP_STEPS + MEASURED_STEPS;

  for (let i = 0; i < totalSteps; i += 1) {
    const segId = segmentIds[i % segmentIds.length]!;

    // Close any previously-open panel so we always measure a cold open.
    await page.evaluate(() => {
      const btn = document.querySelector(
        '[data-testid="segment-detail-close"]',
      ) as HTMLButtonElement | null;
      btn?.click();
    });

    // Dispatch the open action via the Redux store. The store is
    // exposed in dev mode via `window.__store` (added below if not
    // present). For this benchmark we go through the same code path
    // by calling `store.dispatch(openSegment(...))` from inside
    // `evaluate`.
    const elapsed = await page.evaluate(
      async ([id, timeoutMs]) => {
        type Win = Window & {
          __benchOpenSegment?: (id: string) => void;
        };
        const w = window as Win;
        if (!w.__benchOpenSegment) {
          throw new Error(
            "window.__benchOpenSegment not exposed; instrument the app or use a different harness",
          );
        }
        const t0 = performance.now();
        w.__benchOpenSegment(id);

        // Wait for the chart's first arc path.
        const deadline = t0 + (timeoutMs as number);
        // eslint-disable-next-line no-constant-condition
        while (true) {
          const found = document.querySelector(
            '[data-testid="segment-detail-panel"] [data-arc-name]',
          );
          if (found) return performance.now() - t0;
          if (performance.now() > deadline) {
            throw new Error(`Arc not rendered within ${timeoutMs}ms`);
          }
          await new Promise((r) => requestAnimationFrame(r));
        }
      },
      [segId, PER_STEP_TIMEOUT_MS],
    );

    if (i >= WARMUP_STEPS) samples.push(elapsed);
  }

  samples.sort((a, b) => a - b);
  const record = {
    measured_steps: samples.length,
    p50_ms: Math.round(quantile(samples, 0.5)),
    p95_ms: Math.round(quantile(samples, 0.95)),
    p99_ms: Math.round(quantile(samples, 0.99)),
  };

  fs.mkdirSync(RESULTS_DIR, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  fs.writeFileSync(
    path.join(RESULTS_DIR, `segment_detail_open-${stamp}.json`),
    JSON.stringify(record, null, 2) + "\n",
  );
  console.log(JSON.stringify(record, null, 2));

  expect(record.p95_ms, "p95 open-to-render under 300 ms (AC-5)").toBeLessThan(BUDGET_P95_MS);
});
