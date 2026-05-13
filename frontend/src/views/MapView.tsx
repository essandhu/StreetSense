/**
 * MapView — Phase 1's single composition.
 *
 * Wires `useTileSourceConfig` (server state) into the imperative `Map`
 * component. The Redux viewport slice is wired in `main.tsx`'s Provider;
 * the Map currently consumes only the URL and shows the OSM basemap +
 * stub-colored road segments.
 */

import { Map } from "../components/Map/Map";
import { useTileSourceConfig } from "../data/useTileSourceConfig";

export const MapView = () => {
  const tileSource = useTileSourceConfig();

  if (tileSource.status === "pending") {
    return <div data-testid="map-loading">Loading tile source…</div>;
  }
  if (tileSource.status === "error") {
    return <div data-testid="map-error">Tile source unavailable.</div>;
  }

  return <Map tileSourceUrl={tileSource.data.url} />;
};
