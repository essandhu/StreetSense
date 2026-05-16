/**
 * DeltaView — Phase 5, Task 3.7.
 *
 * Composition surface for the delta mode. Wires:
 *   - MapLibre base under DeltaOverlay (paints `composite_delta`
 *     via deck.gl + the diverging accessors in Map/deltaLayer.ts).
 *   - RunPicker  — top-left, drives the (runA, runB) slice state.
 *   - LargestChangesList — right rail.
 *   - DeltaHistogram     — under the picker.
 *   - SegmentDetailPanel — unchanged from single-run mode; opens
 *     when the user clicks a list row or a segment on the map.
 *
 * Map click in delta mode still dispatches `openSegment` so the
 * Phase 3 panel can show the clicked segment's *single-run* detail
 * (which run? The Phase-5 detail-in-delta-context affordance is a
 * polish follow-up — see index.md).
 */

import { useCallback, useState } from "react";
import type maplibregl from "maplibre-gl";

import { DeltaHistogram } from "../components/DeltaHistogram/DeltaHistogram";
import { DeltaOverlay } from "../components/Map/DeltaOverlay";
import { Map } from "../components/Map/Map";
import { LargestChangesList } from "../components/LargestChangesList/LargestChangesList";
import { RunPicker } from "../components/RunPicker/RunPicker";
import { SegmentDetailPanel } from "../components/SegmentDetailPanel";
import { useTileSourceConfig } from "../data/useTileSourceConfig";
import { SegmentId } from "../domain";
import { useAppDispatch } from "../state/hooks";
import { openSegment } from "../state/selectedSegment";

export const DeltaView = () => {
  const tileSource = useTileSourceConfig();
  const [map, setMap] = useState<maplibregl.Map | null>(null);
  const dispatch = useAppDispatch();

  const onSegmentClick = useCallback(
    (id: string) => dispatch(openSegment(SegmentId(id))),
    [dispatch]
  );

  if (tileSource.status === "pending") {
    return <div data-testid="delta-view-loading">Loading tile source…</div>;
  }
  if (tileSource.status === "error") {
    return <div data-testid="delta-view-error">Tile source unavailable.</div>;
  }

  return (
    <div data-testid="delta-view" style={rootStyle}>
      <Map tileSourceUrl={tileSource.data.url} onReady={setMap} onSegmentClick={onSegmentClick} />
      <DeltaOverlay map={map} />
      <div style={pickerStackStyle}>
        <RunPicker />
        <DeltaHistogram />
      </div>
      <div style={listStyle}>
        <LargestChangesList />
      </div>
      <SegmentDetailPanel />
    </div>
  );
};

const rootStyle: React.CSSProperties = {
  position: "absolute",
  inset: 0,
};

const pickerStackStyle: React.CSSProperties = {
  position: "absolute",
  top: 56,
  left: 16,
  display: "flex",
  flexDirection: "column",
  gap: 8,
  zIndex: 10,
};

const listStyle: React.CSSProperties = {
  position: "absolute",
  top: 56,
  right: 16,
  zIndex: 10,
};
