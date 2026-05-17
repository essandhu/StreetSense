import { useState } from "react";
import { useSelector } from "react-redux";

import { CitySelector } from "./components/AppShell/CitySelector";
import { ModeToggle } from "./components/ModeToggle/ModeToggle";
import { useActiveCityUrlSync } from "./state/useActiveCityUrlSync";
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

const headerStyle: React.CSSProperties = {
  position: "absolute",
  top: 16,
  left: 16,
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
