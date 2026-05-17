/**
 * Tests for `useActiveCityUrlSync` — Phase 4b Task 4.5.
 *
 * Three invariants to verify:
 *
 *   1. Mount hydration: a fresh tab opening with `?city=austin`
 *      lands the slice on austin (not the default cambridge).
 *   2. Writer: a dispatched `setActiveCity('phoenix')` updates the URL.
 *   3. popstate: dispatching `dispatchEvent(new PopStateEvent('popstate'))`
 *      after manually updating `window.location` re-hydrates the slice.
 *
 * Plus the two boundary cases:
 *
 *   - An absent `?city` on mount uses the default; the URL is left
 *     alone (the writer effect doesn't fire if the slug was already
 *     the default and the URL didn't have a slug).
 *   - A slug appearing in the URL that matches the current slug
 *     produces no writer call (assertion: no extra
 *     `history.replaceState` after the no-op).
 */
import { configureStore } from "@reduxjs/toolkit";
import { act, renderHook } from "@testing-library/react";
import { type ReactNode } from "react";
import { Provider } from "react-redux";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import activeCity, { setActiveCity } from "./activeCity";
import { useActiveCityUrlSync } from "./useActiveCityUrlSync";

function _makeStore(initialSlug = "cambridge") {
  const store = configureStore({ reducer: { activeCity } });
  if (initialSlug !== "cambridge") store.dispatch(setActiveCity(initialSlug));
  return store;
}

function _wrap(store: ReturnType<typeof _makeStore>) {
  return ({ children }: { children: ReactNode }) => (
    <Provider store={store}>{children}</Provider>
  );
}

// jsdom resets window.location across test files but not across
// `it`s in the same file. Reset URL between each test so cross-test
// state doesn't leak.
let originalSearch: string;
let replaceStateSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  originalSearch = window.location.search;
  // Reset to a clean URL so the URL doesn't accidentally pre-hydrate.
  window.history.replaceState(null, "", "/");
  replaceStateSpy = vi.spyOn(window.history, "replaceState");
});

afterEach(() => {
  replaceStateSpy.mockRestore();
  // Restore the original search so other test files see a clean state.
  window.history.replaceState(null, "", `/${originalSearch}`);
});

describe("useActiveCityUrlSync — mount hydration", () => {
  it("a fresh tab with ?city=austin lands the slice on austin", () => {
    window.history.replaceState(null, "", "/?city=austin");
    const store = _makeStore("cambridge");
    renderHook(() => useActiveCityUrlSync(), { wrapper: _wrap(store) });
    expect(store.getState().activeCity.slug).toBe("austin");
  });

  it("an absent ?city falls back to the default and is a no-op against the slice", () => {
    window.history.replaceState(null, "", "/");
    const store = _makeStore("cambridge");
    renderHook(() => useActiveCityUrlSync(), { wrapper: _wrap(store) });
    expect(store.getState().activeCity.slug).toBe("cambridge");
  });

  it("normalizes slug casing on mount", () => {
    window.history.replaceState(null, "", "/?city=PHOENIX");
    const store = _makeStore("cambridge");
    renderHook(() => useActiveCityUrlSync(), { wrapper: _wrap(store) });
    expect(store.getState().activeCity.slug).toBe("phoenix");
  });
});

describe("useActiveCityUrlSync — writer", () => {
  it("dispatching setActiveCity('phoenix') updates the URL via replaceState", () => {
    window.history.replaceState(null, "", "/");
    const store = _makeStore("cambridge");
    renderHook(() => useActiveCityUrlSync(), { wrapper: _wrap(store) });
    // mount-hydration replaceState calls happen first; clear so we
    // measure only the writer effect's calls.
    replaceStateSpy.mockClear();

    act(() => {
      store.dispatch(setActiveCity("phoenix"));
    });

    expect(replaceStateSpy).toHaveBeenCalledTimes(1);
    expect(window.location.search).toContain("city=phoenix");
  });

  it("does not push extra history entries (replaceState only)", () => {
    const pushStateSpy = vi.spyOn(window.history, "pushState");
    window.history.replaceState(null, "", "/");
    const store = _makeStore("cambridge");
    renderHook(() => useActiveCityUrlSync(), { wrapper: _wrap(store) });

    act(() => {
      store.dispatch(setActiveCity("austin"));
    });
    act(() => {
      store.dispatch(setActiveCity("phoenix"));
    });

    expect(pushStateSpy).not.toHaveBeenCalled();
    pushStateSpy.mockRestore();
  });

  it("a no-op dispatch (same slug) does not call replaceState", () => {
    window.history.replaceState(null, "", "/?city=cambridge");
    const store = _makeStore("cambridge");
    renderHook(() => useActiveCityUrlSync(), { wrapper: _wrap(store) });
    replaceStateSpy.mockClear();

    act(() => {
      store.dispatch(setActiveCity("cambridge"));
    });
    expect(replaceStateSpy).not.toHaveBeenCalled();
  });
});

describe("useActiveCityUrlSync — popstate re-hydration", () => {
  it("popstate after URL change re-dispatches setActiveCity", () => {
    window.history.replaceState(null, "", "/?city=phoenix");
    const store = _makeStore("phoenix");
    renderHook(() => useActiveCityUrlSync(), { wrapper: _wrap(store) });

    // Simulate browser back: URL changes to a different slug, then
    // popstate fires.
    act(() => {
      window.history.replaceState(null, "", "/?city=austin");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(store.getState().activeCity.slug).toBe("austin");
  });

  it("popstate with no ?city falls back to default", () => {
    window.history.replaceState(null, "", "/?city=phoenix");
    const store = _makeStore("phoenix");
    renderHook(() => useActiveCityUrlSync(), { wrapper: _wrap(store) });

    act(() => {
      window.history.replaceState(null, "", "/");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(store.getState().activeCity.slug).toBe("cambridge");
  });
});
