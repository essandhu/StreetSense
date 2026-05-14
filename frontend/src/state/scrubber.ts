/**
 * `scrubber` slice — Phase 2.
 *
 * UI state for the time scrubber. Carries:
 *
 * - `dayOfYear`: 1..365 (no leap-year handling — Phase 2 scoring runs
 *   on a single representative day; multi-day scrubbing is a Phase 5
 *   polish concern per spec §"Out of Scope").
 * - `hourOfDay`: 0..23 (UTC). The 24 scoring-run samples are hourly UTC,
 *   so the scrubber's resolution is the storage resolution.
 *
 * The default lands on the Cambridge spring equinox morning (DOY 80,
 * hour 11 UTC ≈ 06:00 EDT) so the demo opens on a non-degenerate
 * glare-corridor state instead of midnight.
 */

import { createSlice, type PayloadAction } from "@reduxjs/toolkit";

export type ScrubberState = {
  dayOfYear: number;
  hourOfDay: number;
};

// Cambridge spring equinox morning: DOY 80 is 2025-03-21. Hour 11 UTC
// is ~07:00 EDT — sun is low east, EW arteries light up with glare.
const initialState: ScrubberState = {
  dayOfYear: 80,
  hourOfDay: 11,
};

const slice = createSlice({
  name: "scrubber",
  initialState,
  reducers: {
    setHourOfDay(state, action: PayloadAction<number>) {
      state.hourOfDay = clamp(action.payload, 0, 23);
    },
    setDayOfYear(state, action: PayloadAction<number>) {
      state.dayOfYear = clamp(action.payload, 1, 365);
    },
    setScrubber(state, action: PayloadAction<ScrubberState>) {
      state.hourOfDay = clamp(action.payload.hourOfDay, 0, 23);
      state.dayOfYear = clamp(action.payload.dayOfYear, 1, 365);
    },
  },
});

const clamp = (value: number, lo: number, hi: number): number =>
  Math.min(hi, Math.max(lo, Math.round(value)));

export const { setHourOfDay, setDayOfYear, setScrubber } = slice.actions;
export default slice.reducer;
