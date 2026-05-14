/**
 * `selectedSegment` slice — Phase 3.6.11.
 *
 * UI state only:
 *   - `segmentId`: the segment the user clicked, or null when none.
 *   - `isPanelOpen`: whether the segment-detail panel is visible.
 *
 * Server state (the segment-detail response itself) lives in TanStack
 * Query — never put it here.
 */
import { createSlice, type PayloadAction } from "@reduxjs/toolkit";

import type { SegmentId } from "../domain";

export type SelectedSegmentState = {
  segmentId: SegmentId | null;
  isPanelOpen: boolean;
};

const initialState: SelectedSegmentState = {
  segmentId: null,
  isPanelOpen: false,
};

const slice = createSlice({
  name: "selectedSegment",
  initialState,
  reducers: {
    openSegment(state, action: PayloadAction<SegmentId>) {
      state.segmentId = action.payload;
      state.isPanelOpen = true;
    },
    closePanel(state) {
      state.isPanelOpen = false;
    },
    togglePanel(state) {
      state.isPanelOpen = !state.isPanelOpen;
    },
    clearSelection(state) {
      state.segmentId = null;
      state.isPanelOpen = false;
    },
  },
});

export const { openSegment, closePanel, togglePanel, clearSelection } = slice.actions;
export default slice.reducer;
