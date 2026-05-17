/**
 * Tests for `useRuns` (Task 3.3 backend prep — frontend half).
 *
 * Lists every scoring run from `GET /runs`. Sourced by the RunPicker
 * dropdowns (Task 3.3) — never put this in Redux.
 */
import { configureStore } from "@reduxjs/toolkit";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { Provider } from "react-redux";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type RunListResponse } from "../domain";
import activeCity, { setActiveCity } from "../state/activeCity";
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

function _makeStore(initialSlug = "cambridge") {
  const store = configureStore({ reducer: { activeCity } });
  if (initialSlug !== "cambridge") store.dispatch(setActiveCity(initialSlug));
  return store;
}

function _wrap(
  qc: QueryClient,
  store = _makeStore(),
): ({ children }: { children: ReactNode }) => ReactNode {
  return ({ children }: { children: ReactNode }) => (
    <Provider store={store}>
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    </Provider>
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
  it("fetches the city-scoped path /api/cities/{slug}/runs", async () => {
    const qc = _newClient();
    const { result } = renderHook(() => useRuns(), { wrapper: _wrap(qc) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.runs).toHaveLength(2);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const url = String(fetchSpy.mock.calls[0]?.[0] ?? "");
    expect(url).toMatch(/\/api\/cities\/cambridge\/runs$/);
  });

  it("returns runs in the order the server provided", async () => {
    const qc = _newClient();
    const { result } = renderHook(() => useRuns(), { wrapper: _wrap(qc) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.runs[0]?.scoring_run_timestamp).toBe("2026-05-08T12:00:00Z");
    expect(result.current.data?.runs[1]?.scoring_run_timestamp).toBe("2026-05-01T12:00:00Z");
  });

  it("two mounts under the same city share one fetch", async () => {
    const qc = _newClient();
    const wrapper = _wrap(qc);
    const a = renderHook(() => useRuns(), { wrapper });
    await waitFor(() => expect(a.result.current.isSuccess).toBe(true));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const b = renderHook(() => useRuns(), { wrapper });
    await waitFor(() => expect(b.result.current.isSuccess).toBe(true));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("switching cities re-fetches and hits the new slug's path", async () => {
    // Task 4.3 invariant: a city switch invalidates the previous
    // city's run list. Each city has its own scoring history; reusing
    // cambridge's runs in the picker after switching to phoenix
    // would be a correctness bug, not just a UX nit.
    const qc = _newClient();
    const store = _makeStore("cambridge");
    const wrapper = _wrap(qc, store);

    const { result } = renderHook(() => useRuns(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(String(fetchSpy.mock.calls[0]?.[0] ?? "")).toContain(
      "/api/cities/cambridge/runs",
    );

    act(() => {
      store.dispatch(setActiveCity("phoenix"));
    });
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2));
    expect(String(fetchSpy.mock.calls[1]?.[0] ?? "")).toContain(
      "/api/cities/phoenix/runs",
    );
  });
});
