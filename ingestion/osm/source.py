"""The `OSMSource` protocol: the seam every OSM provider implements.

This module is the **only** thing the rest of the system depends on for OSM
ingestion. Concrete adapters (osmium-backed, Overpass-backed, …) sit behind
this protocol. Swapping providers is a config change, not a code change.

The protocol is deliberately narrow:

- `fetch(bbox, into_path) -> SnapshotMetadata` — pulls bytes to a local path,
  returning provenance about the snapshot.
- `parse(path, bbox) -> Iterable[RoadSegment]` — turns those bytes into the
  in-memory shape our persistence layer writes.

`fetch` is allowed to be a no-op for sources that don't have a "download"
step (e.g., a test fixture or a locally-cached PBF), provided the caller
has supplied the file at `into_path`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol

from shapely.geometry import LineString


@dataclass(frozen=True, slots=True)
class SnapshotMetadata:
    """Provenance for a single OSM extract download.

    Populated from upstream metadata (HTTP Last-Modified, Geofabrik
    publication header, etc.). `osm_snapshot_date` lands on every
    scoring run that reads this snapshot — see CLAUDE.md reproducibility
    invariant.
    """

    osm_snapshot_date: date
    source_url: str
    local_path: Path
    size_bytes: int
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class RoadSegment:
    """A single parsed OSM way intended for persistence.

    Geometry is always WGS84 (EPSG:4326). The persistence layer enforces
    this via the `geometry(LineString, 4326)` column type — segments with
    other SRIDs are rejected at insert time, not silently reprojected.
    """

    osm_way_id: int
    geometry: LineString
    attrs: dict[str, str] = field(default_factory=dict)


class OSMSource(Protocol):
    """The adapter seam for OSM ingestion."""

    def fetch(self, bbox: tuple[float, float, float, float], into_path: Path) -> SnapshotMetadata:
        """Download (or stat in-place) the source extract.

        Args:
            bbox: (min_lon, min_lat, max_lon, max_lat). Adapters that fetch
                country-/state-level extracts ignore the bbox at download
                time and apply it at parse time.
            into_path: Destination path. Created if missing; reused if the
                upstream extract is unchanged.

        Returns:
            SnapshotMetadata describing the bytes now at `into_path`.
        """
        ...

    def parse(
        self,
        path: Path,
        bbox: tuple[float, float, float, float],
    ) -> Iterable[RoadSegment]:
        """Yield road segments inside `bbox`, in WGS84.

        Args:
            path: Local PBF (or equivalent) file produced by `fetch`.
            bbox: (min_lon, min_lat, max_lon, max_lat) for clipping.

        Yields:
            RoadSegment instances. Implementations must be streaming —
            city-scale extracts (millions of ways) cannot be held entirely
            in memory.
        """
        ...
