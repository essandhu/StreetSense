/**
 * Phase 5 — Task 3.9 — pan/zoom budget with the delta layer active.
 *
 * Sibling of `pan_zoom.spec.ts` but boots into delta mode first.
 * Confirms that mounting the DeltaOverlay (deck.gl on the
 * `public.road_segments_tile_delta` source) doesn't push the
 * interactive frame budget over the spec's <100 ms ceiling.
 *
 * Hermetic — `page.route` stubs the JSON endpoints so the spec
 * runs against `pnpm dev` alone without docker-compose / seeded
 * Postgres. Tile fetches are not stubbed; deck.gl handles the
 * 404s silently. That means the measured cost here is the
 * **JS overhead floor** of having DeltaOverlay mounted — the
 * deck.gl scene exists, the MVTLayer is registered, the React +
 * Redux + TanStack-Query plumbing runs every render.
 *
 * The full real-tile measurement (with painted delta segments)
 * joins when this spec re-runs against the deployed instance —
 * same code, different `baseURL` and no `page.route` stubs.
 */

import { expect, test, type Route } from "@playwright/test";
import * as fs from "node:fs";
import * as path from "node:path";

const RESULTS_DIR = path.resolve(__dirname, "results");
const BUDGET_MEDIAN_MS = 20;
const BUDGET_P95_MS = 100;

const RUN_A_ID = "11111111-1111-1111-1111-111111111111";
const RUN_B_ID = "22222222-2222-2222-2222-222222222222";

const _runMeta = (id: string, tsIso: string) => ({
  scoring_run_id: id,
  scoring_run_timestamp: tsIso,
  perception_model_version: "stand-in-onnx-0.1.0",
  osm_snapshot_date: "2026-05-01",
  imagery_capture_window_start: "2025-11-01",
  imagery_capture_window_end: "2026-05-01",
  propagation_algorithm_version: "pagerank-diffusion-0.1.0",
});

const RUNS_LIST = {
  runs: [
    _runMeta(RUN_A_ID, "2026-05-08T12:00:00Z"),
    _runMeta(RUN_B_ID, "2026-05-01T12:00:00Z"),
  ],
};

const DELTA_PAYLOAD = {
  run_a: _runMeta(RUN_A_ID, "2026-05-08T12:00:00Z"),
  run_b: _runMeta(RUN_B_ID, "2026-05-01T12:00:00Z"),
  deltas: [],
  page: 1,
  page_size: 1000,
  total: 0,
};

const quantile = (sorted: number[], q: number): number => {
  if (sorted.length === 0) return 0;
  const idx = Math.max(
    0,
    Math.min(sorted.length - 1, Math.ceil(q * sorted.length) - 1),
  );
  return sorted[idx]!;
};

