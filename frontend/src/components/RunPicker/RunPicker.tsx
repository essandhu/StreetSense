/**
 * RunPicker — Phase 5, Task 3.3.
 *
 * Two dropdowns + a swap button drive the delta-view selection. Source
 * list comes from `useRuns()`; selection lives in the `delta` Redux
 * slice. The picker is presentational over the slice and the server
 * hook — no fetching, no derived server state in Redux.
 *
 * Visual style mirrors the Scrubber's utilitarian dark panel — both
 * are app-level controls, deliberately quiet so the map carries the
 * attention. Polish (timestamp formatting, run-diff hints in the
 * option labels) is a Phase 5.5 / methodology-page concern.
 */
import type { ChangeEvent } from "react";
import { useDispatch, useSelector } from "react-redux";

import { useRuns } from "../../data/useRuns";
import { RunId, type ScoringRunMetadata } from "../../domain";
import { setRunA, setRunB, swapRuns } from "../../state/delta";
import type { RootState } from "../../state/store";

const _selectDelta = (state: RootState) => state.delta;

/**
 * Render one scoring run as a human-readable option label. Phase 5
 * keeps this terse: ISO timestamp + first 8 chars of UUID. The
 * methodology page (Task 5.1) hosts the deeper provenance detail.
 */
function _optionLabel(run: ScoringRunMetadata): string {
  const shortId = String(run.scoring_run_id).slice(0, 8);
  return `${run.scoring_run_timestamp} (${shortId})`;
}

export const RunPicker = () => {
  const dispatch = useDispatch();
  const { runA, runB } = useSelector(_selectDelta);
  const runsQuery = useRuns();

  const handleChange = (which: "a" | "b") => (event: ChangeEvent<HTMLSelectElement>) => {
    const raw = event.target.value;
    const next = raw === "" ? null : RunId(raw);
    dispatch(which === "a" ? setRunA(next) : setRunB(next));
  };

  const runs = runsQuery.data?.runs ?? [];
  const isEmptyServer = runsQuery.isSuccess && runs.length === 0;
  const swapDisabled = runA === null && runB === null;

  return (
    <div data-testid="run-picker" style={containerStyle}>
      {isEmptyServer ? (
        <span style={emptyStyle}>No scoring runs available yet.</span>
      ) : (
        <>
          <label style={labelStyle}>
            Run A
            <select
              aria-label="run A"
              value={runA ?? ""}
              onChange={handleChange("a")}
              style={selectStyle}
            >
              <option value="">— pick a run —</option>
              {runs.map((r) => (
                <option key={`a-${r.scoring_run_id}`} value={String(r.scoring_run_id)}>
                  {_optionLabel(r)}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={() => dispatch(swapRuns())}
            disabled={swapDisabled}
            style={swapButtonStyle(swapDisabled)}
            aria-label="swap selected runs"
          >
            Swap ⇄
          </button>
          <label style={labelStyle}>
            Run B
            <select
              aria-label="run B"
              value={runB ?? ""}
              onChange={handleChange("b")}
              style={selectStyle}
            >
              <option value="">— pick a run —</option>
              {runs.map((r) => (
                <option key={`b-${r.scoring_run_id}`} value={String(r.scoring_run_id)}>
                  {_optionLabel(r)}
                </option>
              ))}
            </select>
          </label>
        </>
      )}
    </div>
  );
};

const containerStyle: React.CSSProperties = {
  background: "rgba(20, 20, 24, 0.85)",
  color: "#f5f5f7",
  padding: "10px 14px",
  borderRadius: 8,
  fontFamily: "system-ui, sans-serif",
  fontSize: 13,
  display: "flex",
  gap: 12,
  alignItems: "flex-end",
};

const labelStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
};

const selectStyle: React.CSSProperties = {
  padding: "4px 6px",
  background: "#202028",
  color: "#f5f5f7",
  border: "1px solid #3a3a44",
  borderRadius: 4,
  fontSize: 13,
  minWidth: 220,
};

const swapButtonStyle = (disabled: boolean): React.CSSProperties => ({
  height: 30,
  padding: "0 10px",
  background: disabled ? "#1a1a20" : "#2c2c38",
  color: disabled ? "#5a5a64" : "#f5f5f7",
  border: "1px solid #3a3a44",
  borderRadius: 4,
  fontSize: 13,
  cursor: disabled ? "not-allowed" : "pointer",
});

const emptyStyle: React.CSSProperties = {
  fontStyle: "italic",
  color: "#a0a0b0",
};
