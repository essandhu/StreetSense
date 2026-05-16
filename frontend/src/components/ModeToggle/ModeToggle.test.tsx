/**
 * Tests for ModeToggle (Task 3.7) — top-level Single ↔ Delta switch.
 */
import { configureStore } from "@reduxjs/toolkit";
import { fireEvent, render, screen } from "@testing-library/react";
import { Provider } from "react-redux";
import { describe, expect, it } from "vitest";

import deltaReducer, { type DeltaState } from "../../state/delta";

import { ModeToggle } from "./ModeToggle";

const _render = (initial?: Partial<DeltaState>) => {
  const store = configureStore({
    reducer: { delta: deltaReducer },
    preloadedState: initial
      ? {
          delta: {
            mode: initial.mode ?? "single",
            runA: initial.runA ?? null,
            runB: initial.runB ?? null,
          },
        }
      : undefined,
  });
  return {
    store,
    ...render(
      <Provider store={store}>
        <ModeToggle />
      </Provider>
    ),
  };
};

describe("ModeToggle", () => {
  it("renders two buttons: Single run and Delta", () => {
    _render();
    expect(screen.getByRole("button", { name: /single/i })).toBeDefined();
    expect(screen.getByRole("button", { name: /delta/i })).toBeDefined();
  });

  it("marks the active mode via aria-pressed", () => {
    _render({ mode: "single" });
    const single = screen.getByRole("button", { name: /single/i });
    const delta = screen.getByRole("button", { name: /delta/i });
    expect(single.getAttribute("aria-pressed")).toBe("true");
    expect(delta.getAttribute("aria-pressed")).toBe("false");
  });

  it("clicking Delta dispatches enterDeltaMode", () => {
    const { store } = _render({ mode: "single" });
    fireEvent.click(screen.getByRole("button", { name: /delta/i }));
    expect(store.getState().delta.mode).toBe("delta");
  });

  it("clicking Single dispatches exitDeltaMode (clears runs too)", () => {
    const { store } = _render({
      mode: "delta",
      runA: "11111111-1111-1111-1111-111111111111" as unknown as DeltaState["runA"],
      runB: "22222222-2222-2222-2222-222222222222" as unknown as DeltaState["runB"],
    });
    fireEvent.click(screen.getByRole("button", { name: /single/i }));
    const s = store.getState().delta;
    expect(s.mode).toBe("single");
    expect(s.runA).toBeNull();
    expect(s.runB).toBeNull();
  });

  it("clicking the already-active button is a no-op (no transient state churn)", () => {
    const { store } = _render({ mode: "single" });
    const before = store.getState().delta;
    fireEvent.click(screen.getByRole("button", { name: /single/i }));
    expect(store.getState().delta).toEqual(before);
  });
});
