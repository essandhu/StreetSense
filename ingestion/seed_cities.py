"""Seed the ``cities`` table from ``config/cities/*.yaml`` (Phase 4b Task 1.6).

Reads every YAML city config in the directory (excluding
``__schema__.yaml``), validates each via :func:`ingestion.config.load_city`,
converts to the :class:`api.schemas.City` Pydantic model, and UPSERTs
into the ``cities`` table by slug.

Idempotent by construction:

- INSERT when the slug is new.
- UPDATE when the slug exists but the persisted fields differ from the
  YAML (bbox, name, default_zoom, timezone). ``updated_at`` is bumped
  to ``now()``.
- Skip ("unchanged") when the row already matches the YAML.

The bbox stored in the DB is a ``geometry(Polygon, 4326)`` produced by
``ST_MakeEnvelope`` from the YAML's 4-tuple. The seeder compares
in-memory tuples (not WKB) when deciding update vs. unchanged so the
comparison is cheap and robust to PostGIS canonicalization.

Per CLAUDE.md, no print in shipped code — ``structlog`` events only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import structlog

from ingestion.config import DEFAULT_CONFIG_DIR, load_city

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SeedSummary:
    """Outcome of a single seed run.

    The seeder is idempotent: re-running with no YAML changes yields
    ``inserted == 0`` and ``updated == 0``; every row falls into
    ``unchanged``. Callers can use these counts in CI / cron logs to
    spot unexpected drift.
    """

    inserted: int
    updated: int
    unchanged: int

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.unchanged


def _psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _list_city_yaml_files(config_dir: Path) -> list[Path]:
    """Return city YAML files in ``config_dir`` excluding the schema file."""
    if not config_dir.is_dir():
        raise FileNotFoundError(f"City config directory not found: {config_dir}")
    return sorted(
        path
        for path in config_dir.glob("*.yaml")
        if path.name != "__schema__.yaml" and path.is_file()
    )


def _existing_row(
    conn: psycopg.Connection[Any],
    slug: str,
) -> tuple[str, tuple[float, float, float, float], int, str] | None:
    """Return ``(name, bbox_tuple, default_zoom, timezone)`` or None."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT name,
                   ST_XMin(bbox), ST_YMin(bbox),
                   ST_XMax(bbox), ST_YMax(bbox),
                   default_zoom, timezone
            FROM cities WHERE slug = %s
            """,
            (slug,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    name, min_lon, min_lat, max_lon, max_lat, default_zoom, timezone = row
    return (
        name,
        (float(min_lon), float(min_lat), float(max_lon), float(max_lat)),
        int(default_zoom),
        str(timezone),
    )


_INSERT_SQL = """
INSERT INTO cities (slug, name, bbox, default_zoom, timezone)
VALUES (
    %(slug)s,
    %(name)s,
    ST_MakeEnvelope(%(min_lon)s, %(min_lat)s, %(max_lon)s, %(max_lat)s, 4326),
    %(default_zoom)s,
    %(timezone)s
)
"""

_UPDATE_SQL = """
UPDATE cities
   SET name         = %(name)s,
       bbox         = ST_MakeEnvelope(%(min_lon)s, %(min_lat)s, %(max_lon)s, %(max_lat)s, 4326),
       default_zoom = %(default_zoom)s,
       timezone     = %(timezone)s,
       updated_at   = now()
 WHERE slug = %(slug)s
"""


def seed_cities(
    database_url: str,
    config_dir: Path = DEFAULT_CONFIG_DIR,
) -> SeedSummary:
    """Seed the ``cities`` table from YAML configs.

    Args:
        database_url: SQLAlchemy- or psycopg-style DSN. SQLAlchemy
            prefix is stripped for raw psycopg use.
        config_dir: Directory holding ``<slug>.yaml`` files +
            ``__schema__.yaml``. Defaults to
            ``ingestion/config.DEFAULT_CONFIG_DIR``.

    Returns:
        :class:`SeedSummary` with the count of new / updated / unchanged
        rows.
    """
    dsn = _psycopg_dsn(database_url)
    yaml_paths = _list_city_yaml_files(config_dir)

    inserted = 0
    updated = 0
    unchanged = 0

    with psycopg.connect(dsn) as conn:
        for path in yaml_paths:
            slug_from_filename = path.stem
            cfg = load_city(slug_from_filename, config_dir=config_dir)
            city = cfg.to_city()

            params = {
                "slug": city.slug,
                "name": city.name,
                "min_lon": city.bbox[0],
                "min_lat": city.bbox[1],
                "max_lon": city.bbox[2],
                "max_lat": city.bbox[3],
                "default_zoom": city.default_zoom,
                "timezone": city.timezone,
            }

            existing = _existing_row(conn, city.slug)
            if existing is None:
                with conn.cursor() as cur:
                    cur.execute(_INSERT_SQL, params)
                inserted += 1
                log.info("seed_cities.insert", slug=city.slug)
            else:
                ex_name, ex_bbox, ex_zoom, ex_tz = existing
                fields_match = (
                    ex_name == city.name
                    and ex_bbox == city.bbox
                    and ex_zoom == city.default_zoom
                    and ex_tz == city.timezone
                )
                if fields_match:
                    unchanged += 1
                    log.debug("seed_cities.unchanged", slug=city.slug)
                else:
                    with conn.cursor() as cur:
                        cur.execute(_UPDATE_SQL, params)
                    updated += 1
                    log.info("seed_cities.update", slug=city.slug)

        conn.commit()

    summary = SeedSummary(inserted=inserted, updated=updated, unchanged=unchanged)
    log.info(
        "seed_cities.done",
        inserted=summary.inserted,
        updated=summary.updated,
        unchanged=summary.unchanged,
        total=summary.total,
    )
    return summary


__all__ = ["SeedSummary", "seed_cities"]
