/**
 * Phase 5 — Task 3.8 — delta-view E2E flow.
 *
 * Exercises the JS wiring of the delta mode end-to-end against a
 * fully-stubbed API. Playwright's `page.route` intercepts every
 * JSON call (runs list, delta payload, segment detail) and returns
 * deterministic fixtures, so:
 *
 * - The spec runs against `pnpm dev` alone — no docker-compose, no
 *   pg_tileserv, no seeded Postgres, no risk of wiping live
 *   ingestion data (per the memory note about DB-gated tests).
 * - Re-running against the eventually-deployed instance is a config
 *   change: drop the route handlers, point `baseURL` at the live URL.
 *
 * Limitation: tile fetches are not stubbed — pg_tileserv isn't
 * available locally without docker-compose. deck.gl handles the
 * 404s silently. That means the "delta layer renders segments"
 * visual check from plan §3.8 turns into a "deck.gl canvas mounts
 * and DeltaOverlay is wired" structural check here. The real
 * paint-rendering check joins when Task 3.8 re-runs against the
 * deployed instance.
 */
import { expect, test, type Route } from "@playwright/test";

const RUN_A_ID = "11111111-1111-1111-1111-111111111111";
const RUN_B_ID = "22222222-2222-2222-2222-222222222222";

function _runMeta(id: string, tsIso: string) {
  return {
    scoring_run_id: id,
    scoring_run_timestamp: tsIso,
    perception_model_version: "stand-in-onnx-0.1.0",
    osm_snapshot_date: "2026-05-01",
    imagery_capture_window_start: "2025-11-01",
    imagery_capture_window_end: "2026-05-01",
    propagation_algorithm_version: "pagerank-diffusion-0.1.0",
  };
}

const RUNS_LIST = {
  runs: [_runMeta(RUN_A_ID, "2026-05-08T12:00:00Z"), _runMeta(RUN_B_ID, "2026-05-01T12:00:00Z")],
};

function _row(idHex: string, composite: number) {
  return {
    segment_id: `${idHex.padEnd(8, "0")}-0000-0000-0000-000000000000`,
    composite_delta: composite,
    local_contribution_delta: composite * 0.5,
    propagation_uplift_delta: composite * 0.5,
    sub_score_deltas: {
      lane_marking_quality: 0,
      glare_exposure: 0,
      junction_complexity: 0,
      historical_correlation: 0,
    },
    confidence_a: { value: 0.8, limiter: "model" },
    confidence_b: { value: 0.85, limiter: "model" },
  };
}

const DELTA_PAYLOAD = {
  run_a: _runMeta(RUN_A_ID, "2026-05-08T12:00:00Z"),
  run_b: _runMeta(RUN_B_ID, "2026-05-01T12:00:00Z"),
  deltas: [
    _row("aabbccdd", 0.42),
    _row("11223344", -0.31),
    _row("55667788", 0.18),
    _row("99aabbcc", -0.07),
    _row("ddeeff00", 0.03),
  ],
  page: 1,
  page_size: 1000,
  total: 5,
};

function _segmentDetailStub(segmentId: string) {
  return {
    segment_id: segmentId,
    osm_way_id: 12345,
    composite_risk: 0.55,
    local_contribution: 0.32,
    propagation_uplift: 0.23,
    propagation_algorithm: { name: "pagerank-diffusion", version: "0.1.0" },
    sub_scores: {
      glare_exposure: { value: 0.5, confidence: 0.8, is_stub: false, metadata: {} },
      lane_marking_quality: { value: 0.4, confidence: 0.7, is_stub: false, metadata: {} },
      junction_complexity: { value: 0.3, confidence: 0.7, is_stub: false, metadata: {} },
      historical_correlation: { value: 0.2, confidence: 0.6, is_stub: false, metadata: {} },
    },
    confidence: { value: 0.6, limiter: "freshness" },
    imagery: [],
    attrs: { highway: "primary" },
  };
}

test.describe("Delta view E2E flow", () => {
  test.beforeEach(async ({ page }) => {
    // Stub every API JSON call the frontend may make. Tile fetches
    // are intentionally not stubbed — pg_tileserv isn't running in
    // this hermetic test and deck.gl handles the 404s.
    await page.route(/\/runs(\?.*)?$/, (route: Route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(RUNS_LIST),
      })
    );
    await page.route(/\/runs\/[0-9a-f-]+\/delta\/[0-9a-f-]+/, (route: Route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(DELTA_PAYLOAD),
      })
    );
    await page.route(/\/segments\/[0-9a-f-]+/, (route: Route) => {
      const url = route.request().url();
      const match = url.match(/segments\/([0-9a-f-]+)/);
      const segmentId = match?.[1] ?? "00000000-0000-0000-0000-000000000000";
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(_segmentDetailStub(segmentId)),
      });
    });
    await page.route(/\/admin\/freshness/, (route: Route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ sources: [], server_time: new Date().toISOString() }),
      })
    );
  });

  test("enter delta mode → pick two runs → list + histogram render → row click opens panel", async ({
    page,
  }) => {
    await page.goto("/");

    // Single-run map is the default. Enter delta mode via the toggle.
    await page.getByRole("button", { name: /^delta$/i }).click();

    // DeltaView mounts — RunPicker appears.
    await expect(page.getByLabel(/run a/i)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByLabel(/run b/i)).toBeVisible();

    // Pick both runs.
    await page.getByLabel(/run a/i).selectOption(RUN_A_ID);
    await page.getByLabel(/run b/i).selectOption(RUN_B_ID);

    // Histogram renders 20 bars after the delta payload loads.
    const histogram = page.getByTestId("delta-histogram");
    await expect(histogram).toBeVisible();
    const bars = histogram.locator("rect.bar");
    await expect(bars).toHaveCount(20);

    // LargestChangesList renders five rows (matches DELTA_PAYLOAD.deltas).
    const list = page.getByTestId("largest-changes-list");
    await expect(list).toBeVisible();
    const rows = list.locator("button.row");
    await expect(rows).toHaveCount(5);

    // First row is the largest-magnitude change — aabbccdd at composite_delta=0.42.
    const firstRow = rows.first();
    await expect(firstRow).toHaveAttribute(
      "data-segment-id",
      "aabbccdd-0000-0000-0000-000000000000"
    );

    // Click the first row → opens SegmentDetailPanel (existing Phase 3 affordance).
    await firstRow.click();
    await expect(page.getByTestId("segment-detail-panel")).toBeVisible({
      timeout: 5_000,
    });
  });

  test("ModeToggle round-trip: delta → single clears the run pair", async ({ page }) => {
    await page.goto("/");

    await page.getByRole("button", { name: /^delta$/i }).click();
    await page.getByLabel(/run a/i).selectOption(RUN_A_ID);
    await page.getByLabel(/run b/i).selectOption(RUN_B_ID);
    await expect(page.getByLabel(/run a/i)).toHaveValue(RUN_A_ID);

    // Switch back to single-run mode. Delta slice clears the run pair
    // by design (anti-stale rule from Task 3.1) — re-entering delta
    // mode should not silently resurrect the pair.
    await page.getByRole("button", { name: /single/i }).click();

    // RunPicker is no longer mounted (delta view unmounts).
    await expect(page.getByLabel(/run a/i)).toBeHidden();

    // Re-enter delta mode → picker mounts back with both dropdowns empty.
    await page.getByRole("button", { name: /^delta$/i }).click();
    await expect(page.getByLabel(/run a/i)).toBeVisible();
    await expect(page.getByLabel(/run a/i)).toHaveValue("");
    await expect(page.getByLabel(/run b/i)).toHaveValue("");
  });
});
