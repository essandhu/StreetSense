/**
 * Tests for `useRuns` (Task 3.3 backend prep — frontend half).
 *
 * Lists every scoring run from `GET /runs`. Sourced by the RunPicker
 * dropdowns (Task 3.3) — never put this in Redux.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type RunListResponse } from "../domain";
import { useRuns } from "./useRuns";

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

const _PAYLOAD: RunListResponse = {
  runs: [
    _runMeta("11111111-1111-1111-1111-111111111111", "2026-05-08T12:00:00Z"),
    _runMeta("22222222-2222-2222-2222-222222222222", "2026-05-01T12:00:00Z"),
  ],
} as RunListResponse;

function _wrap(qc: QueryClient): ({ children }: { children: ReactNode }) => ReactNode {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

let fetchSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchSpy = vi.fn(async () => {
    return new Response(JSON.stringify(_PAYLOAD), {
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

describe("useRuns", () => {
  it("fetches /runs and returns the typed response", async () => {
    const qc = _newClient();
    const { result } = renderHook(() => useRuns(), { wrapper: _wrap(qc) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.runs).toHaveLength(2);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const url = String(fetchSpy.mock.calls[0]?.[0] ?? "");
    expect(url).toMatch(/\/runs$/);
  });

  it("returns runs in the order the server provided", async () => {
    const qc = _newClient();
    const { result } = renderHook(() => useRuns(), { wrapper: _wrap(qc) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.runs[0]?.scoring_run_timestamp).toBe("2026-05-08T12:00:00Z");
    expect(result.current.data?.runs[1]?.scoring_run_timestamp).toBe("2026-05-01T12:00:00Z");
  });

  it("two mounts share one fetch (caching keyed on the static list)", async () => {
    const qc = _newClient();
    const wrapper = _wrap(qc);
    const a = renderHook(() => useRuns(), { wrapper });
    await waitFor(() => expect(a.result.current.isSuccess).toBe(true));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const b = renderHook(() => useRuns(), { wrapper });
    await waitFor(() => expect(b.result.current.isSuccess).toBe(true));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });
});
