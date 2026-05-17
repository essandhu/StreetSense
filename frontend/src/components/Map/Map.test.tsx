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
type MapInstance = {
  remove: ReturnType<typeof vi.fn>;
  on: ReturnType<typeof vi.fn>;
  off: ReturnType<typeof vi.fn>;
  once: ReturnType<typeof vi.fn>;
  addSource: ReturnType<typeof vi.fn>;
  addLayer: ReturnType<typeof vi.fn>;
  removeSource: ReturnType<typeof vi.fn>;
  removeLayer: ReturnType<typeof vi.fn>;
  getSource: ReturnType<typeof vi.fn>;
  getLayer: ReturnType<typeof vi.fn>;
  fitBounds: ReturnType<typeof vi.fn>;
  setCenter: ReturnType<typeof vi.fn>;
  setZoom: ReturnType<typeof vi.fn>;
  getZoom: ReturnType<typeof vi.fn>;
  getCenter: ReturnType<typeof vi.fn>;
  loaded: ReturnType<typeof vi.fn>;
};
const mapInstances: MapInstance[] = [];

vi.mock("maplibre-gl", () => {
  return {
    default: {
      Map: vi.fn().mockImplementation(() => {
        // Phase 4b Task 4.6: the mock now also tracks getSource /
        // getLayer / removeSource / removeLayer / fitBounds so the
        // tile-source swap and fitBounds effects are observable.
        const sources = new Set<string>(["streetsense_segments"]);
        const layers = new Set<string>(["road_segments_stub"]);
        const instance = {
          remove: vi.fn(),
          on: vi.fn(),
          off: vi.fn(),
          once: vi.fn((_event: string, cb: () => void) => cb()),
          addSource: vi.fn((id: string) => sources.add(id)),
          addLayer: vi.fn((layer: { id: string }) => layers.add(layer.id)),
          removeSource: vi.fn((id: string) => sources.delete(id)),
          removeLayer: vi.fn((id: string) => layers.delete(id)),
          getSource: vi.fn((id: string) => (sources.has(id) ? {} : undefined)),
          getLayer: vi.fn((id: string) => (layers.has(id) ? {} : undefined)),
          fitBounds: vi.fn(),
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

describe("Map — Phase 4b Task 4.6 — city switch", () => {
  it("swaps the segments source + layer when tileSourceUrl changes", () => {
    const urlA = "http://localhost:7800/tiles/public.road_segments_tile/{z}/{x}/{y}.pbf?city_slug=cambridge";
    const urlB = "http://localhost:7800/tiles/public.road_segments_tile/{z}/{x}/{y}.pbf?city_slug=phoenix";
    const { rerender } = render(<Map tileSourceUrl={urlA} />);
    const instance = mapInstances[0]!;
    // Mount already added the source via the initial style — the
    // swap effect should NOT fire on the very first paint because
    // currentTileUrlRef matches the prop. Only on a subsequent
    // change does it remove + re-add.
    instance.removeLayer.mockClear();
    instance.removeSource.mockClear();
    instance.addSource.mockClear();
    instance.addLayer.mockClear();

    rerender(<Map tileSourceUrl={urlB} />);

    // The layer must be removed before the source (else MapLibre
    // refuses the source removal). Assert via call counts.
    expect(instance.removeLayer).toHaveBeenCalledWith("road_segments_stub");
    expect(instance.removeSource).toHaveBeenCalledWith("streetsense_segments");
    expect(instance.addSource).toHaveBeenCalledWith(
      "streetsense_segments",
      expect.objectContaining({ tiles: [urlB] }),
    );
    expect(instance.addLayer).toHaveBeenCalledWith(
      expect.objectContaining({ id: "road_segments_stub" }),
    );
  });

  it("does not re-swap when the URL is unchanged across re-renders", () => {
    const url = "http://localhost:7800/tiles/public.road_segments_tile/{z}/{x}/{y}.pbf?city_slug=cambridge";
    const { rerender } = render(<Map tileSourceUrl={url} />);
    const instance = mapInstances[0]!;
    instance.removeLayer.mockClear();
    instance.removeSource.mockClear();

    rerender(<Map tileSourceUrl={url} />);
    rerender(<Map tileSourceUrl={url} />);

    expect(instance.removeLayer).not.toHaveBeenCalled();
    expect(instance.removeSource).not.toHaveBeenCalled();
  });

  it("calls fitBounds with the city's bbox + a short ease on fitBoundsTo change", () => {
    const url = "http://localhost:7800/tiles/public.road_segments_tile/{z}/{x}/{y}.pbf?city_slug=cambridge";
    const cambridgeBounds: [[number, number], [number, number]] = [
      [-71.16, 42.35],
      [-71.07, 42.41],
    ];
    const phoenixBounds: [[number, number], [number, number]] = [
      [-112.32, 33.29],
      [-111.93, 33.92],
    ];
    const { rerender } = render(
      <Map tileSourceUrl={url} fitBoundsTo={cambridgeBounds} />,
    );
    const instance = mapInstances[0]!;
    // Initial mount → fitBounds called once for cambridge.
    expect(instance.fitBounds).toHaveBeenCalledWith(
      cambridgeBounds,
      expect.objectContaining({ duration: 600 }),
    );
    instance.fitBounds.mockClear();

    rerender(<Map tileSourceUrl={url} fitBoundsTo={phoenixBounds} />);
    expect(instance.fitBounds).toHaveBeenCalledWith(
      phoenixBounds,
      expect.objectContaining({ duration: 600 }),
    );
  });

  it("does not call fitBounds when fitBoundsTo is null", () => {
    const url = "http://localhost:7800/tiles/public.road_segments_tile/{z}/{x}/{y}.pbf?city_slug=cambridge";
    render(<Map tileSourceUrl={url} fitBoundsTo={null} />);
    const instance = mapInstances[0]!;
    expect(instance.fitBounds).not.toHaveBeenCalled();
  });
});
