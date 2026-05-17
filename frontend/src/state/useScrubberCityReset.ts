/**
 * Reset the time scrubber to the active city's local solar noon
 * whenever the city changes — Phase 4b Task 4.7.
 *
 * "Local solar noon" is approximated as 12:00 in the city's IANA
 * local clock time, converted back to UTC. The full astronomical
 * definition (sun at maximum altitude) depends on longitude within
 * the timezone and the equation of time; that fidelity belongs in
 * the scoring layer, not the scrubber's seed value. Local clock
 * noon is the unambiguous "start the day at a reasonable hour"
 * default a user sees on city switch.
 *
 * Examples:
 *
 *   Phoenix (America/Phoenix, UTC-7 year-round, no DST)
 *     local noon → 19:00 UTC
 *   Cambridge (America/New_York, EDT)
 *     local noon → 16:00 UTC (during EDT)
 *   Cambridge (America/New_York, EST)
 *     local noon → 17:00 UTC (during EST)
 *
 * UX consequence: switching cities is a coarse-grained navigation;
 * any hand-tuned scrubber state on the previous city is intentionally
 * discarded. The same-slug case is a no-op so a setActiveCity('phoenix')
 * dispatch from a phoenix-currently-active state doesn't blow away
 * the user's scrub position.
 */
import { useEffect, useRef } from "react";
import { useDispatch, useSelector } from "react-redux";

import { findCityBySlug, useCities } from "../data/useCities";

import { setHourOfDay } from "./scrubber";
import type { RootState } from "./store";

const _selectActiveCitySlug = (s: RootState) => s.activeCity.slug;

/**
 * Return the UTC hour (0-23) at which the local clock in
 * ``timezone`` reads 12:00. Uses :class:`Intl.DateTimeFormat`'s
 * ``shortOffset`` to read the current UTC offset; ``referenceDate``
 * lets tests pin behavior across DST.
 *
 * Returns 12 (UTC noon) on a malformed timezone — better to ship a
 * sane default than throw inside a useEffect.
 */
export const utcHourOfLocalNoon = (
  timezone: string,
  referenceDate: Date = new Date(),
): number => {
  let offsetPart: string;
  try {
    const fmt = new Intl.DateTimeFormat("en-US", {
      timeZone: timezone,
      hour: "2-digit",
      hour12: false,
      timeZoneName: "shortOffset",
    });
    const parts = fmt.formatToParts(referenceDate);
    offsetPart = parts.find((p) => p.type === "timeZoneName")?.value ?? "GMT+0";
  } catch {
    return 12;
  }
  // shortOffset values look like "GMT", "GMT-7", "GMT+05:30", or
  // "GMT+5:45". Pure "GMT" (no sign/digits) is offset 0.
  const match = offsetPart.match(/^GMT(?:([+-])(\d{1,2})(?::(\d{2}))?)?$/);
  if (!match) return 12;
  const sign = match[1] === "-" ? -1 : 1;
  const hours = parseInt(match[2] ?? "0", 10);
  const minutes = parseInt(match[3] ?? "0", 10);
  const offsetHours = sign * (hours + minutes / 60);
  // Local noon = 12. UTC hour = 12 - offset. Round to nearest
  // (Indian Standard Time = +05:30 → 12 - 5.5 = 6.5 → 7), then
  // wrap into [0, 23].
  const utcHour = Math.round(12 - offsetHours);
  return ((utcHour % 24) + 24) % 24;
};

/**
 * Mount once at the App root. Watches the active city slug; on a
 * real change (and once cities have loaded), dispatches
 * ``setHourOfDay`` with the new city's local-noon UTC hour.
 *
 * No-ops:
 *
 * - Until cities load (the slug → bbox/timezone lookup needs the
 *   registry).
 * - When the active slug is the same as the last applied slug
 *   (idempotent under StrictMode double-invoke and avoids
 *   re-applying noon every time something else in the slice
 *   rerenders the hook).
 * - When the active slug isn't in the cities registry (deep-link
 *   to a removed slug — the scrubber stays at its previous value).
 */
export const useScrubberCityReset = (): void => {
  const dispatch = useDispatch();
  const activeSlug = useSelector(_selectActiveCitySlug);
  const cities = useCities();
  const lastAppliedSlug = useRef<string | null>(null);

  useEffect(() => {
    if (!cities.data) return;
    if (lastAppliedSlug.current === activeSlug) return;
    const city = findCityBySlug(cities.data, activeSlug);
    if (!city) return;
    lastAppliedSlug.current = activeSlug;
    dispatch(setHourOfDay(utcHourOfLocalNoon(city.timezone)));
  }, [activeSlug, cities.data, dispatch]);
};
