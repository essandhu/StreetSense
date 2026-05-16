import { useSelector } from "react-redux";

import { ModeToggle } from "./components/ModeToggle/ModeToggle";
import type { RootState } from "./state/store";
import { DeltaView } from "./views/DeltaView";
import { MapView } from "./views/MapView";

import "./App.css";

const _selectMode = (s: RootState) => s.delta.mode;

const App = () => {
  const mode = useSelector(_selectMode);
  return (
    <div style={{ position: "absolute", inset: 0 }}>
      {mode === "delta" ? <DeltaView /> : <MapView />}
      <div style={modeToggleSlotStyle}>
        <ModeToggle />
      </div>
    </div>
  );
};

const modeToggleSlotStyle: React.CSSProperties = {
  position: "absolute",
  top: 16,
  left: 16,
  zIndex: 20,
};

export default App;
