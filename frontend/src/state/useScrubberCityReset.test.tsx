/**
 * Tests for `utcHourOfLocalNoon` + `useScrubberCityReset` —
 * Phase 4b Task 4.7.
 *
 * The pure function is exercised against several timezones
 * including DST and half-hour offsets. The hook is exercised
 * against a Redux store + a useCities QueryClient prefill.
 */
import { configureStore } from "@reduxjs/toolkit";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { type ReactNode } from "react";
import { Provider } from "react-redux";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { citiesQueryKey } from "../data/useCities";
import type { CityListResponse } from "../domain";

import activeCity, { setActiveCity } from "./activeCity";
import scrubber from "./scrubber";
import { useScrubberCityReset, utcHourOfLocalNoon } from "./useScrubberCityReset";

// ---------------------------------------------------------------------------
// utcHourOfLocalNoon — pure function
// ---------------------------------------------------------------------------

describe("utcHourOfLocalNoon", () => {
  // A summer date so the DST-observing zones land on their summer
  // offset; a winter date for the DST/non-DST diff check.
  const SUMMER = new Date("2026-07-15T00:00:00Z");
  const WINTER = new Date("2026-01-15T00:00:00Z");

  it("Phoenix (UTC-7, no DST) → 19:00 UTC year-round", () => {
    expect(utcHourOfLocalNoon("America/Phoenix", SUMMER)).toBe(19);
    expect(utcHourOfLocalNoon("America/Phoenix", WINTER)).toBe(19);
  });

  it("New York observes DST: 16:00 UTC in summer, 17:00 UTC in winter", () => {
    expect(utcHourOfLocalNoon("America/New_York", SUMMER)).toBe(16);
    expect(utcHourOfLocalNoon("America/New_York", WINTER)).toBe(17);
  });

  it("UTC itself → 12", () => {
    expect(utcHourOfLocalNoon("UTC", SUMMER)).toBe(12);
  });

  it("India (UTC+5:30) rounds to nearest UTC hour (6 or 7)", () => {
    // Local noon in IST = 06:30 UTC. Math.round(12 - 5.5) = 7.
    // Asserting the specific result pins the rounding direction; if
    // a future refactor switches to floor/ceil, this test will flag
    // it.
    expect(utcHourOfLocalNoon("Asia/Kolkata", SUMMER)).toBe(7);
  });

  it("falls back to 12 when the timezone string is malformed", () => {
    expect(utcHourOfLocalNoon("not/a-real-zone", SUMMER)).toBe(12);
  });
});

// ---------------------------------------------------------------------------
// useScrubberCityReset — hook
// ---------------------------------------------------------------------------

const _CITIES: CityListResponse = {
  cities: [
    {
      id: "00000000-0000-0000-0000-000000000001",
      slug: "cambridge",
      name: "Cambridge, MA",
      bbox: [-71.16, 42.35, -71.07, 42.41],
      default_zoom: 12,
      timezone: "America/New_York",
    },
    {
      id: "00000000-0000-0000-0000-000000000002",
      slug: "phoenix",
      name: "Phoenix, AZ",
      bbox: [-112.32, 33.29, -111.93, 33.92],
      default_zoom: 11,
      timezone: "America/Phoenix",
    },
  ],
};

function _makeStore(initialSlug = "cambridge") {
  const store = configureStore({ reducer: { scrubber, activeCity } });
  if (initialSlug !== "cambridge") store.dispatch(setActiveCity(initialSlug));
  return store;
}

function _makeClient(payload: CityListResponse = _CITIES) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(citiesQueryKey(), payload);
  return qc;
}

function _makeEmptyClient() {
  // No prefill — the queryFn would normally fire (and hit a real
  // fetch); the test stubs fetch above to a never-resolving promise
  // so the query stays in `isPending` indefinitely.
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function _wrap(
  store: ReturnType<typeof _makeStore>,
  qc: QueryClient,
): ({ children }: { children: ReactNode }) => ReactNode {
  return ({ children }: { children: ReactNode }) => (
    <Provider store={store}>
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    </Provider>
  );
}

// All hook tests prefill the cities cache directly via
// queryClient.setQueryData so the queryFn (which would hit a real
// fetch) never runs. The one exception below — "registry hasn't
// loaded yet" — stubs fetch to never resolve so the query stays
// truly pending without leaning on network behavior.
let fetchSpy: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchSpy = vi.fn(() => new Promise(() => {}));
  vi.stubGlobal("fetch", fetchSpy);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useScrubberCityReset", () => {
  it("on mount, resets scrubber hour to the active city's local noon", () => {
    const store = _makeStore("phoenix");
    const qc = _makeClient();
    renderHook(() => useScrubberCityReset(), { wrapper: _wrap(store, qc) });
    expect(store.getState().scrubber.hourOfDay).toBe(19);
  });

  it("on city switch, resets scrubber hour to the new city's local noon", () => {
    const store = _makeStore("cambridge");
    const qc = _makeClient();
    renderHook(() => useScrubberCityReset(), { wrapper: _wrap(store, qc) });
    // Cambridge local noon should have applied on mount.
    const initialHour = store.getState().scrubber.hourOfDay;
    expect([16, 17]).toContain(initialHour); // EDT or EST

    act(() => {
      store.dispatch(setActiveCity("phoenix"));
    });
    expect(store.getState().scrubber.hourOfDay).toBe(19);
  });

  it("does nothing when the cities registry hasn't loaded yet", () => {
    const store = _makeStore("phoenix");
    const qc = _makeEmptyClient();
    const beforeHour = store.getState().scrubber.hourOfDay;
    renderHook(() => useScrubberCityReset(), { wrapper: _wrap(store, qc) });
    expect(store.getState().scrubber.hourOfDay).toBe(beforeHour);
  });

  it("does nothing for an unknown slug (deep-link to a removed city)", () => {
    const store = _makeStore("nonexistent");
    const qc = _makeClient();
    const beforeHour = store.getState().scrubber.hourOfDay;
    renderHook(() => useScrubberCityReset(), { wrapper: _wrap(store, qc) });
    expect(store.getState().scrubber.hourOfDay).toBe(beforeHour);
  });

  it("re-dispatching the same slug does not re-apply (idempotent)", () => {
    const store = _makeStore("phoenix");
    const qc = _makeClient();
    renderHook(() => useScrubberCityReset(), { wrapper: _wrap(store, qc) });
    expect(store.getState().scrubber.hourOfDay).toBe(19);

    // The user manually scrubs to a different hour.
    act(() => {
      store.dispatch({ type: "scrubber/setHourOfDay", payload: 8 });
    });
    expect(store.getState().scrubber.hourOfDay).toBe(8);

    // Re-dispatching the same active city must NOT clobber the
    // user's manual scrub — the lastAppliedSlug ref guards this.
    act(() => {
      store.dispatch(setActiveCity("phoenix"));
    });
    expect(store.getState().scrubber.hourOfDay).toBe(8);
  });
});
