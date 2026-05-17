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
  /**
   * Called when a feature on the road-segments layer is clicked. Phase
   * 3.6.13: dispatched by `MapView` to open the segment-detail panel.
   */
  onSegmentClick?: (segmentId: string) => void;
  /**
   * Phase 4b Task 4.6: on city switch, MapView passes the new city's
   * WGS84 bbox as ``[[min_lon, min_lat], [max_lon, max_lat]]``. The
   * component calls ``map.fitBounds`` with a short ease, leaving the
   * MapLibre instance in place. ``null`` skips the fit (e.g., the
   * city registry hasn't loaded yet).
   */
  fitBoundsTo?: [[number, number], [number, number]] | null;
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

const SEGMENTS_SOURCE_ID = "streetsense_segments";
const SEGMENTS_LAYER_ID = "road_segments_stub";

export const Map = ({
  tileSourceUrl,
  initialCenter = DEFAULT_CENTER,
  initialZoom = DEFAULT_ZOOM,
  onReady,
  onSegmentClick,
  fitBoundsTo,
}: MapProps) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const onSegmentClickRef = useRef(onSegmentClick);
  // Track the last URL the source was rebuilt against. Phase 4b
  // Task 4.6's switch-source effect compares against this so it
  // only swaps when the URL truly changes — useful in StrictMode's
  // double-invoke.
  const currentTileUrlRef = useRef<string>(tileSourceUrl);

  // Keep the callback ref in sync so the mount-once map handler picks
  // up the latest closure without re-attaching on every render.
  useEffect(() => {
    onSegmentClickRef.current = onSegmentClick;
  }, [onSegmentClick]);

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

    // Phase 3.6.13: clicks on the road-segments layer open the panel.
    // The listener reads the latest callback via ref so we don't have
    // to re-attach on every render.
    const onClick = (e: maplibregl.MapMouseEvent) => {
      const features = map.queryRenderedFeatures(e.point, {
        layers: ["road_segments_stub"],
      });
      const id = features[0]?.properties?.id;
      if (typeof id === "string") {
        onSegmentClickRef.current?.(id);
      }
    };
    map.on("click", onClick);

    return () => {
      map.off("click", onClick);
      onReady?.(null);
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // mount-once: subsequent prop changes do not re-create the instance.

  // ----- Phase 4b Task 4.6: swap the segments source on URL change ------
  // The deck.gl LayerOverlay already re-fetches its MVT tiles when the
  // URL changes (its MVTLayer accepts the new `data` prop). The
  // MapLibre `streetsense_segments` source also has to be re-bound or
  // its `road_segments_stub` layer continues to render the previous
  // city's tiles. Imperative remove + add per spec: never tear down
  // the whole map.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (currentTileUrlRef.current === tileSourceUrl) return;
    currentTileUrlRef.current = tileSourceUrl;

    const apply = () => {
      // Drop the layer first so removeSource doesn't fail with
      // "Source ... cannot be removed while layer ... is using it".
      if (map.getLayer(SEGMENTS_LAYER_ID)) {
        map.removeLayer(SEGMENTS_LAYER_ID);
      }
      if (map.getSource(SEGMENTS_SOURCE_ID)) {
        map.removeSource(SEGMENTS_SOURCE_ID);
      }
      map.addSource(SEGMENTS_SOURCE_ID, {
        type: "vector",
        tiles: [tileSourceUrl],
        minzoom: 0,
        maxzoom: 22,
      });
      // Re-add the original layer with the same paint expressions
      // buildStyle uses on mount — the layer config lives in one
      // place via the literal below; if buildStyle's paint expression
      // changes, update both call sites.
      map.addLayer({
        id: SEGMENTS_LAYER_ID,
        type: "line",
        source: SEGMENTS_SOURCE_ID,
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
            "#888",
          ],
          "line-opacity": 0.85,
        },
      });
    };

    if (map.loaded()) {
      apply();
    } else {
      map.once("load", apply);
    }
  }, [tileSourceUrl]);

  // ----- Phase 4b Task 4.6: fitBounds on city switch --------------------
  // Short ease (600ms) so the user sees the camera glide between
  // cities rather than teleport. `null` / undefined skips the call
  // (the city registry hasn't loaded yet, or no bounds change is
  // needed). MapLibre's fitBounds accepts WGS84 lon/lat directly,
  // matching the City.bbox shape exactly.
  useEffect(() => {
    if (!fitBoundsTo) return;
    const map = mapRef.current;
    if (!map) return;
    const doFit = () => {
      map.fitBounds(fitBoundsTo, { duration: 600 });
    };
    if (map.loaded()) {
      doFit();
    } else {
      map.once("load", doFit);
    }
  }, [fitBoundsTo]);

  return <div ref={containerRef} style={{ position: "absolute", inset: 0 }} />;
};
