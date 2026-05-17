"""``GET /api/cities`` — top-level cities-list endpoint (Phase 4b Task 3.4).

The one un-slugged API route in the Phase 4b refactor. Backs the
frontend's city-selector discovery (Phase 4 Task 4.4 — top-bar
dropdown): on app mount the SPA fetches this list to populate the
dropdown without hardcoding slugs. By definition it precedes a city
choice, so the ``/api/cities/{slug}/...`` family doesn't apply.

Response contract (locked by the Task 3.2 tests, user-confirmed):

- **Envelope:** ``{"cities": [...]}`` — wrapped (not bare) so future
  fields land non-breaking.
- **Per-entry shape:** ``slug``, ``name``, ``bbox``, ``default_zoom``,
  ``timezone`` from the :class:`api.schemas.City` model. The DB UUID
  (``id``) is included; clients that key on ``slug`` ignore it.
- **Ordering:** alphabetical by ``slug``. This is the precondition for
  ETag stability — if order varied across requests, the body hash
  would too, and conditional revalidation would never 304.
- **Caching:** ETag-only (no Cache-Control max-age). The ETag is a
  sha256 of the serialized response body, quoted per RFC 7232. A
  matching ``If-None-Match`` returns 304 with an empty body; a
  mismatched (or absent) one returns the full 200.

Why ETag-only: the cities list changes ~never in steady state (a
``make seed-cities`` run is the only mutator), so conditional
revalidation is the dominant access pattern. A Cache-Control max-age
would let stale data linger past a re-seed; ETag round-trips are
fast enough that the small server hit is the right trade.

Why a single Postgres query is sufficient: the table has ~5 rows. A
``SELECT ... FROM cities ORDER BY slug`` with the bbox decomposed via
``ST_XMin`` / ``ST_YMin`` / ``ST_XMax`` / ``ST_YMax`` does the whole
job. No JOINs, no LATERAL, no projection sub-queries.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Request
from starlette.responses import Response

from api.db import conn
from api.schemas import City

router = APIRouter(prefix="/api/cities", tags=["cities"])


# ETag is RFC 7232: a quoted opaque tag. The implementation choice
# here is a strong validator (no ``W/`` prefix) computed as a
# sha256 prefix over the serialized body. The 16-hex-char prefix
# (64 bits) is collision-resistant for a 5-row list with overwhelming
# margin and keeps the header short.
_ETAG_HEX_LEN = 16


_SELECT_CITIES_SQL = """
SELECT
    id,
    slug,
    name,
    ST_XMin(bbox) AS min_lon,
    ST_YMin(bbox) AS min_lat,
    ST_XMax(bbox) AS max_lon,
    ST_YMax(bbox) AS max_lat,
    default_zoom,
    timezone
FROM cities
ORDER BY slug
"""


async def _fetch_cities() -> list[City]:
    """Read every row from ``cities`` and project into the API shape.

    Ordered by slug for stable output (ETag precondition). Bbox is
    decomposed into four floats here rather than fetching the WKB
    geometry — saves a Shapely round-trip and matches the on-wire
    ``[min_lon, min_lat, max_lon, max_lat]`` shape directly.
    """
    async with conn() as c, c.cursor() as cur:
        await cur.execute(_SELECT_CITIES_SQL)
        rows = await cur.fetchall()
    return [
        City(
            id=row[0],
            slug=row[1],
            name=row[2],
            bbox=(float(row[3]), float(row[4]), float(row[5]), float(row[6])),
            default_zoom=int(row[7]),
            timezone=row[8],
        )
        for row in rows
    ]


def _compute_etag(body_bytes: bytes) -> str:
    """Compute the quoted strong-validator ETag for ``body_bytes``.

    sha256 prefix; quoted to satisfy RFC 7232 §2.3. Deterministic in
    the body bytes, which (combined with the slug-ordered query and
    ``sort_keys=True`` serialization) makes the ETag stable across
    requests when the table hasn't changed.
    """
    digest = hashlib.sha256(body_bytes).hexdigest()[:_ETAG_HEX_LEN]
    return f'"{digest}"'


def _serialize_response(cities: list[City]) -> bytes:
    """Serialize the response body deterministically.

    ``sort_keys=True`` + ``separators=(",", ":")`` produces canonical
    JSON: the same input always yields the same byte sequence
    regardless of dict-iteration order, whitespace, or Python version.
    That stability is what makes the ETag stable.
    """
    payload: dict[str, Any] = {
        "cities": [city.model_dump(mode="json") for city in cities],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


@router.get("")
async def list_cities(request: Request) -> Response:
    """List every configured city; ETag-cached.

    Returns 200 with the wrapped envelope on first request and on any
    request whose ``If-None-Match`` doesn't match the current ETag.
    Returns 304 with an empty body when ``If-None-Match`` matches.
    """
    cities = await _fetch_cities()
    body_bytes = _serialize_response(cities)
    etag = _compute_etag(body_bytes)

    if_none_match = request.headers.get("if-none-match")
    if if_none_match is not None and if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})

    return Response(
        content=body_bytes,
        media_type="application/json",
        headers={"ETag": etag},
    )
