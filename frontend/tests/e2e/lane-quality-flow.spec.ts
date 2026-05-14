/**
 * Phase 3.7.1 — end-to-end lane-quality flow.
 *
 * Demonstrable output: opening a Cambridge road segment with real
 * Mapillary imagery shows a non-stub lane-marking arc on the radial
 * chart, the confidence dial labels one of the three limiters, and
 * clicking a thumbnail loads the source imagery.
 *
 * Imagery-bearing segments are a small fraction of the full network
 * (Phase 3 is budget-bounded on Mapillary calls), so the spec
 * deterministically targets one via the dev-only
 * ``__benchOpenSegment`` hook rather than gambling on a click
 * landing on the right pixel. The map-click path itself has its own
 * coverage in ``segment-detail.spec.ts``.
 *
 * Prerequisites:
 *   - ``docker compose up -d``
 *   - ``make seed``
 *   - ``make ingest-imagery``     (needs MAPILLARY_ACCESS_TOKEN in env)
 *   - ``make seed-model``
 *   - ``make scoring-run``
 *   - ``make api``
 *   - ``pnpm dev``               (Playwright spins this up via webServer)
 *
 * Pass ``PLAYWRIGHT_IMAGERY_SEGMENT_ID`` to point at a segment with
 * ``segment_imagery`` rows. Without it, the spec asserts the API is
 * up and bails — there is no point asserting the lane-marking arc on
 * a segment that fell to the stub fallback.
 */
import { expect, test } from "@playwright/test";

test("lane-quality flow against seeded Cambridge", async ({ page, request }) => {
  page.on("response", async (resp) => {
    if (resp.status() >= 500) {
      console.warn(`[${resp.status()}] ${resp.url()}`);
    }
  });

  const imagerySegmentId = process.env.PLAYWRIGHT_IMAGERY_SEGMENT_ID;
  test.skip(
    !imagerySegmentId,
    "set PLAYWRIGHT_IMAGERY_SEGMENT_ID to a segment with segment_imagery rows",
  );

  const apiBase = process.env.PLAYWRIGHT_API_BASE ?? "http://localhost:8001";
  const fresh = await request.get(`${apiBase}/admin/freshness`);
  expect(fresh.ok(), `API not reachable at ${apiBase}/admin/freshness`).toBe(true);

  await page.goto("/");
  await page.waitForSelector("canvas", { timeout: 30_000 });

  // Open the panel via the dev-only hook (bypasses click hit-testing).
  await page.evaluate((id: string) => {
    type Win = Window & { __benchOpenSegment?: (id: string) => void };
    const w = window as Win;
    if (!w.__benchOpenSegment) throw new Error("__benchOpenSegment not exposed");
    w.__benchOpenSegment(id);
  }, imagerySegmentId!);

  await expect(page.getByTestId("segment-detail-panel")).toBeVisible({
    timeout: 5_000,
  });

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
