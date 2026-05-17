/**
 * CitySelector — Phase 4b Task 4.4.
 *
 * App-shell-level dropdown that lets the user switch the active city.
 * The list comes from :func:`useCities` (server state — the global
 * `/api/cities` registry, never per-city), and selection dispatches
 * :func:`setActiveCity` to update the slice that every other Phase 4b
 * subscriber reads (tile URLs, MapLibre fitBounds, scrubber local-noon
 * reset, segment-detail rebind).
 *
 * Renders as a native ``<select>`` rather than a custom listbox: it
 * gets keyboard navigation, screen-reader support, and mobile-native
 * pickers for free. The dropdown is positioned next to the existing
 * ModeToggle and Methodology buttons in the app shell.
 *
 * Defensive states:
 *
 * - **Loading.** Shows a disabled placeholder while the cities query
 *   resolves. The query is `staleTime: Infinity` and the response
 *   ETag is small, so this state is rare after the first paint.
 * - **Error.** Shows a disabled error label rather than disappearing.
 *   The fallback keeps the app usable on the default city even if
 *   the registry endpoint is briefly unreachable.
 * - **Slug not in list.** If the active slug isn't found in the
 *   loaded cities (e.g., a deep-link to a since-removed slug), the
 *   `<select>` shows no selected option; clicking still dispatches
 *   the chosen slug. The activeCity slice's setActiveCity reducer
 *   normalizes anything we hand it.
 */
import { useDispatch, useSelector } from "react-redux";

import { useCities } from "../../data/useCities";
import { setActiveCity } from "../../state/activeCity";
import type { RootState } from "../../state/store";

const _selectActiveCitySlug = (s: RootState) => s.activeCity.slug;

export const CitySelector = () => {
  const dispatch = useDispatch();
  const activeSlug = useSelector(_selectActiveCitySlug);
  const { data, isPending, isError } = useCities();

  if (isPending) {
    return (
      <select
        disabled
        aria-label="City"
        data-testid="city-selector"
        style={selectStyle(true)}
      >
        <option>Loading cities…</option>
      </select>
    );
  }
  if (isError || !data) {
    return (
      <select
        disabled
        aria-label="City"
        data-testid="city-selector"
        style={selectStyle(true)}
      >
        <option>Cities unavailable</option>
      </select>
    );
  }

  return (
    <select
      aria-label="City"
      data-testid="city-selector"
      value={activeSlug}
      onChange={(e) => {
        dispatch(setActiveCity(e.target.value));
      }}
      style={selectStyle(false)}
    >
      {data.cities.map((city) => (
        <option key={city.slug} value={city.slug}>
          {city.name}
        </option>
      ))}
    </select>
  );
};

const selectStyle = (disabled: boolean): React.CSSProperties => ({
  background: "rgba(20, 20, 24, 0.85)",
  color: disabled ? "#7a7a85" : "#f5f5f7",
  padding: "6px 12px",
  border: 0,
  borderRadius: 6,
  fontFamily: "system-ui, sans-serif",
  fontSize: 13,
  cursor: disabled ? "default" : "pointer",
  appearance: "auto",
});
