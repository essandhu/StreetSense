/**
 * Test for the Scrubber component — Task 2.5.3 (test-first).
 *
 * The Scrubber is a thin presentational component over two numeric
 * inputs: `hour` (0..23) and `day` (1..365). It reads from / writes to
 * the `scrubber` Redux slice. No calendar widget — see spec §"Out of
 * Scope".
 */

import { configureStore } from "@reduxjs/toolkit";
import { fireEvent, render, screen } from "@testing-library/react";
import { Provider } from "react-redux";
import { describe, expect, it } from "vitest";

import scrubberReducer, { setHourOfDay, setDayOfYear } from "../../state/scrubber";

import { Scrubber } from "./Scrubber";

const renderWithStore = (initial?: { hourOfDay?: number; dayOfYear?: number }) => {
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
  return {
    store,
    ...render(
      <Provider store={store}>
        <Scrubber />
      </Provider>
    ),
  };
};

describe("Scrubber", () => {
  it("renders hour and day-of-year numeric inputs", () => {
    renderWithStore();
    const hourInput = screen.getByLabelText(/hour/i) as HTMLInputElement;
    const dayInput = screen.getByLabelText(/day/i) as HTMLInputElement;
    expect(hourInput).toBeDefined();
    expect(dayInput).toBeDefined();
    expect(hourInput.type).toBe("number");
    expect(dayInput.type).toBe("number");
  });

  it("reflects current store state", () => {
    renderWithStore({ hourOfDay: 17, dayOfYear: 172 });
    const hourInput = screen.getByLabelText(/hour/i) as HTMLInputElement;
    const dayInput = screen.getByLabelText(/day/i) as HTMLInputElement;
    expect(hourInput.value).toBe("17");
    expect(dayInput.value).toBe("172");
  });

  it("dispatches setHourOfDay on hour input change", () => {
    const { store } = renderWithStore({ hourOfDay: 5, dayOfYear: 80 });
    const hourInput = screen.getByLabelText(/hour/i);
    fireEvent.change(hourInput, { target: { value: "14" } });
    expect(store.getState().scrubber.hourOfDay).toBe(14);
  });

  it("dispatches setDayOfYear on day input change", () => {
    const { store } = renderWithStore({ hourOfDay: 5, dayOfYear: 80 });
    const dayInput = screen.getByLabelText(/day/i);
    fireEvent.change(dayInput, { target: { value: "200" } });
    expect(store.getState().scrubber.dayOfYear).toBe(200);
  });

  it("clamps hour input to 0..23 via the slice", () => {
    const { store } = renderWithStore({ hourOfDay: 5, dayOfYear: 80 });
    // The slice clamps on dispatch.
    store.dispatch(setHourOfDay(99));
    expect(store.getState().scrubber.hourOfDay).toBe(23);
    store.dispatch(setHourOfDay(-5));
    expect(store.getState().scrubber.hourOfDay).toBe(0);
  });

  it("clamps day input to 1..365 via the slice", () => {
    const { store } = renderWithStore();
    store.dispatch(setDayOfYear(0));
    expect(store.getState().scrubber.dayOfYear).toBe(1);
    store.dispatch(setDayOfYear(400));
    expect(store.getState().scrubber.dayOfYear).toBe(365);
  });

  it("has min/max attributes matching slice bounds", () => {
    renderWithStore();
    const hourInput = screen.getByLabelText(/hour/i) as HTMLInputElement;
    const dayInput = screen.getByLabelText(/day/i) as HTMLInputElement;
    expect(hourInput.min).toBe("0");
    expect(hourInput.max).toBe("23");
    expect(dayInput.min).toBe("1");
    expect(dayInput.max).toBe("365");
  });
});
