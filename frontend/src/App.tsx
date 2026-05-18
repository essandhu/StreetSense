import { useState } from "react";
import { useSelector } from "react-redux";

import { CitySelector } from "./components/AppShell/CitySelector";
import { ModeToggle } from "./components/ModeToggle/ModeToggle";
import { useActiveCityUrlSync } from "./state/useActiveCityUrlSync";
import { useClearSelectionOnCitySwitch } from "./state/useClearSelectionOnCitySwitch";
import { useScrubberCityReset } from "./state/useScrubberCityReset";
import type { RootState } from "./state/store";
import { DeltaView } from "./views/DeltaView";
import { MapView } from "./views/MapView";
import { MethodologyView } from "./views/MethodologyView";

import "./App.css";

const _selectMode = (s: RootState) => s.delta.mode;

const App = () => {
  const mode = useSelector(_selectMode);
  const [showMethodology, setShowMethodology] = useState(false);
  // Phase 4b Task 4.5: bidirectional ?city=<slug> URL sync. Mount-time
  // hydration so a deep-link lands on the right city without a flash;
  // writer mirrors slice → URL via replaceState (no history bloat);
  // popstate re-hydrates on browser back / forward.
  useActiveCityUrlSync();
  // Phase 4b Task 4.7: on every city switch, reset the scrubber to
  // that city's local solar noon. Mount-time fire is intentional —
  // a fresh deep-link should land at the new city's noon rather
  // than the scrubber slice's cambridge-shaped default.
  useScrubberCityReset();
  // Phase 4b Task 4.8: clear segment + run selections on city switch.
  // A segment from city A isn't valid in city B; a (runA, runB) pair
  // refers to one city's scoring history. Mount-time is a no-op so
  // pre-existing selections survive the first render.
  useClearSelectionOnCitySwitch();

  return (
    <div style={{ position: "absolute", inset: 0 }}>
      {showMethodology ? <MethodologyView /> : mode === "delta" ? <DeltaView /> : <MapView />}
      <div style={headerStyle}>
        <CitySelector />
        <ModeToggle />
        <button
          type="button"
          onClick={() => setShowMethodology((prev) => !prev)}
          style={methodologyButtonStyle(showMethodology)}
          aria-pressed={showMethodology}
        >
          {showMethodology ? "Close methodology" : "Methodology"}
        </button>
      </div>
    </div>
  );
};

// Phase 4b Task 4.4 added the CitySelector to this header; the
// header originally sat at top-left and clickjacked the existing
// LayerToggle (top-left, z-index 5). Moving the shell controls to
// the top-right keeps map controls (LayerToggle, delta run-picker)
// in their pre-Phase-4b position while giving the new shell
// controls (City + Mode + Methodology) their own corner.
const headerStyle: React.CSSProperties = {
  position: "absolute",
  top: 16,
  right: 16,
  zIndex: 20,
  display: "flex",
  gap: 8,
  alignItems: "center",
};

const methodologyButtonStyle = (active: boolean): React.CSSProperties => ({
  background: active ? "#3a4f70" : "rgba(20, 20, 24, 0.85)",
  color: "#f5f5f7",
  padding: "8px 14px",
  border: 0,
  borderRadius: 6,
  fontFamily: "system-ui, sans-serif",
  fontSize: 13,
  fontWeight: active ? 600 : 400,
  cursor: "pointer",
});

export default App;