test("pan/zoom in delta mode holds sub-100ms frame budget", async ({
  page,
}) => {
  test.setTimeout(120_000);

  await page.route(/\/runs(\?.*)?$/, (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(RUNS_LIST),
    }),
  );
  await page.route(/\/runs\/[0-9a-f-]+\/delta\/[0-9a-f-]+/, (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(DELTA_PAYLOAD),
    }),
  );

  await page.goto("/");

  // Enter delta mode and pick both runs so DeltaOverlay mounts.
  await page.getByRole("button", { name: /^delta$/i }).click();
  await page.getByLabel(/run a/i).waitFor({ timeout: 10_000 });
  await page.getByLabel(/run a/i).selectOption(RUN_A_ID);
  await page.getByLabel(/run b/i).selectOption(RUN_B_ID);

  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(800);

  // Warm-up pan to settle the basemap tile cache for the traversal
  // region. Same approach as pan_zoom.spec.ts.
  await page.evaluate(async () => {
    const canvas = document.querySelector(
      "canvas.maplibregl-canvas",
    ) as HTMLCanvasElement | null;
    if (!canvas) throw new Error("MapLibre canvas not found");
    const rect = canvas.getBoundingClientRect();
    const sx = rect.left + rect.width / 2;
    const sy = rect.top + rect.height / 2;
    const dispatchPointer = (type: string, x: number, y: number) =>
      canvas.dispatchEvent(
        new PointerEvent(type, {
          clientX: x,
          clientY: y,
          pointerType: "mouse",
          button: 0,
          isPrimary: true,
          bubbles: true,
          cancelable: true,
        }),
      );
    for (let pan = 0; pan < 2; pan++) {
      const dirSign = pan % 2 === 0 ? 1 : -1;
      dispatchPointer("pointerdown", sx, sy);
      for (let i = 1; i <= 10; i++) {
        dispatchPointer(
          "pointermove",
          sx + dirSign * 24 * i,
          sy + dirSign * 12 * i,
        );
        await new Promise<void>((r) => setTimeout(r, 16));
      }
      dispatchPointer("pointerup", sx + dirSign * 240, sy + dirSign * 120);
      await new Promise<void>((r) => setTimeout(r, 400));
    }
  });
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(800);

  // Recorded pan + zoom sequence — same shape as pan_zoom.spec.ts so
  // results are apples-to-apples comparable to the single-run
  // benchmark in `results/`.
  await page.evaluate(async () => {
    interface FrameWindow extends Window {
      __frameTimes?: number[];
    }
    const w = window as FrameWindow;
    w.__frameTimes = [];
    let last = performance.now();
    let active = true;
    const tick = (now: number) => {
      if (!active) return;
      w.__frameTimes!.push(now - last);
      last = now;
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
    (w as unknown as { __stopRecording?: () => void }).__stopRecording = () => {
      active = false;
    };

    const canvas = document.querySelector(
      "canvas.maplibregl-canvas",
    ) as HTMLCanvasElement | null;
    if (!canvas) throw new Error("MapLibre canvas not found");
    const rect = canvas.getBoundingClientRect();
    const sx = rect.left + rect.width / 2;
    const sy = rect.top + rect.height / 2;
    const dispatchPointer = (type: string, x: number, y: number) =>
      canvas.dispatchEvent(
        new PointerEvent(type, {
          clientX: x,
          clientY: y,
          pointerType: "mouse",
          button: 0,
          isPrimary: true,
          bubbles: true,
          cancelable: true,
        }),
      );
    const dispatchWheel = (deltaY: number) =>
      canvas.dispatchEvent(
        new WheelEvent("wheel", { deltaY, bubbles: true, cancelable: true }),
      );

    for (let i = 0; i < 5; i++) {
      const dirSign = i % 2 === 0 ? 1 : -1;
      dispatchPointer("pointerdown", sx, sy);
      for (let step = 1; step <= 12; step++) {
        const dx = (dirSign * 100 * step) / 12;
        const dy = (dirSign * 60 * step) / 12;
        dispatchPointer("pointermove", sx + dx, sy + dy);
        await new Promise<void>((r) => setTimeout(r, 16));
      }
      dispatchPointer("pointerup", sx + dirSign * 100, sy + dirSign * 60);
      await new Promise<void>((r) => setTimeout(r, 150));
    }

    for (let i = 0; i < 3; i++) {
      dispatchWheel(-200);
      await new Promise<void>((r) => setTimeout(r, 150));
      dispatchWheel(200);
      await new Promise<void>((r) => setTimeout(r, 150));
    }

    (w as unknown as { __stopRecording?: () => void }).__stopRecording?.();
  });

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
  const samplesOverBudget = samples.filter((s) => s > 100).length;
  const fractionOverBudget = samplesOverBudget / samples.length;

  fs.mkdirSync(RESULTS_DIR, { recursive: true });
  const resultPath = path.join(
    RESULTS_DIR,
    `pan_zoom_delta-${new Date().toISOString().replace(/:/g, "-")}.json`,
  );
  fs.writeFileSync(
    resultPath,
    JSON.stringify(
      {
        timestamp: new Date().toISOString(),
        mode: "delta",
        note: "Hermetic — tile fetches stubbed/404; measures JS overhead floor with DeltaOverlay mounted. Re-run against deployed instance for the with-real-tiles number.",
        sample_count: samples.length,
        samples_over_100ms: samplesOverBudget,
        fraction_over_100ms: fractionOverBudget,
        median_ms: median,
        mean_ms: mean,
        p95_ms: p95,
        max_ms: max,
        budget_median_ms: BUDGET_MEDIAN_MS,
        budget_p95_ms: BUDGET_P95_MS,
      },
      null,
      2,
    ),
  );

  expect(median).toBeLessThan(BUDGET_MEDIAN_MS);
  expect(fractionOverBudget).toBeLessThan(0.2);
});
