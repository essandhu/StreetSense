/**
 * GlareOverlay — Phase 2 deck.gl overlay above the MapLibre base.
 *
 * The MapLibre instance owns its GL context; deck.gl rides on top via
 * `MapboxOverlay` (from `@deck.gl/mapbox`), which adds itself as a
 * MapLibre IControl so the two share one canvas + render loop. No
 * per-frame JavaScript work happens in our code path — deck.gl's
 * `MVTLayer` decodes and draws each requested tile on the GPU using
 * the accessor function below.
 *
 * Color ramp: `glare_score` in [0, 1] is mapped through a 5-step cool→hot
 * palette identical to the Phase 1 stub palette so the visual language
 * is consistent. Stub flag handling: when `is_stub_glare = true` (e.g.,
 * a segment with no scoring-run row), the value falls back to a neutral
 * dim color rather than implying high risk.
 *
 * This component renders no DOM. It is a controller component that
 * imperatively manages a side effect on the parent's MapLibre instance.
 */

import { MapboxOverlay } from "@deck.gl/mapbox";
import { MVTLayer } from "@deck.gl/geo-layers";
import type maplibregl from "maplibre-gl";
import { useEffect, useMemo } from "react";

import { useGlareTileSource } from "../../data/useGlareTileSource";

// Five-step cool → hot palette as RGBA tuples. Same ordering as the
// Phase 1 stub palette in Map.tsx; the deck.gl shader needs numbers
// instead of CSS strings.
const PALETTE: ReadonlyArray<[number, number, number, number]> = [
  [44, 123, 182, 220], //   0 – coolest
  [171, 217, 233, 220],
  [255, 255, 191, 220],
  [253, 174, 97, 220],
  [215, 25, 28, 220], //    4 – hottest
];

const STUB_COLOR: [number, number, number, number] = [80, 80, 90, 80]; // neutral dim

/**
 * Map a glare_score in [0, 1] to a palette bucket and return the RGBA
 * tuple. Used as deck.gl's `getLineColor` accessor.
 */
const colorForGlare = (feature: {
  properties?: Record<string, unknown>;
}): [number, number, number, number] => {
  const props = feature.properties ?? {};
  const isStub = props["is_stub_glare"] === true;
  if (isStub) return STUB_COLOR;
  const raw = props["glare_score"];
  if (typeof raw !== "number" || !Number.isFinite(raw)) return STUB_COLOR;
  const clamped = Math.min(1.0, Math.max(0.0, raw));
  const idx = Math.min(PALETTE.length - 1, Math.floor(clamped * PALETTE.length));
  return PALETTE[idx] ?? STUB_COLOR;
};

export type GlareOverlayProps = {
  map: maplibregl.Map | null;
};

export const GlareOverlay = ({ map }: GlareOverlayProps) => {
  const tileSource = useGlareTileSource();

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

  // Re-set deck.gl layers whenever the tile URL changes (i.e., the
  // scrubber changed). MVTLayer fetches new tiles for the new URL.
  useEffect(() => {
    const url = tileSource.data?.url;
    if (!url) return;
    overlay.setProps({
      layers: [
        new MVTLayer({
          id: `glare-mvt-${url}`,
          data: url,
          minZoom: 0,
          maxZoom: 22,
          lineWidthMinPixels: 1.5,
          getLineColor: colorForGlare,
          getLineWidth: 2,
          pickable: false,
          updateTriggers: {
            getLineColor: [url],
          },
        }),
      ],
    });
  }, [overlay, tileSource.data?.url]);

  return null;
};
