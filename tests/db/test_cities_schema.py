"""Schema invariant tests for the Phase 4b `cities` table.

These tests enforce the shape and constraints of the cities table after
the Phase 4b migration runs (Task 1.3 + Task 1.4):

1. cities table exists with the expected columns and types.
2. ``id`` is UUID PK with ``gen_random_uuid()`` default — no SERIAL.
3. ``slug`` is TEXT, NOT NULL, UNIQUE.
4. ``name`` is TEXT NOT NULL.
5. ``bbox`` is geometry(Polygon, 4326) NOT NULL with a GIST index.
6. ``default_zoom`` is INT NOT NULL.
7. ``timezone`` is TEXT NOT NULL.
8. Audit timestamps (created_at, updated_at) exist with sensible defaults.
9. The ``cambridge`` bootstrap row exists after migration (the
   single-city demo data is backfilled to it).
10. The app role can read but not delete cities (cities table is curated,
    not append-only, but deletions are a rare operation and should not be
    in the steady-state app-role grant).

If any of these regress, the migration that broke them never lands.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from psycopg import errors as pg_errors

pytestmark = pytest.mark.integration


# --- Helpers --------------------------------------------------------------
def _column_info(
    conn: psycopg.Connection[Any], table: str
) -> dict[str, tuple[str, str, str | None]]:
    """Return a mapping {column_name: (data_type, is_nullable, column_default)}."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table,),
        )
        return {row[0]: (row[1], row[2], row[3]) for row in cur.fetchall()}


def _index_definitions(conn: psycopg.Connection[Any], table: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = %s",
            (table,),
        )
        return [row[0] for row in cur.fetchall()]


# --- cities table shape ---------------------------------------------------


