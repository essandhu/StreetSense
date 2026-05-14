/**
 * MapView — Phase 3 composition.
 *
 * Wires the MapLibre base under the deck.gl glare overlay (Phase 2)
 * and the new segment-detail panel (Phase 3). Map clicks dispatch
 * `openSegment`; the panel reads `selectedSegment` from Redux and
 * fires `useSegmentDetail`.
 */

import { useCallback, useState } from "react";
import type maplibregl from "maplibre-gl";

import { GlareOverlay } from "../components/Map/GlareOverlay";
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
      <GlareOverlay map={map} />
      <Scrubber />
      <SegmentDetailPanel />
    </>
  );
};
