/**
 * Clear stale per-city selections when the active city changes —
 * Phase 4b Task 4.8.
 *
 * Task 4.3 already made every server-state hook re-fetch under the
 * new slug (`useSegmentDetail`, `useRuns`, `useDelta` are city-keyed).
 * What's missing is the UI-state side: a selected segment from city
 * A is not a valid segment in city B; a (runA, runB) delta selection
 * refers to two specific runs that exist only under one city's
 * scoring history. Carrying either across a city switch would let
 * the panel try to fetch `/api/cities/cambridge/segments/<phoenix-uuid>`
 * (a guaranteed 404) or `/api/cities/cambridge/runs/<phoenix-uuid>/
 * delta/<phoenix-uuid>` (the same).
 *
 * This hook subscribes to ``state.activeCity.slug`` and, on a real
 * change (not on initial mount), dispatches:
 *
 *   - ``clearSelection`` on the ``selectedSegment`` slice (closes
 *     the panel + nulls the id),
 *   - ``clearRuns`` on the ``delta`` slice (nulls runA + runB,
 *     leaves the mode alone so a delta-mode user stays in delta
 *     mode after switching cities and re-picks runs for the new
 *     city).
 *
 * Why "not on initial mount": at mount, the slug hasn't actually
 * *switched* from anything — it just landed at whatever the URL
 * hydrated to. Clearing then would null fresh selections a
 * just-loaded URL might carry (none today, but the invariant
 * stays defensible).
 */
import { useEffect, useRef } from "react";
import { useDispatch, useSelector } from "react-redux";

import { clearRuns } from "./delta";
import { clearSelection } from "./selectedSegment";
import type { RootState } from "./store";

const _selectActiveCitySlug = (s: RootState) => s.activeCity.slug;

export const useClearSelectionOnCitySwitch = (): void => {
  const dispatch = useDispatch();
  const activeSlug = useSelector(_selectActiveCitySlug);
  const previousSlug = useRef<string | null>(null);

  useEffect(() => {
    if (previousSlug.current === null) {
      previousSlug.current = activeSlug;
      return;
    }
    if (previousSlug.current === activeSlug) return;
    previousSlug.current = activeSlug;
    dispatch(clearSelection());
    dispatch(clearRuns());
  }, [activeSlug, dispatch]);
};
