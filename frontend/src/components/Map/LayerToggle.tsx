/**
 * LayerToggle — Phase 4 layer-switcher widget.
 *
 * Renders a small floating control panel with one radio button per
 * thematic layer. Reads/writes the ``activeLayer`` Redux slice;
 * single-select so the color channel stays legible. The default
 * (``composite``) is the only one labelled "primary" per spec AC-8;
 * the others are secondary sub-score inspection views.
 *
 * Keyboard navigation falls out of native ``<input type="radio">``
 * grouping — arrow keys move focus through the options.
 */

import { useSelector } from "react-redux";

import {
  LAYER_IDS,
  type LayerId,
  setActiveLayer,
} from "../../state/activeLayer";
import { useAppDispatch } from "../../state/hooks";
import type { RootState } from "../../state/store";

import "./LayerToggle.css";

const LABELS: Readonly<Record<LayerId, string>> = {
  composite: "Composite risk",
  glare: "Glare",
  lane: "Lane marking",
  junction: "Junction complexity",
  historical: "Historical correlation",
};

export const LayerToggle = () => {
  const dispatch = useAppDispatch();
  const activeLayer = useSelector((s: RootState) => s.activeLayer.layer);

  return (
    <fieldset
      className="layer-toggle"
      data-testid="layer-toggle"
      aria-label="Map layer"
    >
      <legend className="layer-toggle__legend">Layer</legend>
      {LAYER_IDS.map((id) => {
        const checked = activeLayer === id;
        return (
          <label
            key={id}
            className={`layer-toggle__option${checked ? " layer-toggle__option--active" : ""}`}
            data-active={checked ? "true" : "false"}
          >
            <input
              type="radio"
              name="active-layer"
              value={id}
              checked={checked}
              onChange={() => dispatch(setActiveLayer(id))}
              data-testid={`layer-toggle-${id}`}
            />
            <span>
              {LABELS[id]}
              {id === "composite" && (
                <span className="layer-toggle__primary"> (primary)</span>
              )}
            </span>
          </label>
        );
      })}
    </fieldset>
  );
};
