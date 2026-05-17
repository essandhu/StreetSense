"""Tests for the Phase 4b `make seed-cities` command (Task 1.6).

The seeder reads every `config/cities/*.yaml` and UPSERTs into the
``cities`` table by slug. It is idempotent: re-running against an
already-seeded DB yields the same row count and updates rows whose
YAML changed.

These tests pin:

1. All five shipped cities (cambridge + four curated additions) land
   in the DB after one run.
2. Re-running does not duplicate rows.
3. Re-running with a changed YAML updates the existing row.
4. Unknown / extra YAMLs in a custom config dir are seeded as well —
   this is the AC-4 "add a city by writing one YAML" path.
5. The seeder returns a summary structure with insert / update counts.
6. The bbox in the DB is a valid Polygon with SRID 4326 after seeding
   (the YAML 4-tuple is converted via ST_MakeEnvelope).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml

from ingestion.seed_cities import SeedSummary, seed_cities

pytestmark = pytest.mark.integration


SHIPPED_CITIES = ("cambridge", "phoenix", "san-francisco", "austin", "los-angeles")


@pytest.fixture(autouse=True)
def _wipe_test_cities(owner_conn: psycopg.Connection[Any]) -> None:
    """Cities added during a test are removed afterward.

    We never wipe the rows that other tests + Phase 1-4 backfill assume
    (cambridge specifically). Instead the autouse fixture is a no-op
    for cambridge but removes any test-only slugs the seeder created.

    The seeder is idempotent so re-creating the five shipped rows on
    every test is fine.
    """
    yield
    with owner_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM cities WHERE slug LIKE 'test-%'",
        )
    owner_conn.commit()


def _row(owner_conn: psycopg.Connection[Any], slug: str) -> tuple[str, str, int, str] | None:
    with owner_conn.cursor() as cur:
        cur.execute(
            """
            SELECT slug, name, default_zoom, timezone
            FROM cities WHERE slug = %s
            """,
            (slug,),
        )
        return cur.fetchone()


def test_seed_inserts_all_five_shipped_cities(
    owner_conn: psycopg.Connection[Any], database_url: str
) -> None:
    summary = seed_cities(database_url)
    assert isinstance(summary, SeedSummary)

    with owner_conn.cursor() as cur:
        cur.execute(
            "SELECT slug FROM cities WHERE slug = ANY(%s) ORDER BY slug",
            (list(SHIPPED_CITIES),),
        )
        present = [row[0] for row in cur.fetchall()]
    assert set(present) == set(SHIPPED_CITIES)


def test_seed_is_idempotent(owner_conn: psycopg.Connection[Any], database_url: str) -> None:
    seed_cities(database_url)
    with owner_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM cities")
        row = cur.fetchone()
        assert row is not None
        first_count = row[0]

    seed_cities(database_url)
    with owner_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM cities")
        row = cur.fetchone()
        assert row is not None
        second_count = row[0]

    assert first_count == second_count, (
        f"Re-running the seeder duplicated rows: {first_count} -> {second_count}"
    )


def test_seed_summary_reports_inserts_then_no_inserts(
    database_url: str,
    owner_conn: psycopg.Connection[Any],
) -> None:
    # Ensure a clean run by removing the non-cambridge rows first; cambridge
    # was inserted by the migration so we can't easily remove it without
    # cascading FK trouble. The summary's insert count is asserted relative
    # to the (curated - cambridge) = 4 cities the seeder still has to add.
    with owner_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM cities WHERE slug IN ('phoenix','san-francisco','austin','los-angeles')"
        )
    owner_conn.commit()

    first = seed_cities(database_url)
    assert first.inserted >= 4
    assert first.unchanged + first.updated >= 1  # cambridge already existed

    second = seed_cities(database_url)
    # Idempotent re-run: nothing inserted, everything unchanged.
    assert second.inserted == 0
    assert second.unchanged + second.updated == first.inserted + first.unchanged + first.updated


def test_seed_updates_existing_row_when_yaml_changes(
    tmp_path: Path,
    owner_conn: psycopg.Connection[Any],
    database_url: str,
) -> None:
    # Build a single-city config dir with a custom slug we can safely
    # mutate. Using a `test-` prefix so the autouse cleanup fixture
    # removes it post-test.
    schema_src = Path(__file__).resolve().parents[2] / "config" / "cities" / "__schema__.yaml"
    (tmp_path / "__schema__.yaml").write_text(
        schema_src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    yaml_path = tmp_path / "test-update-city.yaml"
    body: dict[str, object] = {
        "slug": "test-update-city",
        "name": "test-update-city",
        "display_name": "Test Update City — v1",
        "bbox": [-1.0, -1.0, 1.0, 1.0],
        "default_zoom": 10,
        "timezone": "UTC",
        "geofabrik_extract_url": "https://example.invalid/null.osm.pbf",
        "local_cache_path": "data/osm/null.osm.pbf",
    }
    yaml_path.write_text(yaml.safe_dump(body), encoding="utf-8")

    seed_cities(database_url, config_dir=tmp_path)
    row = _row(owner_conn, "test-update-city")
    assert row is not None
    assert row[2] == 10  # default_zoom
    assert row[3] == "UTC"

    # Mutate and re-run.
    body["default_zoom"] = 14
    body["timezone"] = "America/New_York"
    yaml_path.write_text(yaml.safe_dump(body), encoding="utf-8")
    summary = seed_cities(database_url, config_dir=tmp_path)

    row = _row(owner_conn, "test-update-city")
    assert row is not None
    assert row[2] == 14
    assert row[3] == "America/New_York"
    assert summary.updated >= 1


def test_seed_handles_unknown_city_in_custom_dir(
    tmp_path: Path,
    owner_conn: psycopg.Connection[Any],
    database_url: str,
) -> None:
    """AC-4 path: dropping a YAML into the config dir is sufficient to
    register the city. No code change.
    """
    schema_src = Path(__file__).resolve().parents[2] / "config" / "cities" / "__schema__.yaml"
    (tmp_path / "__schema__.yaml").write_text(
        schema_src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    body = {
        "slug": "test-novel-city",
        "name": "test-novel-city",
        "display_name": "Novel City",
        "bbox": [-2.0, -2.0, 2.0, 2.0],
        "default_zoom": 9,
        "timezone": "Europe/London",
        "geofabrik_extract_url": "https://example.invalid/null.osm.pbf",
        "local_cache_path": "data/osm/null.osm.pbf",
    }
    (tmp_path / "test-novel-city.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")

    seed_cities(database_url, config_dir=tmp_path)
    row = _row(owner_conn, "test-novel-city")
    assert row is not None
    assert row[3] == "Europe/London"


def test_seeded_bbox_is_polygon_4326(
    owner_conn: psycopg.Connection[Any], database_url: str
) -> None:
    seed_cities(database_url)
    with owner_conn.cursor() as cur:
        cur.execute(
            """
            SELECT ST_GeometryType(bbox), ST_SRID(bbox), ST_IsValid(bbox)
            FROM cities WHERE slug = 'phoenix'
            """,
        )
        row = cur.fetchone()
    assert row is not None
    geom_type, srid, is_valid = row
    assert geom_type == "ST_Polygon"
    assert srid == 4326
    assert is_valid is True


def test_seed_ignores_schema_file(
    tmp_path: Path,
    owner_conn: psycopg.Connection[Any],
    database_url: str,
) -> None:
    """``__schema__.yaml`` lives alongside the city YAMLs but is not a city.

    The seeder must skip it cleanly rather than trying to validate the
    schema file against itself.
    """
    schema_src = Path(__file__).resolve().parents[2] / "config" / "cities" / "__schema__.yaml"
    (tmp_path / "__schema__.yaml").write_text(
        schema_src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    # Empty config dir aside from the schema. Should run cleanly.
    summary = seed_cities(database_url, config_dir=tmp_path)
    assert summary.inserted == 0
    assert summary.updated == 0
    assert summary.unchanged == 0
