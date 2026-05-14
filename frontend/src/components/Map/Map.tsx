/**
 * Map component — imperative MapLibre, mounted once via a ref.
 *
 * MapLibre owns its own GL context and animation loop; React's
 * reconciliation must not touch it. This component:
 *
 * - Mounts the MapLibre instance once on first render.
 * - Calls `map.remove()` on unmount.
 * - Never re-creates the instance on parent re-render.
 *
 * Style is built declaratively from `tileSourceUrl` and the
 * `risk_stub_bucket` attribute set by the SQL VIEW (Phase 1.5.6). Coloring
 * happens GPU-side via a Mapbox `match` expression — no per-frame
 * JavaScript in the render loop, per CLAUDE.md.
 */

import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";

import type { StyleSpecification } from "maplibre-gl";

const FIVE_STEP_PALETTE: readonly [string, string, string, string, string] = [
  "#2c7bb6", // 0 — coolest (lowest stub risk)
  "#abd9e9",
  "#ffffbf",
  "#fdae61",
  "#d7191c", // 4 — hottest (highest stub risk)
];

const DEFAULT_CENTER: [number, number] = [-71.1097, 42.3736]; // Cambridge, MA
const DEFAULT_ZOOM = 12;

export type MapProps = {
  tileSourceUrl: string;
  initialCenter?: [number, number];
  initialZoom?: number;
  /**
   * Imperative escape hatch — called once with the MapLibre instance
   * after construction, called again with `null` on unmount. Children
   * needing the map (e.g., the deck.gl glare overlay) hold the instance
   * in their own ref and react to its presence via `useEffect`.
   */
  onReady?: (map: maplibregl.Map | null) => void;
};

const buildStyle = (tileSourceUrl: string): StyleSpecification => ({
  version: 8,
  glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
  sources: {
    osm_basemap: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
      maxzoom: 19,
    },
    streetsense_segments: {
      type: "vector",
      tiles: [tileSourceUrl],
      minzoom: 0,
      maxzoom: 22,
    },
  },
  layers: [
    {
      id: "osm_basemap",
      type: "raster",
      source: "osm_basemap",
      minzoom: 0,
      maxzoom: 22,
    },
    {
      id: "road_segments_stub",
      type: "line",
      source: "streetsense_segments",
      "source-layer": "public.road_segments_tile",
      paint: {
        "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.5, 16, 4],
        "line-color": [
          "match",
          ["get", "risk_stub_bucket"],
          0,
          FIVE_STEP_PALETTE[0],
          1,
          FIVE_STEP_PALETTE[1],
          2,
          FIVE_STEP_PALETTE[2],
          3,
          FIVE_STEP_PALETTE[3],
          4,
          FIVE_STEP_PALETTE[4],
          /* default */ "#888",
        ],
        "line-opacity": 0.85,
      },
    },
  ],
});

export const Map = ({
  tileSourceUrl,
  initialCenter = DEFAULT_CENTER,
  initialZoom = DEFAULT_ZOOM,
  onReady,
}: MapProps) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) {
      return;
    }
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: buildStyle(tileSourceUrl),
      center: initialCenter,
      zoom: initialZoom,
    });
    mapRef.current = map;
    // Defer onReady until MapLibre's style finishes loading — deck.gl's
    // MapboxOverlay can attach pre-load but its first frame will
    // mis-fire if the underlying GL context isn't ready.
    if (map.loaded()) {
      onReady?.(map);
    } else {
      map.once("load", () => onReady?.(map));
    }

    return () => {
      onReady?.(null);
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // mount-once: subsequent prop changes do not re-create the instance.

  return <div ref={containerRef} style={{ position: "absolute", inset: 0 }} />;
};
