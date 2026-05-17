"""FastAPI request-scoped dependencies (Phase 4b Task 3.3).

The single dependency this module exposes — :func:`resolve_city_id` —
is the gate every city-scoped route passes through. It takes the
``slug`` path parameter, looks up the matching ``cities.id``, and
returns the UUID for downstream queries to filter against. If the
slug doesn't resolve, a :class:`UnknownCitySlug` exception fires; the
top-level handler registered in :mod:`api.main` flattens it into a
404 response whose body lists the valid slugs.

Why a custom exception instead of ``HTTPException(detail=...)``: FastAPI
serializes ``HTTPException.detail`` under a top-level ``detail`` key
(``{"detail": {"valid_slugs": [...]}}``), but the contract the Task 3.1
tests pin requires ``valid_slugs`` at the response root
(``{"valid_slugs": [...]}``). Surfacing the recovery hints at the top
level matches the spec wording — "JSON body listing valid slugs" — and
keeps the frontend's error parsing schema-simple.

DB lookup posture: this dependency runs on every city-scoped request,
which is a lot of small queries against a 5-row table. We intentionally
do NOT cache here:

- The ``cities`` table fits in the PG buffer cache and is hit on a
  unique BTREE index (``slug``). The query cost is dominated by the
  network round-trip, not the scan.
- An in-process cache would need an invalidation path so a fresh
  ``make seed-cities`` shows up without a process restart. The
  complexity isn't worth the saved microseconds.
- If profiling ever shows this as a hotspot, an LRU on the slug→id
  mapping is a one-line follow-up.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Path

from api.db import conn


class UnknownCitySlug(Exception):
    """Raised by :func:`resolve_city_id` when the path slug isn't in ``cities``.

    Carries the list of valid slugs so the response handler can surface
    them. The 404 body shape the Task 3.1 tests require is
    ``{"message": "...", "valid_slugs": ["austin", "cambridge", ...]}``.
    """

    def __init__(self, slug: str, valid_slugs: list[str]) -> None:
        super().__init__(f"unknown city slug {slug!r}; valid slugs: {sorted(valid_slugs)!r}")
        self.slug = slug
        self.valid_slugs = valid_slugs


async def resolve_city_id(
    slug: str = Path(
        ...,
        description=(
            "Lowercase city identifier — the URL path segment used "
            "throughout the API. See ``GET /api/cities`` for the live "
            "list of valid slugs."
        ),
        examples=["cambridge", "phoenix", "san-francisco"],
    ),
) -> UUID:
    """Resolve a URL slug to the matching ``cities.id`` UUID.

    Lookup happens on every request (no cache — see module docstring).
    Hits the primary store via the same async pool the rest of the API
    uses; PostgreSQL serves it from the buffer cache via the unique
    ``cities_slug_key`` index.

    Args:
        slug: The ``{slug}`` path parameter from the route URL.

    Returns:
        The ``cities.id`` UUID matching ``slug``.

    Raises:
        UnknownCitySlug: when ``slug`` does not match any row in the
            ``cities`` table. The exception carries the list of valid
            slugs so the response handler can echo them back to the
            client for recovery.
    """
    async with conn() as c, c.cursor() as cur:
        await cur.execute("SELECT id FROM cities WHERE slug = %s", (slug,))
        row = await cur.fetchone()
        if row is not None:
            return UUID(str(row[0]))
        await cur.execute("SELECT slug FROM cities ORDER BY slug")
        valid_slugs = [str(r[0]) for r in await cur.fetchall()]
    raise UnknownCitySlug(slug, valid_slugs)


__all__ = ["UnknownCitySlug", "resolve_city_id"]
