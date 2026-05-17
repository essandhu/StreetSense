/**
 * Tests for CitySelector — Phase 4b Task 4.4.
 *
 * Style mirrors the component-level wrappers used by RunPicker and
 * DeltaHistogram: configureStore + QueryClientProvider + a fetch
 * shim. No MSW dependency.
 *
 * Covers:
 *
 * - **Loading.** Shows a disabled placeholder before the
 *   `GET /api/cities` query settles.
 * - **Error.** Shows a disabled "Cities unavailable" label when the
 *   query rejects (the dropdown stays mounted rather than vanishing,
 *   so the app shell never reflows around it).
 * - **Render.** Each city in the response renders as an `<option>`
 *   labeled with its `name`. The currently-active slug is selected.
 * - **Dispatch.** Choosing a different city dispatches `setActiveCity`
 *   with the chosen slug.
 * - **Active slug not in list.** A deep-link to a removed slug still
 *   renders the list; the `<select>` shows no selected option but
 *   choosing one still dispatches correctly.
 */
import { configureStore } from "@reduxjs/toolkit";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { Provider } from "react-redux";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CityListResponse } from "../../domain";
import activeCityReducer from "../../state/activeCity";

import { CitySelector } from "./CitySelector";

const _PAYLOAD: CityListResponse = {
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
    {
      id: "00000000-0000-0000-0000-000000000003",
      slug: "austin",
      name: "Austin, TX",
      bbox: [-97.94, 30.1, -97.56, 30.52],
      default_zoom: 11,
      timezone: "America/Chicago",
    },
  ],
};

function _makeStore(initialSlug = "cambridge") {
  const store = configureStore({
    reducer: { activeCity: activeCityReducer },
    preloadedState: { activeCity: { slug: initialSlug } },
  });
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

const _newClient = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

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

describe("CitySelector — loading state", () => {
  it("renders a disabled placeholder while the cities query is pending", () => {
    // A QueryClient that never resolves — the queryFn is held by the
    // mounted hook but the test never lets the microtask flush past
    // pending. Easiest reproduction: stub fetch with a never-settling
    // promise so the query is forever "pending".
    fetchSpy = vi.fn(() => new Promise(() => {}));
    vi.stubGlobal("fetch", fetchSpy);

    const qc = _newClient();
    render(<CitySelector />, { wrapper: _wrap(qc) });
    const select = screen.getByTestId("city-selector") as HTMLSelectElement;
    expect(select.disabled).toBe(true);
    expect(select.textContent).toContain("Loading");
  });
});

describe("CitySelector — error state", () => {
  it("renders a disabled error label when the query rejects", async () => {
    fetchSpy = vi.fn(async () => {
      return new Response("boom", { status: 500 });
    });
    vi.stubGlobal("fetch", fetchSpy);

    const qc = _newClient();
    render(<CitySelector />, { wrapper: _wrap(qc) });
    await waitFor(() => {
      const select = screen.getByTestId("city-selector") as HTMLSelectElement;
      expect(select.textContent).toContain("unavailable");
      expect(select.disabled).toBe(true);
    });
  });
});

describe("CitySelector — render", () => {
  it("renders one <option> per city with its display name", async () => {
    const qc = _newClient();
    render(<CitySelector />, { wrapper: _wrap(qc) });
    await waitFor(() => {
      expect(screen.getByRole("option", { name: "Cambridge, MA" })).toBeInTheDocument();
    });
    expect(screen.getByRole("option", { name: "Phoenix, AZ" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Austin, TX" })).toBeInTheDocument();
  });

  it("selects the active slug as the dropdown value", async () => {
    const qc = _newClient();
    const store = _makeStore("phoenix");
    render(<CitySelector />, { wrapper: _wrap(qc, store) });
    await waitFor(() => {
      const select = screen.getByTestId("city-selector") as HTMLSelectElement;
      expect(select.value).toBe("phoenix");
    });
  });
});

describe("CitySelector — dispatch", () => {
  it("dispatches setActiveCity when the user picks a different city", async () => {
    const qc = _newClient();
    const store = _makeStore("cambridge");
    render(<CitySelector />, { wrapper: _wrap(qc, store) });
    await waitFor(() => {
      expect(screen.getByRole("option", { name: "Phoenix, AZ" })).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("city-selector"), {
      target: { value: "phoenix" },
    });

    expect(store.getState().activeCity.slug).toBe("phoenix");
  });

  it("re-selecting the same city is a no-op against the slice (already normalized)", async () => {
    // setActiveCity is idempotent at the slice level (covered by
    // activeCity.test.ts). At the component layer, native <select>
    // fires `change` only on a real value transition, so a
    // re-selection wouldn't even hit the reducer. Asserting the slug
    // is unchanged keeps that contract pinned.
    const qc = _newClient();
    const store = _makeStore("cambridge");
    render(<CitySelector />, { wrapper: _wrap(qc, store) });
    await waitFor(() => {
      expect(screen.getByRole("option", { name: "Cambridge, MA" })).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId("city-selector"), {
      target: { value: "cambridge" },
    });
    expect(store.getState().activeCity.slug).toBe("cambridge");
  });
});

describe("CitySelector — active slug not in the loaded list", () => {
  it("still renders the dropdown so the user can recover", async () => {
    const qc = _newClient();
    // Deep-link to a slug that the cities table doesn't have. The
    // activeCity slice already accepted it (setActiveCity normalizes
    // but doesn't validate against /api/cities — the API does that
    // server-side). The selector must stay mounted so a user can
    // recover by picking a real one.
    const store = _makeStore("nonexistent");
    render(<CitySelector />, { wrapper: _wrap(qc, store) });
    await waitFor(() => {
      expect(screen.getByRole("option", { name: "Cambridge, MA" })).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("city-selector"), {
      target: { value: "austin" },
    });
    expect(store.getState().activeCity.slug).toBe("austin");
  });
});
