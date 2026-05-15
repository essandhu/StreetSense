/**
 * MapView — Phase 4 composition.
 *
 * Wires the MapLibre base under the deck.gl thematic-layer overlay
 * (composite-risk by default, switchable to any of the four
 * sub-scores per spec AC-8) and the segment-detail panel. Map clicks
 * dispatch `openSegment`; the panel reads `selectedSegment` from
 * Redux and fires `useSegmentDetail`.
 */

import { useCallback, useEffect, useState } from "react";
import type maplibregl from "maplibre-gl";

import { LayerOverlay } from "../components/Map/LayerOverlay";
import { LayerToggle } from "../components/Map/LayerToggle";
import { Map } from "../components/Map/Map";
import { Scrubber } from "../components/Scrubber/Scrubber";
import { SegmentDetailPanel } from "../components/SegmentDetailPanel";
import { useTileSourceConfig } from "../data/useTileSourceConfig";
import { SegmentId } from "../domain";
import { useAppDispatch } from "../state/hooks";
import { openSegment } from "../state/selectedSegment";

export const MapView = () => {
  const tileSource = useTileSourceConfig();
  const [map, setMap] = useState<maplibregl.Map | null>(null);
  const dispatch = useAppDispatch();

  const onSegmentClick = useCallback(
    (id: string) => {
      dispatch(openSegment(SegmentId(id)));
    },
    [dispatch],
  );

  // Dev-only hook for benchmark / E2E suites that need to programmatically
  // open the panel without hitting MapLibre's per-pixel feature-click
  // probe. Production builds strip this.
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    type BenchWindow = Window & { __benchOpenSegment?: (id: string) => void };
    const w = window as BenchWindow;
    w.__benchOpenSegment = (id: string) => dispatch(openSegment(SegmentId(id)));
    return () => {
      delete w.__benchOpenSegment;
    };
  }, [dispatch]);

  if (tileSource.status === "pending") {
    return <div data-testid="map-loading">Loading tile source…</div>;
  }
  if (tileSource.status === "error") {
    return <div data-testid="map-error">Tile source unavailable.</div>;
  }

  return (
    <>
      <Map
        tileSourceUrl={tileSource.data.url}
        onReady={setMap}
        onSegmentClick={onSegmentClick}
      />
      <LayerOverlay map={map} />
      <LayerToggle />
      <Scrubber />
      <SegmentDetailPanel />
    </>
  );
};
