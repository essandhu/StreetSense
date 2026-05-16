/**
 * ModeToggle — Phase 5, Task 3.7.
 *
 * App-shell-level toggle between single-run (default) and delta
 * comparison. Renders as a two-button segmented control. Clicking
 * the active button is a no-op — avoids a transient slice update
 * that could blow the (runA, runB) state on accident.
 */
import { useDispatch, useSelector } from "react-redux";

import { enterDeltaMode, exitDeltaMode } from "../../state/delta";
import type { RootState } from "../../state/store";

const _selectMode = (s: RootState) => s.delta.mode;

export const ModeToggle = () => {
  const dispatch = useDispatch();
  const mode = useSelector(_selectMode);

  return (
    <div style={containerStyle} data-testid="mode-toggle" role="group" aria-label="View mode">
      <button
        type="button"
        aria-pressed={mode === "single"}
        onClick={() => {
          if (mode !== "single") dispatch(exitDeltaMode());
        }}
        style={buttonStyle(mode === "single")}
      >
        Single run
      </button>
      <button
        type="button"
        aria-pressed={mode === "delta"}
        onClick={() => {
          if (mode !== "delta") dispatch(enterDeltaMode());
        }}
        style={buttonStyle(mode === "delta")}
      >
        Delta
      </button>
    </div>
  );
};

const containerStyle: React.CSSProperties = {
  display: "inline-flex",
  background: "rgba(20, 20, 24, 0.85)",
  borderRadius: 6,
  padding: 2,
  gap: 2,
  fontFamily: "system-ui, sans-serif",
  fontSize: 13,
};

const buttonStyle = (active: boolean): React.CSSProperties => ({
  padding: "6px 12px",
  background: active ? "#3a4f70" : "transparent",
  color: active ? "#f5f5f7" : "#a0a0b0",
  border: 0,
  borderRadius: 4,
  cursor: "pointer",
  fontWeight: active ? 600 : 400,
});
