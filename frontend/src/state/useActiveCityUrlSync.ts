/**
 * `useActiveCityUrlSync` — Phase 4b Task 4.5.
 *
 * Bidirectional sync between the `activeCity` slice and the URL's
 * `?city=<slug>` query parameter.
 *
 * The plan parenthetically allows "react-router (or equivalent)";
 * the bare HTML5 history API is the lightest equivalent and avoids
 * pulling react-router-dom in for a single query param. If a router
 * becomes necessary later (multi-page navigation, programmatic
 * route guards, nested layouts), this hook collapses into a
 * 5-line `useSearchParams` adapter.
 *
 * Invariants:
 *
 * 1. **The URL is the source of truth on mount.** First effect
 *    reads `window.location.search`, hydrates a slug via
 *    :func:`hydrateActiveCityFromUrl`, and dispatches
 *    `setActiveCity` only if the resolved slug differs from the
 *    current slice value. This lets a fresh tab open on
 *    `?city=austin` and land in Austin without a flash of the
 *    default city.
 *
 * 2. **The slice is the source of truth thereafter.** A subsequent
 *    effect watches `state.activeCity.slug` and writes back to the
 *    URL via `history.replaceState` (NOT pushState — see Reason 1
 *    below). The URL stays in sync after every selector dispatch
 *    without growing the browser history stack.
 *
 * 3. **Browser back / forward re-hydrates.** A `popstate` listener
 *    reads the new URL and dispatches `setActiveCity` to match.
 *    The user's back button moves them between cities cleanly.
 *
 * Reason 1 for `replaceState` over `pushState`: pushing to history
 * on every city switch would let a user trap themselves in a
 * 20-deep stack of phoenix → austin → phoenix → … by alternating
 * the dropdown. `replaceState` keeps the URL truthful without that
 * footgun. The exception is mount-time hydration, which doesn't
 * touch history at all — it only reads.
 */
import { useEffect, useRef } from "react";
import { useDispatch, useSelector } from "react-redux";

import {
  hydrateActiveCityFromUrl,
  setActiveCity,
} from "./activeCity";
import type { RootState } from "./store";

const _selectActiveCitySlug = (s: RootState) => s.activeCity.slug;

/**
 * Mount once at the App root. Returns nothing; the side effects
 * live in three `useEffect`s.
 */
export const useActiveCityUrlSync = (): void => {
  const dispatch = useDispatch();
  const activeSlug = useSelector(_selectActiveCitySlug);
  const hydrated = useRef(false);

  // ----- 1. Mount hydration ----------------------------------------
  // Read the URL once on mount and dispatch if it differs. Using a
  // ref instead of an empty deps array would still re-run under
  // React StrictMode's intentional double-invoke; the guard keeps
  // the dispatch idempotent.
  useEffect(() => {
    if (hydrated.current) return;
    hydrated.current = true;
    const fromUrl = hydrateActiveCityFromUrl(window.location.search);
    if (fromUrl !== activeSlug) {
      dispatch(setActiveCity(fromUrl));
    }
    // activeSlug intentionally omitted from deps — this effect runs
    // once. Subsequent slug changes are handled by the writer
    // effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dispatch]);

  // ----- 2. Writer ------------------------------------------------
  // Mirror every slice change back to the URL. `replaceState` (not
  // `pushState`) avoids growing the back-button stack on each
  // dropdown change.
  useEffect(() => {
    if (!hydrated.current) return; // skip the first paint
    const url = new URL(window.location.href);
    const current = url.searchParams.get("city");
    if (current === activeSlug) return; // nothing to do
    url.searchParams.set("city", activeSlug);
    window.history.replaceState(window.history.state, "", url.toString());
  }, [activeSlug]);

  // ----- 3. popstate re-hydration --------------------------------
  // Back / forward navigation should land on whichever city the
  // history entry recorded. The slice updates, which then triggers
  // the writer effect — but the writer is a no-op when current ===
  // activeSlug, so there's no oscillation.
  useEffect(() => {
    const onPopState = () => {
      const fromUrl = hydrateActiveCityFromUrl(window.location.search);
      dispatch(setActiveCity(fromUrl));
    };
    window.addEventListener("popstate", onPopState);
    return () => {
      window.removeEventListener("popstate", onPopState);
    };
  }, [dispatch]);
};