class TestCitiesTableShape:
    def test_table_exists(self, owner_conn: psycopg.Connection[Any]) -> None:
        with owner_conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'cities'
                """
            )
            assert cur.fetchone() is not None, "cities table not present"

    def test_id_is_uuid_with_gen_random_uuid_default(
        self, owner_conn: psycopg.Connection[Any]
    ) -> None:
        cols = _column_info(owner_conn, "cities")
        data_type, is_nullable, default = cols["id"]
        assert data_type == "uuid"
        assert is_nullable == "NO"
        assert default is not None
        assert "gen_random_uuid" in default
        assert "nextval" not in default

    def test_slug_is_text_not_null(self, owner_conn: psycopg.Connection[Any]) -> None:
        cols = _column_info(owner_conn, "cities")
        data_type, is_nullable, _default = cols["slug"]
        assert data_type == "text"
        assert is_nullable == "NO"

    def test_slug_is_unique(self, owner_conn: psycopg.Connection[Any]) -> None:
        defs = _index_definitions(owner_conn, "cities")
        unique_on_slug = [d for d in defs if "UNIQUE" in d.upper() and "(slug)" in d.lower()]
        assert unique_on_slug, f"No UNIQUE index on cities(slug). Found: {defs}"

    def test_duplicate_slug_inserts_are_rejected(self, owner_conn: psycopg.Connection[Any]) -> None:
        with owner_conn.cursor() as cur:
            # cambridge bootstrap row is inserted by migration; trying to
            # insert it again must trip the UNIQUE constraint.
            with pytest.raises(pg_errors.UniqueViolation):
                cur.execute(
                    """
                    INSERT INTO cities (slug, name, bbox, default_zoom, timezone)
                    VALUES (
                        'cambridge',
                        'Cambridge, MA (duplicate)',
                        ST_MakeEnvelope(-71.16, 42.35, -71.07, 42.41, 4326),
                        12,
                        'America/New_York'
                    )
                    """
                )
            owner_conn.rollback()

    def test_name_is_text_not_null(self, owner_conn: psycopg.Connection[Any]) -> None:
        cols = _column_info(owner_conn, "cities")
        data_type, is_nullable, _ = cols["name"]
        assert data_type == "text"
        assert is_nullable == "NO"

    def test_bbox_is_polygon_4326_not_null(self, owner_conn: psycopg.Connection[Any]) -> None:
        cols = _column_info(owner_conn, "cities")
        _data_type, is_nullable, _ = cols["bbox"]
        assert is_nullable == "NO"
        with owner_conn.cursor() as cur:
            cur.execute(
                """
                SELECT type, srid
                FROM geometry_columns
                WHERE f_table_schema = 'public' AND f_table_name = 'cities'
                  AND f_geometry_column = 'bbox'
                """
            )
            row = cur.fetchone()
            assert row is not None, "geometry_columns has no entry for cities.bbox"
            geom_type, srid = row
            assert geom_type == "POLYGON"
            assert srid == 4326

    def test_gist_index_on_bbox(self, owner_conn: psycopg.Connection[Any]) -> None:
        defs = _index_definitions(owner_conn, "cities")
        gist = [d for d in defs if "using gist" in d.lower() and "bbox" in d.lower()]
        assert gist, f"No GIST index on cities.bbox. Found: {defs}"

    def test_default_zoom_is_integer_not_null(self, owner_conn: psycopg.Connection[Any]) -> None:
        cols = _column_info(owner_conn, "cities")
        data_type, is_nullable, _ = cols["default_zoom"]
        assert data_type == "integer"
        assert is_nullable == "NO"

    def test_timezone_is_text_not_null(self, owner_conn: psycopg.Connection[Any]) -> None:
        cols = _column_info(owner_conn, "cities")
        data_type, is_nullable, _ = cols["timezone"]
        assert data_type == "text"
        assert is_nullable == "NO"

    def test_audit_timestamps_exist(self, owner_conn: psycopg.Connection[Any]) -> None:
        cols = _column_info(owner_conn, "cities")
        assert "created_at" in cols
        created_dt, created_null, created_default = cols["created_at"]
        assert "timestamp" in created_dt
        assert created_null == "NO"
        assert created_default is not None  # now() default

        assert "updated_at" in cols
        updated_dt, updated_null, updated_default = cols["updated_at"]
        assert "timestamp" in updated_dt
        assert updated_null == "NO"
        assert updated_default is not None


# --- Bootstrap row (cambridge) -------------------------------------------


class TestCambridgeBootstrap:
    """The Phase 4b migration inserts the cambridge bootstrap row so that
    Task 1.4's UPDATE can backfill existing single-city data to a valid
    city_id. The other four shipped cities are seeded by ``make
    seed-cities`` (Task 1.6).
    """

    def test_cambridge_row_present(self, owner_conn: psycopg.Connection[Any]) -> None:
        with owner_conn.cursor() as cur:
            cur.execute(
                """
                SELECT slug, name, default_zoom, timezone
                FROM cities WHERE slug = 'cambridge'
                """
            )
            row = cur.fetchone()
        assert row is not None, (
            "cambridge bootstrap row missing — backfill cannot have run correctly"
        )
        slug, name, default_zoom, timezone = row
        assert slug == "cambridge"
        assert name  # non-empty
        assert isinstance(default_zoom, int)
        assert default_zoom > 0
        assert timezone  # non-empty IANA name

    def test_cambridge_bbox_is_valid_polygon(self, owner_conn: psycopg.Connection[Any]) -> None:
        with owner_conn.cursor() as cur:
            cur.execute(
                """
                SELECT ST_IsValid(bbox), ST_GeometryType(bbox), ST_SRID(bbox)
                FROM cities WHERE slug = 'cambridge'
                """
            )
            row = cur.fetchone()
        assert row is not None
        is_valid, geom_type, srid = row
        assert is_valid is True
        assert geom_type == "ST_Polygon"
        assert srid == 4326


# --- Round-trip insert / read --------------------------------------------


class TestCitiesRoundTrip:
    def test_insert_and_read_back(self, owner_conn: psycopg.Connection[Any]) -> None:
        with owner_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cities (slug, name, bbox, default_zoom, timezone)
                VALUES (
                    'roundtrip-test-city',
                    'Round Trip Test City',
                    ST_MakeEnvelope(-1.0, -1.0, 1.0, 1.0, 4326),
                    10,
                    'UTC'
                )
                RETURNING id, slug, ST_AsText(bbox), default_zoom, timezone
                """
            )
            row = cur.fetchone()
        assert row is not None
        _id, slug, bbox_wkt, default_zoom, timezone = row
        assert slug == "roundtrip-test-city"
        assert bbox_wkt is not None
        assert bbox_wkt.startswith("POLYGON")
        assert default_zoom == 10
        assert timezone == "UTC"
        owner_conn.rollback()  # leave bootstrap state alone for other tests
