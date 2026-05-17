/**
 * Test for the glare tile source hook — Task 2.5.5 (test-first).
 *
 * The hook composes a tile URL from the Redux scrubber state + a fixed
 * reference year (the scoring run wrote 2025 timestamps). The URL
 * changes whenever the scrubber changes; the change is the signal the
 * deck.gl `MVTLayer` needs to swap tiles.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { configureStore } from "@reduxjs/toolkit";
import { renderHook, act, waitFor } from "@testing-library/react";
import { Provider } from "react-redux";
import { describe, expect, it } from "vitest";
import type { ReactNode } from "react";

import scrubberReducer, { setHourOfDay, setDayOfYear } from "../state/scrubber";

import { glareTileUrl, useGlareTileSource } from "./useGlareTileSource";

const renderWithProviders = (initial?: { hourOfDay?: number; dayOfYear?: number }) => {
  const store = configureStore({
    reducer: { scrubber: scrubberReducer },
    preloadedState: initial
      ? {
          scrubber: {
            hourOfDay: initial.hourOfDay ?? 11,
            dayOfYear: initial.dayOfYear ?? 80,
          },
        }
      : undefined,
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <Provider store={store}>
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    </Provider>
  );
  return {
    store,
    qc,
    ...renderHook(() => useGlareTileSource(), { wrapper }),
  };
};

describe("glareTileUrl", () => {
  it("builds a tile URL with a ?t= query param snapped to the chosen hour", () => {
    const url = glareTileUrl({ dayOfYear: 80, hourOfDay: 11 });
    expect(url).toContain("/tiles/public.road_segments_tile_t/{z}/{x}/{y}.pbf");
    expect(url).toMatch(/[?&]t=2025-03-21T11%3A00%3A00Z/);
  });

  it("encodes the colon in ISO timestamp", () => {
    const url = glareTileUrl({ dayOfYear: 172, hourOfDay: 17 });
    // 2025 day 172 = 2025-06-21 (summer solstice).
    expect(url).toMatch(/t=2025-06-21T17%3A00%3A00Z/);
  });

  it("clamps hour-of-day to [0, 23] defensively", () => {
    // The slice already clamps but the URL builder must also be safe
    // if called with raw state (e.g., during transitional values).
    const url = glareTileUrl({ dayOfYear: 80, hourOfDay: 25 });
    expect(url).toMatch(/T23%3A00%3A00Z/);
  });

  it("includes the default city_slug=cambridge (Phase 4b migration 0019)", () => {
    // pg_tileserv's road_segments_tile_t requires city_slug; an unknown
    // or missing slug returns an empty MVT (the SQL never raises), but
    // forgetting it in the URL would silently render nothing.
    const url = glareTileUrl({ dayOfYear: 80, hourOfDay: 11 });
    expect(url).toMatch(/[?&]city_slug=cambridge/);
  });

  it("honors an explicit city_slug override (forward compat for Phase 4 selector)", () => {
    const url = glareTileUrl({ dayOfYear: 80, hourOfDay: 11 }, "phoenix");
    expect(url).toMatch(/[?&]city_slug=phoenix/);
  });
});

describe("useGlareTileSource", () => {
  it("returns a tile URL based on initial scrubber state", async () => {
    const { result } = renderWithProviders({ dayOfYear: 80, hourOfDay: 11 });
    await waitFor(() => {
      expect(result.current.data).toBeDefined();
    });
    expect(result.current.data?.url).toMatch(/t=2025-03-21T11%3A00%3A00Z/);
    expect(result.current.data?.layer).toBe("public.road_segments_tile_t");
  });

  it("updates the tile URL when the scrubber hour changes", async () => {
    const { result, store } = renderWithProviders({ dayOfYear: 80, hourOfDay: 11 });
    await waitFor(() => expect(result.current.data?.url).toMatch(/T11%3A00%3A00Z/));
    act(() => {
      store.dispatch(setHourOfDay(14));
    });
    await waitFor(() => expect(result.current.data?.url).toMatch(/T14%3A00%3A00Z/));
  });

  it("updates the tile URL when the scrubber day-of-year changes", async () => {
    const { result, store } = renderWithProviders({ dayOfYear: 80, hourOfDay: 11 });
    await waitFor(() => expect(result.current.data).toBeDefined());
    act(() => {
      store.dispatch(setDayOfYear(172));
    });
    await waitFor(() => expect(result.current.data?.url).toMatch(/2025-06-21/));
    expect(result.current.data?.url).toMatch(/2025-06-21T11/);
  });
});
