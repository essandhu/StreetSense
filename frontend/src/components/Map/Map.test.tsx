/**
 * Test for the Map component — Task 1.6.3 (test-first).
 *
 * Asserts the imperative-MapLibre pattern from CLAUDE.md / spec.md AC-4:
 *
 * - The MapLibre instance is created exactly once per mount.
 * - Re-rendering the parent does not re-create the instance.
 * - Unmounting calls map.remove() so WebGL contexts are released.
 *
 * MapLibre is mocked because jsdom has no WebGL — we just verify our
 * lifecycle wiring is correct, not MapLibre's internals.
 */

import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// --- Mock maplibre-gl ----------------------------------------------------
const mapInstances: Array<{ remove: ReturnType<typeof vi.fn>; on: ReturnType<typeof vi.fn> }> = [];

vi.mock("maplibre-gl", () => {
  return {
    default: {
      Map: vi.fn().mockImplementation(() => {
        const instance = {
          remove: vi.fn(),
          on: vi.fn(),
          off: vi.fn(),
          addSource: vi.fn(),
          addLayer: vi.fn(),
          setCenter: vi.fn(),
          setZoom: vi.fn(),
          getZoom: vi.fn(() => 12),
          getCenter: vi.fn(() => ({ lng: 0, lat: 0 })),
          loaded: vi.fn(() => true),
        };
        mapInstances.push(instance);
        return instance;
      }),
    },
    Map: vi.fn(),
  };
});

// Imported AFTER vi.mock so it picks up the mock.
import { Map } from "./Map";

beforeEach(() => {
  mapInstances.length = 0;
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Map component", () => {
  it("creates exactly one MapLibre instance on mount", () => {
    render(
      <Map tileSourceUrl="http://localhost:7800/tiles/public.road_segments_tile/{z}/{x}/{y}.pbf" />
    );
    expect(mapInstances.length).toBe(1);
  });

  it("does not re-create the instance on parent re-render", () => {
    const { rerender } = render(
      <Map tileSourceUrl="http://localhost:7800/tiles/public.road_segments_tile/{z}/{x}/{y}.pbf" />
    );
    rerender(
      <Map tileSourceUrl="http://localhost:7800/tiles/public.road_segments_tile/{z}/{x}/{y}.pbf" />
    );
    rerender(
      <Map tileSourceUrl="http://localhost:7800/tiles/public.road_segments_tile/{z}/{x}/{y}.pbf" />
    );
    expect(mapInstances.length).toBe(1);
  });

  it("calls map.remove() on unmount", () => {
    const { unmount } = render(
      <Map tileSourceUrl="http://localhost:7800/tiles/public.road_segments_tile/{z}/{x}/{y}.pbf" />
    );
    expect(mapInstances.length).toBe(1);
    const instance = mapInstances[0];
    expect(instance).toBeDefined();
    unmount();
    expect(instance!.remove).toHaveBeenCalledTimes(1);
  });
});
