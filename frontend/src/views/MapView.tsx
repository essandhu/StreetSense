/**
 * MapView — Phase 2 composition.
 *
 * Wires the MapLibre base (Phase 1's stub-colored road segments) under
 * the deck.gl glare overlay (Phase 2). The Redux `scrubber` slice drives
 * the overlay's tile URL via the `useGlareTileSource` hook. The Scrubber
 * component sits at the bottom-left as an HTML overlay.
 *
 * The MapLibre instance escapes React reconciliation as before; the
 * deck.gl overlay attaches to it via the `onReady` callback, which
 * exposes the instance to the parent state.
 */

import { useState } from "react";
import type maplibregl from "maplibre-gl";

import { GlareOverlay } from "../components/Map/GlareOverlay";
import { Map } from "../components/Map/Map";
import { Scrubber } from "../components/Scrubber/Scrubber";
import { useTileSourceConfig } from "../data/useTileSourceConfig";

export const MapView = () => {
  const tileSource = useTileSourceConfig();
  const [map, setMap] = useState<maplibregl.Map | null>(null);

  if (tileSource.status === "pending") {
    return <div data-testid="map-loading">Loading tile source…</div>;
  }
  if (tileSource.status === "error") {
    return <div data-testid="map-error">Tile source unavailable.</div>;
  }

  return (
    <>
      <Map tileSourceUrl={tileSource.data.url} onReady={setMap} />
      <GlareOverlay map={map} />
      <Scrubber />
    </>
  );
};
