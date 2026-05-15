/**
 * LayerOverlay — Phase 4 deck.gl overlay above the MapLibre base.
 *
 * Generalizes Phase 2's glare-only overlay so the same MapboxOverlay
 * can render any of the five Phase 4 thematic layers (composite plus
 * the four sub-scores). The active layer is single-select per
 * ``state.activeLayer`` — only one thematic surface paints at a
 * time, which keeps the color channel legible. Switching is cheap:
 * the tile URL is the same (the Phase 4 tile function returns every
 * attribute), so only the per-feature accessor changes.
 *
 * Stub handling: every sub-score carries an ``is_stub_*`` flag from
 * the tile. Phase 4 steady-state has all four real, but legacy rows
 * (and the no-score-row fallback branch) still appear with stubs —
 * those render as a neutral dim color so the visual language doesn't
 * imply elevated risk where there is no signal.
 *
 * This component renders no DOM. It is a controller component that
 * imperatively manages a side effect on the parent's MapLibre instance.
 *
 * Spec: AC-8 — "A new composite-risk layer ships, default-on at app
 * boot; glare / lane / junction / historical become secondary
 * toggleable layers."
 */

import { MapboxOverlay } from "@deck.gl/mapbox";
import { MVTLayer } from "@deck.gl/geo-layers";
import type maplibregl from "maplibre-gl";
import { useEffect, useMemo } from "react";
import { useSelector } from "react-redux";

import { useGlareTileSource } from "../../data/useGlareTileSource";
import type { RootState } from "../../state/store";

import { colorAccessorForLayer } from "./layerColor";

export type LayerOverlayProps = {
  map: maplibregl.Map | null;
};

export const LayerOverlay = ({ map }: LayerOverlayProps) => {
  const tileSource = useGlareTileSource();
  const activeLayer = useSelector((s: RootState) => s.activeLayer.layer);

  // `interleaved: false` overlays deck.gl on its own canvas above
  // MapLibre — the deck.gl-recommended pattern. `true` (shared GL
  // context) is supported but our scrubber latency benchmark shows
  // a 5-7x regression in tile-fetch-to-render latency under that
  // mode on Cambridge data; not worth the saving.
  const overlay = useMemo(() => {
    return new MapboxOverlay({ interleaved: false, layers: [] });
  }, []);

  // Mount the overlay control once on the map.
  useEffect(() => {
    if (!map) return undefined;
    map.addControl(overlay);
    return () => {
      map.removeControl(overlay);
    };
  }, [map, overlay]);

  // Re-set the deck.gl MVTLayer whenever the active layer, the tile
  // URL (i.e., the scrubber changed), or both change.
  useEffect(() => {
    const url = tileSource.data?.url;
    if (!url) return;
    const accessor = colorAccessorForLayer(activeLayer);
    overlay.setProps({
      layers: [
        new MVTLayer({
          id: `layer-${activeLayer}-${url}`,
          data: url,
          minZoom: 0,
          maxZoom: 22,
          lineWidthMinPixels: 1.5,
          getLineColor: accessor,
          getLineWidth: 2,
          pickable: false,
          updateTriggers: {
            // Force a re-paint when either the URL or the active
            // layer changes — deck.gl otherwise caches the prior
            // accessor closure.
            getLineColor: [url, activeLayer],
          },
        }),
      ],
    });
  }, [overlay, tileSource.data?.url, activeLayer]);

  return null;
};
