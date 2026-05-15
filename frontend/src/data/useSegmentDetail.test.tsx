/**
 * Tests for `useSegmentDetail` (Task 3.6.9).
 *
 * Uses TanStack Query's testing utilities + a fetch shim — no MSW
 * dependency. Covers:
 *   - Caches per (segmentId, hour) pair so two scrubber positions in
 *     the same UTC hour share a single network call.
 *   - Returns the typed response shape.
 *   - Disabled when segmentId is null.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SegmentId, type SegmentDetail } from "../domain";
import { useSegmentDetail } from "./useSegmentDetail";

const _SEGMENT_ID = SegmentId("12345678-1234-5678-1234-567812345678");

function _payload(id: string): SegmentDetail {
  return {
    segment_id: id,
    osm_way_id: 999,
    composite_risk: 0.42,
    // Phase 4 (API 4.0) — split composite into local + uplift with
    // the propagator identity. The test fixture treats this segment
    // as a real Phase 4 row produced by pagerank-diffusion.
    local_contribution: 0.3,
    propagation_uplift: 0.12,
    propagation_algorithm: { name: "pagerank-diffusion", version: "0.1.0" },
    sub_scores: {
      glare_exposure: { value: 0.3, confidence: 0.8, is_stub: false, metadata: {} },
      lane_marking_quality: { value: 0.5, confidence: 0.7, is_stub: false, metadata: {} },
      junction_complexity: { value: 0, confidence: 0, is_stub: true, metadata: {} },
      historical_correlation: { value: 0, confidence: 0, is_stub: true, metadata: {} },
    },
    confidence: { value: 0.7, limiter: "freshness" },
    imagery: [],
    attrs: {},
  } as SegmentDetail;
}

function _wrap(qc: QueryClient): ({ children }: { children: ReactNode }) => ReactNode {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

let fetchSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchSpy = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    const segMatch = url.match(/segments\/([0-9a-f-]+)/);
    const id = segMatch?.[1] ?? "unknown";
    return new Response(JSON.stringify(_payload(id)), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchSpy);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useSegmentDetail", () => {
  it("returns the typed response when segmentId is set", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => useSegmentDetail(_SEGMENT_ID, null), {
      wrapper: _wrap(qc),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.segment_id).toBe(_SEGMENT_ID);
    expect(result.current.data?.confidence.limiter).toBe("freshness");
  });

  it("is disabled when segmentId is null", () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => useSegmentDetail(null, null), {
      wrapper: _wrap(qc),
    });
    expect(result.current.fetchStatus).toBe("idle");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("caches per (segmentId, hour) — two times in same hour share one fetch", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = _wrap(qc);

    const t1 = new Date(Date.UTC(2025, 5, 21, 16, 5));
    const t2 = new Date(Date.UTC(2025, 5, 21, 16, 55));
    const t3 = new Date(Date.UTC(2025, 5, 21, 17, 5));

    const a = renderHook(() => useSegmentDetail(_SEGMENT_ID, t1), { wrapper });
    await waitFor(() => expect(a.result.current.isSuccess).toBe(true));
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    // Same hour → cache hit.
    const b = renderHook(() => useSegmentDetail(_SEGMENT_ID, t2), { wrapper });
    await waitFor(() => expect(b.result.current.isSuccess).toBe(true));
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    // Different hour → new fetch.
    const c = renderHook(() => useSegmentDetail(_SEGMENT_ID, t3), { wrapper });
    await waitFor(() => expect(c.result.current.isSuccess).toBe(true));
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });
});
