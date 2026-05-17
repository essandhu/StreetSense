/**
 * MapView — Phase 4 composition.
 *
 * Wires the MapLibre base under the deck.gl thematic-layer overlay
 * (composite-risk by default, switchable to any of the four
 * sub-scores per spec AC-8) and the segment-detail panel. Map clicks
 * dispatch `openSegment`; the panel reads `selectedSegment` from
 * Redux and fires `useSegmentDetail`.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSelector } from "react-redux";
import type maplibregl from "maplibre-gl";

import { LayerOverlay } from "../components/Map/LayerOverlay";
import { LayerToggle } from "../components/Map/LayerToggle";
import { Map } from "../components/Map/Map";
import { Scrubber } from "../components/Scrubber/Scrubber";
import { SegmentDetailPanel } from "../components/SegmentDetailPanel";
import { findCityBySlug, useCities } from "../data/useCities";
import { useTileSourceConfig } from "../data/useTileSourceConfig";
import { SegmentId } from "../domain";
import { useAppDispatch } from "../state/hooks";
import { openSegment } from "../state/selectedSegment";
import type { RootState } from "../state/store";

const _selectActiveCitySlug = (s: RootState) => s.activeCity.slug;

export const MapView = () => {
  const tileSource = useTileSourceConfig();
  const cities = useCities();
  const activeCitySlug = useSelector(_selectActiveCitySlug);
  const [map, setMap] = useState<maplibregl.Map | null>(null);
  const dispatch = useAppDispatch();

  // Phase 4b Task 4.6: feed the Map the active city's bbox so it
  // calls fitBounds on switch. `null` while the cities registry is
  // loading or the active slug isn't in the registry — the Map's
  // effect no-ops on null.
  const fitBoundsTo = useMemo<[[number, number], [number, number]] | null>(() => {
    const city = findCityBySlug(cities.data, activeCitySlug);
    if (!city) return null;
    const [minLon, minLat, maxLon, maxLat] = city.bbox;
    return [
      [minLon, minLat],
      [maxLon, maxLat],
    ];
  }, [cities.data, activeCitySlug]);

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
        fitBoundsTo={fitBoundsTo}
      />
      <LayerOverlay map={map} />
      <LayerToggle />
      <Scrubber />
      <SegmentDetailPanel />
    </>
  );
};
