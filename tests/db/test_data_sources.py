"""data_sources regression test (Phase 1.2.5)."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from psycopg import errors as pg_errors

pytestmark = pytest.mark.integration


def test_data_sources_table_exists(owner_conn: psycopg.Connection[Any]) -> None:
    with owner_conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'data_sources'
            ORDER BY ordinal_position
            """
        )
        cols = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
    assert "id" in cols
    assert cols["id"][0] == "uuid"
    assert "name" in cols
    assert cols["name"][1] == "NO"
    assert "last_ingested_at" in cols
    assert "metadata" in cols
    assert cols["metadata"][0] == "jsonb"


def test_data_sources_name_is_unique(owner_conn: psycopg.Connection[Any]) -> None:
    with owner_conn.cursor() as cur:
        cur.execute(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public' AND tablename = 'data_sources'
            """
        )
        defs = {row[0]: row[1] for row in cur.fetchall()}
    unique_on_name = [d for d in defs.values() if "UNIQUE" in d.upper() and "(name)" in d.lower()]
    assert unique_on_name, f"No unique index on data_sources(name). Found: {defs}"


def test_duplicate_name_inserts_are_rejected(owner_conn: psycopg.Connection[Any]) -> None:
    with owner_conn.cursor() as cur:
        cur.execute("INSERT INTO data_sources (name) VALUES ('osm') ON CONFLICT DO NOTHING")
        owner_conn.commit()
        with pytest.raises(pg_errors.UniqueViolation):
            cur.execute("INSERT INTO data_sources (name) VALUES ('osm')")
        owner_conn.rollback()
