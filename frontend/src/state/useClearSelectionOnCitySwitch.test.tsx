/**
 * Tests for `useClearSelectionOnCitySwitch` — Phase 4b Task 4.8.
 *
 * Three invariants:
 *
 *   1. On initial mount, nothing is cleared (the slug hasn't
 *      switched — it just landed). A pre-existing selection that
 *      came in via Redux preloadedState (e.g., a future "save my
 *      last view" feature) survives the first render.
 *
 *   2. On a real city switch, both `selectedSegment.segmentId` and
 *      the delta `(runA, runB)` pair clear to null. The delta mode
 *      itself does NOT change — a user in delta mode stays in
 *      delta mode after switching cities and re-picks runs.
 *
 *   3. Re-dispatching the same slug is a no-op (idempotent under
 *      StrictMode double-invoke and any redundant URL hydration).
 */
import { configureStore } from "@reduxjs/toolkit";
import { act, renderHook } from "@testing-library/react";
import { type ReactNode } from "react";
import { Provider } from "react-redux";
import { describe, expect, it } from "vitest";

import { RunId, SegmentId } from "../domain";
import activeCity, { setActiveCity } from "./activeCity";
import delta from "./delta";
import selectedSegment, { openSegment } from "./selectedSegment";
import { useClearSelectionOnCitySwitch } from "./useClearSelectionOnCitySwitch";

const _SEG = SegmentId("12345678-1234-5678-1234-567812345678");
const _RUN_A = RunId("11111111-1111-1111-1111-111111111111");
const _RUN_B = RunId("22222222-2222-2222-2222-222222222222");

function _makeStore(initialSlug = "cambridge") {
  const store = configureStore({
    reducer: { activeCity, selectedSegment, delta },
  });
  if (initialSlug !== "cambridge") store.dispatch(setActiveCity(initialSlug));
  return store;
}

function _wrap(store: ReturnType<typeof _makeStore>) {
  return ({ children }: { children: ReactNode }) => (
    <Provider store={store}>{children}</Provider>
  );
}

describe("useClearSelectionOnCitySwitch — initial mount", () => {
  it("preserves a pre-existing segment selection on the first render", () => {
    const store = _makeStore("cambridge");
    store.dispatch(openSegment(_SEG));
    renderHook(() => useClearSelectionOnCitySwitch(), { wrapper: _wrap(store) });
    expect(store.getState().selectedSegment.segmentId).toBe(_SEG);
    expect(store.getState().selectedSegment.isPanelOpen).toBe(true);
  });
});

describe("useClearSelectionOnCitySwitch — city switch", () => {
  it("clears selectedSegment on a real city switch", () => {
    const store = _makeStore("cambridge");
    store.dispatch(openSegment(_SEG));
    renderHook(() => useClearSelectionOnCitySwitch(), { wrapper: _wrap(store) });

    act(() => {
      store.dispatch(setActiveCity("phoenix"));
    });
    expect(store.getState().selectedSegment.segmentId).toBeNull();
    expect(store.getState().selectedSegment.isPanelOpen).toBe(false);
  });

  it("clears the delta (runA, runB) pair on a real city switch", () => {
    const store = _makeStore("cambridge");
    store.dispatch({ type: "delta/setRunA", payload: _RUN_A });
    store.dispatch({ type: "delta/setRunB", payload: _RUN_B });
    renderHook(() => useClearSelectionOnCitySwitch(), { wrapper: _wrap(store) });

    act(() => {
      store.dispatch(setActiveCity("austin"));
    });
    expect(store.getState().delta.runA).toBeNull();
    expect(store.getState().delta.runB).toBeNull();
  });

  it("leaves the delta mode alone (delta-mode user stays in delta mode)", () => {
    const store = _makeStore("cambridge");
    store.dispatch({ type: "delta/enterDeltaMode" });
    expect(store.getState().delta.mode).toBe("delta");
    renderHook(() => useClearSelectionOnCitySwitch(), { wrapper: _wrap(store) });

    act(() => {
      store.dispatch(setActiveCity("phoenix"));
    });
    expect(store.getState().delta.mode).toBe("delta");
  });
});

describe("useClearSelectionOnCitySwitch — idempotency", () => {
  it("re-dispatching the same slug is a no-op", () => {
    const store = _makeStore("phoenix");
    store.dispatch(openSegment(_SEG));
    renderHook(() => useClearSelectionOnCitySwitch(), { wrapper: _wrap(store) });
    expect(store.getState().selectedSegment.segmentId).toBe(_SEG);

    act(() => {
      store.dispatch(setActiveCity("phoenix"));
    });
    expect(store.getState().selectedSegment.segmentId).toBe(_SEG);
  });
});
