/**
 * `delta` slice — Phase 5.
 *
 * UI state for the delta-comparison mode. Tracks:
 *
 * - `mode`: whether the app is showing a single scoring run (default) or
 *   comparing two runs side-by-side.
 * - `runA` / `runB`: the two scoring runs the comparison view is pinned
 *   to. Either may be null while the user is mid-selection in the
 *   `RunPicker` (Task 3.3); the `useDelta` query (Task 3.2) only fires
 *   when both are set.
 *
 * Exiting delta mode clears both runs — re-entering should not silently
 * resume a stale pair (a previous run could have been deleted, or the
 * user's intent has changed). Re-selection is cheap; resurrected stale
 * state is not.
 *
 * Server state (the delta response itself) lives in TanStack Query —
 * never put it here.
 */

import { createSlice, type PayloadAction } from "@reduxjs/toolkit";

import type { RunId } from "../domain";

export type DeltaMode = "single" | "delta";

export type DeltaState = {
  mode: DeltaMode;
  runA: RunId | null;
  runB: RunId | null;
};

const initialState: DeltaState = {
  mode: "single",
  runA: null,
  runB: null,
};

const slice = createSlice({
  name: "delta",
  initialState,
  reducers: {
    enterDeltaMode(state) {
      state.mode = "delta";
    },
    exitDeltaMode(state) {
      state.mode = "single";
      state.runA = null;
      state.runB = null;
    },
    setRunA(state, action: PayloadAction<RunId | null>) {
      state.runA = action.payload;
    },
    setRunB(state, action: PayloadAction<RunId | null>) {
      state.runB = action.payload;
    },
    swapRuns(state) {
      const previousA = state.runA;
      state.runA = state.runB;
      state.runB = previousA;
    },
    clearRuns(state) {
      state.runA = null;
      state.runB = null;
    },
  },
});

export const { enterDeltaMode, exitDeltaMode, setRunA, setRunB, swapRuns, clearRuns } =
  slice.actions;
export default slice.reducer;
