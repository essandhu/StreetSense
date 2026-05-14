/**
 * Scrubber — Phase 2 time scrubber.
 *
 * Thin presentational component over two numeric inputs (hour-of-day
 * and day-of-year) that read from / write to the `scrubber` Redux
 * slice. No calendar widget — that's a Phase 5 polish concern per
 * spec §"Out of Scope".
 *
 * Visual style is utilitarian: the deliberate-motion identity from the
 * architecture document is a Phase 5 polish concern as well; what
 * matters in Phase 2 is that the inputs dispatch the right actions and
 * the deck.gl overlay re-fetches the snapped tile.
 */

import { useDispatch, useSelector } from "react-redux";

import { setDayOfYear, setHourOfDay, type ScrubberState } from "../../state/scrubber";
import type { RootState } from "../../state/store";

const scrubberSelector = (state: RootState): ScrubberState => state.scrubber;

export const Scrubber = () => {
  const { hourOfDay, dayOfYear } = useSelector(scrubberSelector);
  const dispatch = useDispatch();

  return (
    <div
      data-testid="scrubber"
      style={{
        position: "absolute",
        bottom: 16,
        left: 16,
        background: "rgba(20, 20, 24, 0.85)",
        color: "#f5f5f7",
        padding: "10px 14px",
        borderRadius: 8,
        fontFamily: "system-ui, sans-serif",
        fontSize: 13,
        display: "flex",
        gap: 12,
        alignItems: "center",
        zIndex: 10,
      }}
    >
      <label style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        Hour (UTC)
        <input
          type="number"
          aria-label="hour"
          min={0}
          max={23}
          step={1}
          value={hourOfDay}
          onChange={(e) => dispatch(setHourOfDay(Number(e.target.value)))}
          style={inputStyle}
        />
      </label>
      <label style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        Day of year
        <input
          type="number"
          aria-label="day of year"
          min={1}
          max={365}
          step={1}
          value={dayOfYear}
          onChange={(e) => dispatch(setDayOfYear(Number(e.target.value)))}
          style={inputStyle}
        />
      </label>
    </div>
  );
};

const inputStyle: React.CSSProperties = {
  width: 64,
  padding: "4px 6px",
  background: "#202028",
  color: "#f5f5f7",
  border: "1px solid #3a3a44",
  borderRadius: 4,
  fontSize: 13,
};
