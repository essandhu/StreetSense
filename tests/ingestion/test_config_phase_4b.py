"""Phase 4b extension tests for ingestion/config.py CityConfig.

The Phase 4b schema work (Task 1.5) extends the YAML city-config
format with three new keys — ``slug``, ``default_zoom``, ``timezone``
— and an optional ``notes`` key (per ADR 0010). The four curated new
cities (phoenix, san-francisco, austin, los-angeles) each ship a
config file; cambridge is backfilled with the new keys to keep the
shipped set consistent.

These tests assert:

1. The extended ``CityConfig`` dataclass exposes the new fields.
2. ``load_city`` reads them from the YAML and surfaces them on the
   returned config.
3. The schema rejects YAML files missing any of the now-required keys.
4. Each of the five shipped cities (cambridge + four curated) loads
   without error and surfaces consistent metadata.
5. ``CityConfig.to_city()`` produces a valid ``api.schemas.City`` —
   the seed-cities loader (Task 1.6) drives the cities table from this.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from jsonschema import ValidationError

from api.schemas import City
from ingestion.config import DEFAULT_CONFIG_DIR, load_city

REPO_ROOT = Path(__file__).resolve().parents[2]

SHIPPED_CITIES = ("cambridge", "phoenix", "san-francisco", "austin", "los-angeles")


# -- Extended CityConfig fields -------------------------------------------


class TestCityConfigPhase4bFields:
    def test_city_config_has_slug_attribute(self) -> None:
        cfg = load_city("cambridge")
        assert cfg.slug == "cambridge"

    def test_city_config_has_default_zoom_attribute(self) -> None:
        cfg = load_city("cambridge")
        assert isinstance(cfg.default_zoom, int)
        assert 1 <= cfg.default_zoom <= 22

    def test_city_config_has_timezone_attribute(self) -> None:
        cfg = load_city("cambridge")
        assert isinstance(cfg.timezone, str)
        # IANA name — exact value is in the YAML; just assert non-empty.
        assert cfg.timezone

    def test_city_config_has_notes_attribute_optional(self) -> None:
        cfg = load_city("cambridge")
        assert hasattr(cfg, "notes")
        # Either None or non-empty string.
        assert cfg.notes is None or isinstance(cfg.notes, str)


# -- All shipped cities load -----------------------------------------------


@pytest.mark.parametrize("slug", SHIPPED_CITIES)
def test_shipped_city_loads_without_error(slug: str) -> None:
    cfg = load_city(slug)
    assert cfg.slug == slug
    assert cfg.name == slug  # YAML 'name' field still equals slug for back-compat
    assert cfg.bbox
    assert cfg.geofabrik_extract_url.startswith("https://")
    assert cfg.timezone
    assert cfg.default_zoom > 0


@pytest.mark.parametrize("slug", SHIPPED_CITIES)
def test_shipped_city_bbox_is_valid_wgs84_range(slug: str) -> None:
    cfg = load_city(slug)
    min_lon, min_lat, max_lon, max_lat = cfg.bbox
    assert -180.0 <= min_lon < max_lon <= 180.0
    assert -90.0 <= min_lat < max_lat <= 90.0


@pytest.mark.parametrize("slug", SHIPPED_CITIES)
def test_shipped_city_converts_to_city_pydantic_model(slug: str) -> None:
    cfg = load_city(slug)
    city = cfg.to_city()
    assert isinstance(city, City)
    assert city.slug == slug
    assert city.bbox == cfg.bbox
    assert city.default_zoom == cfg.default_zoom
    assert city.timezone == cfg.timezone
    # id is None pre-insert; the seeder fills it.
    assert city.id is None


# -- Schema rejects missing-required-key files ----------------------------


def _write_yaml(tmp_path: Path, slug: str, body: dict[str, object]) -> Path:
    schema_src = DEFAULT_CONFIG_DIR / "__schema__.yaml"
    schema_dst = tmp_path / "__schema__.yaml"
    schema_dst.write_text(schema_src.read_text(encoding="utf-8"), encoding="utf-8")
    target = tmp_path / f"{slug}.yaml"
    target.write_text(yaml.safe_dump(body), encoding="utf-8")
    return target


@pytest.mark.parametrize("missing_key", ["slug", "default_zoom", "timezone"])
def test_yaml_missing_phase_4b_required_key_fails(tmp_path: Path, missing_key: str) -> None:
    """Removing any of the three newly-required keys must fail validation."""
    valid = {
        "slug": "phoenix",
        "name": "phoenix",
        "display_name": "Phoenix, AZ",
        "bbox": [-112.32, 33.29, -111.93, 33.92],
        "default_zoom": 11,
        "timezone": "America/Phoenix",
        "geofabrik_extract_url": "https://example.invalid/arizona.osm.pbf",
        "local_cache_path": "data/osm/arizona.osm.pbf",
    }
    del valid[missing_key]
    _write_yaml(tmp_path, "phoenix", valid)
    with pytest.raises(ValidationError):
        load_city("phoenix", config_dir=tmp_path)


def test_yaml_with_invalid_slug_pattern_fails(tmp_path: Path) -> None:
    valid = {
        "slug": "Phoenix",  # uppercase — must fail
        "name": "phoenix",
        "bbox": [-112.32, 33.29, -111.93, 33.92],
        "default_zoom": 11,
        "timezone": "America/Phoenix",
        "geofabrik_extract_url": "https://example.invalid/arizona.osm.pbf",
        "local_cache_path": "data/osm/arizona.osm.pbf",
    }
    _write_yaml(tmp_path, "phoenix", valid)
    with pytest.raises(ValidationError):
        load_city("phoenix", config_dir=tmp_path)


def test_yaml_with_default_zoom_out_of_range_fails(tmp_path: Path) -> None:
    valid = {
        "slug": "phoenix",
        "name": "phoenix",
        "bbox": [-112.32, 33.29, -111.93, 33.92],
        "default_zoom": 25,  # > 22
        "timezone": "America/Phoenix",
        "geofabrik_extract_url": "https://example.invalid/arizona.osm.pbf",
        "local_cache_path": "data/osm/arizona.osm.pbf",
    }
    _write_yaml(tmp_path, "phoenix", valid)
    with pytest.raises(ValidationError):
        load_city("phoenix", config_dir=tmp_path)


# -- Single default city posture preserved --------------------------------


def test_exactly_one_city_marked_default() -> None:
    """At most one shipped city has ``default: true``. Cambridge keeps it
    (Phase 1 demo continuity); the four curated additions ship with
    ``default: false``.
    """
    defaults = []
    for slug in SHIPPED_CITIES:
        cfg = load_city(slug)
        if cfg.default:
            defaults.append(slug)
    assert len(defaults) == 1, f"Exactly one city must be the default; got {defaults!r}"
    assert defaults[0] == "cambridge"


# -- CityConfig is hashable + immutable ----------------------------------


def test_city_config_is_frozen_dataclass() -> None:
    import dataclasses

    cfg = load_city("cambridge")
    # frozen=True, slots=True per existing convention
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.slug = "other"  # type: ignore[misc]


# -- New cities ship the right timezones ----------------------------------


@pytest.mark.parametrize(
    ("slug", "expected_tz"),
    [
        ("cambridge", "America/New_York"),
        ("phoenix", "America/Phoenix"),
        ("san-francisco", "America/Los_Angeles"),
        ("austin", "America/Chicago"),
        ("los-angeles", "America/Los_Angeles"),
    ],
)
def test_each_city_has_expected_timezone(slug: str, expected_tz: str) -> None:
    cfg = load_city(slug)
    assert cfg.timezone == expected_tz, (
        f"{slug} should ship with timezone {expected_tz}; got {cfg.timezone}"
    )
