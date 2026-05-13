/**
 * `viewport` slice — Phase 1's only Redux state.
 *
 * Carries the current map zoom + center so future features (scrubber,
 * minimap, deep-link URL sync) can read/write it without each owning its
 * own copy. The slice is intentionally tiny in Phase 1; Phase 2 adds
 * `scrubber`, Phase 3 adds `selection`.
 */

import { createSlice, type PayloadAction } from "@reduxjs/toolkit";

export type Viewport = {
  center: [number, number];
  zoom: number;
};

const initialState: Viewport = {
  center: [-71.1097, 42.3736], // Cambridge, MA
  zoom: 12,
};

const slice = createSlice({
  name: "viewport",
  initialState,
  reducers: {
    setCenter(state, action: PayloadAction<[number, number]>) {
      state.center = action.payload;
    },
    setZoom(state, action: PayloadAction<number>) {
      state.zoom = action.payload;
    },
    setViewport(state, action: PayloadAction<Viewport>) {
      state.center = action.payload.center;
      state.zoom = action.payload.zoom;
    },
  },
});

export const { setCenter, setZoom, setViewport } = slice.actions;
export default slice.reducer;
