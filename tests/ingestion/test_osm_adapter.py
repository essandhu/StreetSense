"""Tests for the OSM adapter — Task 1.4.2 (test-first).

These run against the committed `tests/fixtures/tiny_extract.osm` so CI
needs no live network. The adapter must:

- Yield exactly the highway ways inside the bbox (3 of the 5 ways in the
  fixture).
- Filter out the building (non-highway).
- Filter out the way whose nodes lie outside the bbox.
- Produce `LineString` geometries in WGS84.
- Populate `SnapshotMetadata.osm_snapshot_date`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from shapely.geometry import LineString

from ingestion.osm import OSMSource, RoadSegment
from ingestion.osm.osmium_adapter import OsmiumOSMSource

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "tiny_extract.osm"
FIXTURE_BBOX = (-71.10, 42.36, -71.08, 42.38)


# --- Adapter wiring -------------------------------------------------------
def test_osmium_adapter_implements_protocol() -> None:
    """Structural subtype check — caller-side guarantee, not nominal."""
    adapter: OSMSource = OsmiumOSMSource()
    assert hasattr(adapter, "fetch")
    assert hasattr(adapter, "parse")


# --- parse() --------------------------------------------------------------
@pytest.fixture
def parsed_segments() -> list[RoadSegment]:
    adapter = OsmiumOSMSource()
    return list(adapter.parse(FIXTURE_PATH, FIXTURE_BBOX))


def test_parse_yields_only_highways_inside_bbox(parsed_segments: list[RoadSegment]) -> None:
    osm_ids = sorted(seg.osm_way_id for seg in parsed_segments)
    # 3001, 3002, 3003 are highway ways inside the bbox.
    # 3004 (highway but outside) and 3005 (building, not highway) are excluded.
    assert osm_ids == [3001, 3002, 3003]


def test_each_segment_has_linestring_geometry(parsed_segments: list[RoadSegment]) -> None:
    for seg in parsed_segments:
        assert isinstance(seg.geometry, LineString), f"{seg.osm_way_id} is not a LineString"
        # Real geometries have ≥2 coordinates.
        assert len(seg.geometry.coords) >= 2


def test_geometry_coords_are_wgs84_lon_lat(parsed_segments: list[RoadSegment]) -> None:
    """Sanity-check coordinate ordering: shapely keeps (x, y) = (lon, lat).

    The fixture's lon range is ~[-71.10, -71.08] and lat ~[42.36, 42.38]. If
    an adapter ever returned (lat, lon) by mistake, the y values would
    exceed plausible longitudes.
    """
    for seg in parsed_segments:
        for x, y in seg.geometry.coords:
            assert -71.10 <= x <= -71.08
            assert 42.36 <= y <= 42.38


def test_attrs_preserve_highway_tag(parsed_segments: list[RoadSegment]) -> None:
    by_id = {seg.osm_way_id: seg for seg in parsed_segments}
    assert by_id[3001].attrs["highway"] == "primary"
    assert by_id[3002].attrs["highway"] == "residential"
    assert by_id[3003].attrs["highway"] == "service"


def test_attrs_preserve_arbitrary_tags(parsed_segments: list[RoadSegment]) -> None:
    by_id = {seg.osm_way_id: seg for seg in parsed_segments}
    assert by_id[3001].attrs.get("name") == "Test Primary"
    assert by_id[3001].attrs.get("maxspeed") == "30 mph"


# --- fetch() --------------------------------------------------------------
def test_fetch_against_local_path_yields_snapshot_metadata(tmp_path: Path) -> None:
    """fetch() must accept a local path for tests — no live network.

    The adapter copies / hard-links the fixture into `into_path` and returns
    SnapshotMetadata with osm_snapshot_date populated.
    """
    target = tmp_path / "fixture.osm"
    target.write_bytes(FIXTURE_PATH.read_bytes())
    adapter = OsmiumOSMSource(prefetched=target)

    metadata = adapter.fetch(FIXTURE_BBOX, target)

    assert metadata.local_path == target
    assert metadata.size_bytes == target.stat().st_size
    assert isinstance(metadata.osm_snapshot_date, date)
    assert metadata.source_url  # non-empty
