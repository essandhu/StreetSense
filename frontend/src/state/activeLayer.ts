/**
 * `activeLayer` slice — Phase 4.
 *
 * Single-select which thematic layer is rendered over the basemap.
 *
 * - ``composite`` — Phase 4's headline layer, default on at app boot
 *   per spec AC-8. Reads ``composite_risk`` from the tile features.
 * - ``glare`` / ``lane`` / ``junction`` / ``historical`` — the four
 *   real sub-score layers; secondary toggleable views the user can
 *   switch into to inspect any one input to the composite. Reads the
 *   corresponding attribute from the same tile source (the Phase 4
 *   tile function returns all four plus composite + uplift).
 *
 * Single-select rather than multi-toggle because the four sub-scores
 * + composite share a single color channel on the screen; stacking
 * two of them would just produce a muddy blend. Switching is cheap
 * (no new tile fetch — same URL, different accessor).
 *
 * UI state only — TanStack Query owns the tile URL.
 */
import { createSlice, type PayloadAction } from "@reduxjs/toolkit";

export const LAYER_IDS = [
  "composite",
  "glare",
  "lane",
  "junction",
  "historical",
] as const;

export type LayerId = (typeof LAYER_IDS)[number];

export type ActiveLayerState = {
  layer: LayerId;
};

const initialState: ActiveLayerState = {
  // Spec AC-8: the composite-risk layer is default-on at app boot.
  layer: "composite",
};

const slice = createSlice({
  name: "activeLayer",
  initialState,
  reducers: {
    setActiveLayer(state, action: PayloadAction<LayerId>) {
      state.layer = action.payload;
    },
  },
});

export const { setActiveLayer } = slice.actions;
export default slice.reducer;
