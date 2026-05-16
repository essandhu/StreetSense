/**
 * Tests for `useDelta` (Task 3.2).
 *
 * Mirrors the testing style of `useSegmentDetail.test.tsx`: TanStack
 * Query + a fetch shim, no MSW. Covers:
 *   - Returns the typed DeltaResponse on success.
 *   - Disabled when either run is null.
 *   - Disabled when runA === runB (the API would 422; cheaper to short-circuit).
 *   - Cache key is the (runA, runB) pair — second mount with the same pair
 *     hits cache, second mount with swapped pair re-fetches (different URL).
 *   - Requests the maximum allowed page size so a city-scale delta arrives
 *     in one round-trip (the LargestChangesList virtualizes >200 rows and
 *     the histogram needs the full distribution).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RunId, type DeltaResponse } from "../domain";
import { useDelta } from "./useDelta";

const RUN_A = RunId("11111111-1111-1111-1111-111111111111");
const RUN_B = RunId("22222222-2222-2222-2222-222222222222");

function _payload(runA: string, runB: string): DeltaResponse {
  const _runMeta = (id: string) => ({
    scoring_run_id: id,
    scoring_run_timestamp: "2026-05-01T12:00:00Z",
    perception_model_version: "v1",
    osm_snapshot_date: "2026-05-01",
    imagery_capture_window_start: "2026-04-01T00:00:00Z",
    imagery_capture_window_end: "2026-05-01T00:00:00Z",
    propagation_algorithm_version: "pagerank-diffusion-0.1.0",
  });
  return {
    run_a: _runMeta(runA),
    run_b: _runMeta(runB),
    deltas: [],
    page: 1,
    page_size: 1000,
    total: 0,
  } as unknown as DeltaResponse;
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
    const match = url.match(/runs\/([0-9a-f-]+)\/delta\/([0-9a-f-]+)/);
    const a = match?.[1] ?? "unknown-a";
    const b = match?.[2] ?? "unknown-b";
    return new Response(JSON.stringify(_payload(a, b)), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchSpy);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const _newClient = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

describe("useDelta — success path", () => {
  it("returns the typed response when both runs are set", async () => {
    const qc = _newClient();
    const { result } = renderHook(() => useDelta(RUN_A, RUN_B), {
      wrapper: _wrap(qc),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.run_a.scoring_run_id).toBe(RUN_A);
    expect(result.current.data?.run_b.scoring_run_id).toBe(RUN_B);
    expect(result.current.data?.deltas).toEqual([]);
  });

  it("requests page_size=1000 so the page-1 response covers a city", async () => {
    const qc = _newClient();
    renderHook(() => useDelta(RUN_A, RUN_B), { wrapper: _wrap(qc) });
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    const url = String(fetchSpy.mock.calls[0]?.[0] ?? "");
    expect(url).toContain(`/runs/${RUN_A}/delta/${RUN_B}`);
    expect(url).toMatch(/[?&]page_size=1000\b/);
  });
});

describe("useDelta — disabled states", () => {
  it("is disabled when runA is null", () => {
    const qc = _newClient();
    const { result } = renderHook(() => useDelta(null, RUN_B), {
      wrapper: _wrap(qc),
    });
    expect(result.current.fetchStatus).toBe("idle");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("is disabled when runB is null", () => {
    const qc = _newClient();
    const { result } = renderHook(() => useDelta(RUN_A, null), {
      wrapper: _wrap(qc),
    });
    expect(result.current.fetchStatus).toBe("idle");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("is disabled when both runs are null", () => {
    const qc = _newClient();
    const { result } = renderHook(() => useDelta(null, null), {
      wrapper: _wrap(qc),
    });
    expect(result.current.fetchStatus).toBe("idle");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("is disabled when runA === runB (avoids guaranteed 422)", () => {
    const qc = _newClient();
    const { result } = renderHook(() => useDelta(RUN_A, RUN_A), {
      wrapper: _wrap(qc),
    });
    expect(result.current.fetchStatus).toBe("idle");
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("useDelta — caching", () => {
  it("two mounts with the same pair share a single fetch", async () => {
    const qc = _newClient();
    const wrapper = _wrap(qc);
    const a = renderHook(() => useDelta(RUN_A, RUN_B), { wrapper });
    await waitFor(() => expect(a.result.current.isSuccess).toBe(true));
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    const b = renderHook(() => useDelta(RUN_A, RUN_B), { wrapper });
    await waitFor(() => expect(b.result.current.isSuccess).toBe(true));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("swapping runA and runB re-fetches (different URL)", async () => {
    const qc = _newClient();
    const wrapper = _wrap(qc);
    const a = renderHook(() => useDelta(RUN_A, RUN_B), { wrapper });
    await waitFor(() => expect(a.result.current.isSuccess).toBe(true));
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    const b = renderHook(() => useDelta(RUN_B, RUN_A), { wrapper });
    await waitFor(() => expect(b.result.current.isSuccess).toBe(true));
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });
});
