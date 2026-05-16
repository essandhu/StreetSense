/**
 * DeltaOverlay — Phase 5 deck.gl overlay over MapLibre, scoped to
 * delta-mode views.
 *
 * Mirrors :class:`LayerOverlay` (single-run layers) but reads the
 * delta tile URL from `useDeltaTileSource` and paints
 * ``composite_delta`` via the diverging accessors in
 * `Map/deltaLayer.ts`. The component is a controller — it renders
 * no DOM, just imperatively mounts a MapboxOverlay on the parent's
 * MapLibre instance.
 *
 * Unmounts cleanly when the run pair is empty (the underlying hook
 * returns null) so an in-flight tile fetch doesn't hang the GL
 * context when the user clears a dropdown.
 */
import { MapboxOverlay } from "@deck.gl/mapbox";
import { MVTLayer } from "@deck.gl/geo-layers";
import type maplibregl from "maplibre-gl";
import { useEffect, useMemo } from "react";
import { useSelector } from "react-redux";

import { useDeltaTileSource } from "../../data/useDeltaTileSource";
import type { RootState } from "../../state/store";

import { deltaColorAccessor, deltaWidthAccessor } from "./deltaLayer";

export type DeltaOverlayProps = {
  map: maplibregl.Map | null;
};

const _selectDelta = (s: RootState) => s.delta;

export const DeltaOverlay = ({ map }: DeltaOverlayProps) => {
  const { runA, runB } = useSelector(_selectDelta);
  const tileSource = useDeltaTileSource(runA, runB);

  // Same `interleaved: false` rationale as LayerOverlay — see that
  // file's docstring; the shared-GL mode regressed scrubber tile
  // latency 5-7x on Cambridge data.
  const overlay = useMemo(() => {
    return new MapboxOverlay({ interleaved: false, layers: [] });
  }, []);

  useEffect(() => {
    if (!map) return undefined;
    map.addControl(overlay);
    return () => {
      map.removeControl(overlay);
    };
  }, [map, overlay]);

  useEffect(() => {
    const url = tileSource.data?.url;
    if (!url) {
      overlay.setProps({ layers: [] });
      return;
    }
    overlay.setProps({
      layers: [
        new MVTLayer({
          id: `delta-layer-${url}`,
          data: url,
          minZoom: 0,
          maxZoom: 22,
          lineWidthMinPixels: 1.5,
          getLineColor: deltaColorAccessor,
          getLineWidth: deltaWidthAccessor,
          pickable: false,
          updateTriggers: {
            getLineColor: [url],
            getLineWidth: [url],
          },
        }),
      ],
    });
  }, [overlay, tileSource.data?.url]);

  return null;
};
