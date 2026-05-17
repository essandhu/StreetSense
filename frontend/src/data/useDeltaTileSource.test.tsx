/**
 * Tests for useDeltaTileSource (Task 3.7, extended for Phase 4b
 * Task 4.3 city-keyed query cache).
 */
import { configureStore } from "@reduxjs/toolkit";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { Provider } from "react-redux";
import { describe, expect, it } from "vitest";

import { RunId } from "../domain";
import activeCity, { setActiveCity } from "../state/activeCity";
import { deltaTileUrl, useDeltaTileSource } from "./useDeltaTileSource";

const RUN_A = RunId("11111111-1111-1111-1111-111111111111");
const RUN_B = RunId("22222222-2222-2222-2222-222222222222");

function _makeStore(initialSlug = "cambridge") {
  const store = configureStore({ reducer: { activeCity } });
  if (initialSlug !== "cambridge") store.dispatch(setActiveCity(initialSlug));
  return store;
}

const _wrap = (
  qc: QueryClient,
  store = _makeStore(),
): (({ children }: { children: ReactNode }) => ReactNode) => {
  return ({ children }: { children: ReactNode }) => (
    <Provider store={store}>
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    </Provider>
  );
};

describe("deltaTileUrl — pure URL builder", () => {
  it("includes the delta tile layer name and the {z}/{x}/{y} template", () => {
    const url = deltaTileUrl(RUN_A, RUN_B);
    expect(url).toContain("public.road_segments_tile_delta");
    expect(url).toContain("/{z}/{x}/{y}.pbf");
  });

  it("URL-encodes both run UUIDs into query params", () => {
    const url = deltaTileUrl(RUN_A, RUN_B);
    expect(url).toContain(`run_a=${RUN_A}`);
    expect(url).toContain(`run_b=${RUN_B}`);
  });

  it("includes the default city_slug=cambridge (Phase 4b migration 0019)", () => {
    // pg_tileserv's road_segments_tile_delta requires city_slug; an
    // unknown slug yields an empty MVT, but a missing one is a SQL
    // arity error.
    const url = deltaTileUrl(RUN_A, RUN_B);
    expect(url).toMatch(/[?&]city_slug=cambridge/);
  });

  it("honors an explicit city_slug override (Phase 4 selector forward compat)", () => {
    const url = deltaTileUrl(RUN_A, RUN_B, "phoenix");
    expect(url).toMatch(/[?&]city_slug=phoenix/);
  });
});

describe("useDeltaTileSource", () => {
  it("returns the URL bundle when both runs are set and distinct", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => useDeltaTileSource(RUN_A, RUN_B), {
      wrapper: _wrap(qc),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.url).toContain("public.road_segments_tile_delta");
    expect(result.current.data?.runA).toBe(RUN_A);
    expect(result.current.data?.runB).toBe(RUN_B);
  });

  it("is disabled when either run is null", () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => useDeltaTileSource(RUN_A, null), {
      wrapper: _wrap(qc),
    });
    expect(result.current.fetchStatus).toBe("idle");
  });

  it("is disabled when both runs are equal", () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => useDeltaTileSource(RUN_A, RUN_A), {
      wrapper: _wrap(qc),
    });
    expect(result.current.fetchStatus).toBe("idle");
  });

  it("re-keys on swap (RUN_A, RUN_B) vs (RUN_B, RUN_A)", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = _wrap(qc);
    const a = renderHook(() => useDeltaTileSource(RUN_A, RUN_B), { wrapper });
    await waitFor(() => expect(a.result.current.isSuccess).toBe(true));
    const urlA = a.result.current.data?.url ?? "";

    const b = renderHook(() => useDeltaTileSource(RUN_B, RUN_A), { wrapper });
    await waitFor(() => expect(b.result.current.isSuccess).toBe(true));
    const urlB = b.result.current.data?.url ?? "";

    expect(urlA).not.toBe(urlB);
  });

  it("rebinds to the new city slug on setActiveCity (Task 4.3)", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const store = _makeStore("cambridge");
    const wrapper = _wrap(qc, store);

    const { result } = renderHook(() => useDeltaTileSource(RUN_A, RUN_B), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.url).toMatch(/[?&]city_slug=cambridge/);

    act(() => {
      store.dispatch(setActiveCity("phoenix"));
    });
    await waitFor(() =>
      expect(result.current.data?.url).toMatch(/[?&]city_slug=phoenix/),
    );
  });
});
