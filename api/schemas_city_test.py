"""Shape tests for the Phase 4b City schema.

These are *schema-only* tests — slug regex, bbox shape, IANA timezone,
default_zoom bounds. The DB-side schema (cities table, GIST, NOT NULL,
backfill) is exercised by ``tests/db/test_cities_schema.py``; the
end-to-end seed loop by ``tests/ingestion/test_seed_cities.py`` (Task
1.6).

The City Pydantic model is the single source of truth for the shape
that crosses both the API boundary (``GET /api/cities``) and the
seed-cities loader. Per-call YAML-config-to-City conversion is
:meth:`api.schemas.City.from_config` (Task 1.6); this file only
exercises ``City`` itself.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from api.schemas import City


# -- Construction helpers --------------------------------------------------


def _valid_kwargs(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "slug": "phoenix",
        "name": "Phoenix, AZ",
        "bbox": (-112.32, 33.29, -111.93, 33.92),
        "default_zoom": 11,
        "timezone": "America/Phoenix",
    }
    defaults.update(overrides)
    return defaults


# -- Valid construction ----------------------------------------------------


def test_city_accepts_all_required_fields() -> None:
    city = City(**_valid_kwargs())  # type: ignore[arg-type]
    assert city.slug == "phoenix"
    assert city.name == "Phoenix, AZ"
    assert city.bbox == (-112.32, 33.29, -111.93, 33.92)
    assert city.default_zoom == 11
    assert city.timezone == "America/Phoenix"
    assert city.id is None  # id is optional — seeder fills it from the DB


def test_city_accepts_id_when_returned_by_api() -> None:
    cid = uuid4()
    city = City(id=cid, **_valid_kwargs())  # type: ignore[arg-type]
    assert city.id == cid


def test_city_roundtrip_via_model_dump_and_validate() -> None:
    city = City(**_valid_kwargs())  # type: ignore[arg-type]
    dumped = city.model_dump()
    restored = City.model_validate(dumped)
    assert restored == city


# -- Slug validation -------------------------------------------------------


@pytest.mark.parametrize(
    "slug",
    ["phoenix", "san-francisco", "austin", "los-angeles", "cambridge", "city_with_underscore"],
)
def test_valid_slugs_accepted(slug: str) -> None:
    city = City(**_valid_kwargs(slug=slug))  # type: ignore[arg-type]
    assert city.slug == slug


@pytest.mark.parametrize(
    "slug",
    [
        "Phoenix",  # uppercase
        "San Francisco",  # space
        "1city",  # leading digit
        "-phoenix",  # leading dash
        "phoenix!",  # punctuation
        "",  # empty
        "city.with.dots",  # dots
    ],
)
def test_invalid_slugs_rejected(slug: str) -> None:
    with pytest.raises(ValidationError):
        City(**_valid_kwargs(slug=slug))  # type: ignore[arg-type]


# -- bbox validation -------------------------------------------------------


def test_bbox_requires_four_elements() -> None:
    with pytest.raises(ValidationError):
        City(**_valid_kwargs(bbox=(1.0, 2.0, 3.0)))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        City(**_valid_kwargs(bbox=(1.0, 2.0, 3.0, 4.0, 5.0)))  # type: ignore[arg-type]


def test_bbox_rejects_min_greater_than_max_for_longitude() -> None:
    # min_lon > max_lon — degenerate bbox.
    with pytest.raises(ValidationError):
        City(**_valid_kwargs(bbox=(10.0, 0.0, -10.0, 1.0)))  # type: ignore[arg-type]


def test_bbox_rejects_min_greater_than_max_for_latitude() -> None:
    with pytest.raises(ValidationError):
        City(**_valid_kwargs(bbox=(-10.0, 10.0, 10.0, -10.0)))  # type: ignore[arg-type]


def test_bbox_rejects_out_of_range_longitude() -> None:
    with pytest.raises(ValidationError):
        City(**_valid_kwargs(bbox=(-181.0, 0.0, 0.0, 1.0)))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        City(**_valid_kwargs(bbox=(0.0, 0.0, 181.0, 1.0)))  # type: ignore[arg-type]


def test_bbox_rejects_out_of_range_latitude() -> None:
    with pytest.raises(ValidationError):
        City(**_valid_kwargs(bbox=(-1.0, -91.0, 1.0, 0.0)))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        City(**_valid_kwargs(bbox=(-1.0, 0.0, 1.0, 91.0)))  # type: ignore[arg-type]


# -- timezone validation ---------------------------------------------------


@pytest.mark.parametrize(
    "tz",
    [
        "America/Phoenix",
        "America/Los_Angeles",
        "America/Chicago",
        "America/New_York",
        "UTC",
        "Europe/London",
    ],
)
def test_valid_iana_timezones_accepted(tz: str) -> None:
    city = City(**_valid_kwargs(timezone=tz))  # type: ignore[arg-type]
    assert city.timezone == tz


@pytest.mark.parametrize(
    "tz",
    [
        "America/NotARealCity",  # plausibly-shaped but unknown
        "PST",  # legacy short-name, not IANA-valid via zoneinfo
        "",  # empty
        "GMT+5",  # POSIX offset, not IANA
    ],
)
def test_invalid_timezones_rejected(tz: str) -> None:
    with pytest.raises(ValidationError):
        City(**_valid_kwargs(timezone=tz))  # type: ignore[arg-type]


# -- default_zoom validation -----------------------------------------------


@pytest.mark.parametrize("zoom", [1, 5, 11, 18, 22])
def test_default_zoom_accepts_realistic_values(zoom: int) -> None:
    city = City(**_valid_kwargs(default_zoom=zoom))  # type: ignore[arg-type]
    assert city.default_zoom == zoom


@pytest.mark.parametrize("zoom", [0, -1, 23, 100])
def test_default_zoom_rejects_out_of_range(zoom: int) -> None:
    with pytest.raises(ValidationError):
        City(**_valid_kwargs(default_zoom=zoom))  # type: ignore[arg-type]


# -- name validation -------------------------------------------------------


def test_name_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        City(**_valid_kwargs(name=""))  # type: ignore[arg-type]


# -- id roundtrip ----------------------------------------------------------


def test_id_accepts_uuid_string_form() -> None:
    cid = uuid4()
    city = City(id=str(cid), **_valid_kwargs())  # type: ignore[arg-type]
    assert isinstance(city.id, UUID)
    assert city.id == cid
