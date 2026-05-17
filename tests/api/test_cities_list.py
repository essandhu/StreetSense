"""``GET /api/cities`` — Phase 4b Task 3.2 (TDD red phase).

The cities-list endpoint is the discovery path for the frontend's city
selector (Phase 4 Task 4.4 — top-bar dropdown). It returns the full
set of configured cities so the UI can populate the selector without
hardcoding slugs. Every other route on the API is mounted under
``/api/cities/{slug}/...``; this is the *one* unscoped endpoint
because by definition it precedes a city choice.

Response shape (decided up-front, user-confirmed):

- **Wrapped envelope** ``{"cities": [...]}`` — matches the existing
  list-endpoint convention (RunListResponse, FreshnessReport). Future
  fields like a summary count or server-time can land without a
  breaking change.

- **ETag-only caching** — the server emits an ``ETag`` header on every
  200; clients can revalidate with ``If-None-Match`` and the server
  returns 304 (empty body) when the cities table hasn't changed. The
  ETag is a stable hash of the response body, so identical content
  yields the same ETag across requests. The cities list changes ~never
  in steady state, so revalidation almost always 304s.

Per-city payload fields (per spec.md "API" + the
:class:`api.schemas.City` model):

- ``slug`` — lowercase identifier, the URL path segment everywhere else
- ``name`` — display name
- ``bbox`` — ``[min_lon, min_lat, max_lon, max_lat]`` in WGS84
- ``default_zoom`` — initial MapLibre zoom
- ``timezone`` — IANA name (e.g., ``America/Phoenix``)

The ``id`` field is intentionally *not asserted on* — it's an
implementation detail (the DB UUID), and the public identity of a
city is its slug. Implementations may include or omit it.

Integration tests — requires a running, migrated Postgres with the
``cities`` table seeded by ``make seed-cities``.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from ingestion.seed_cities import seed_cities

pytestmark = pytest.mark.integration


# The five cities ADR 0010 ships: cambridge (grandfathered) + four
# curated. Seeded by migration 0017 (cambridge) and
# ``make seed-cities`` (the other four). Treated as the canonical set
# by these tests so a regression that drops one is loud.
_EXPECTED_SLUGS: frozenset[str] = frozenset(
    {"cambridge", "phoenix", "san-francisco", "austin", "los-angeles"}
)


# --- Fixtures -------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _seeded_cities(database_url: str) -> None:
    """Ensure every YAML-configured city is in the ``cities`` table.

    Idempotent; safe to invoke as a module-autouse fixture. Migration
    0017 only seeds cambridge, so this is required for the list
    endpoint to return the curated four.
    """
    seed_cities(database_url)


# --- Helpers --------------------------------------------------------------


_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {"slug", "name", "bbox", "default_zoom", "timezone"}
)


def _assert_city_shape(entry: dict[str, object]) -> None:
    """Assert one city entry carries every required field with the right type."""
    missing = _REQUIRED_FIELDS - entry.keys()
    assert not missing, f"city entry missing fields: {sorted(missing)}; got {entry!r}"

    slug = entry["slug"]
    assert isinstance(slug, str) and slug, f"slug must be non-empty str; got {slug!r}"

    name = entry["name"]
    assert isinstance(name, str) and name, f"name must be non-empty str; got {name!r}"

    bbox = entry["bbox"]
    assert isinstance(bbox, list) and len(bbox) == 4, (
        f"bbox must be a 4-element list; got {bbox!r}"
    )
    for component in bbox:
        assert isinstance(component, (int, float)), (
            f"bbox components must be numeric; got {bbox!r}"
        )

    zoom = entry["default_zoom"]
    assert isinstance(zoom, int) and 1 <= zoom <= 22, (
        f"default_zoom must be int in [1,22]; got {zoom!r}"
    )

    tz = entry["timezone"]
    assert isinstance(tz, str) and "/" in tz, (
        f"timezone must be IANA name (contains '/'); got {tz!r}"
    )


# --- Tests ----------------------------------------------------------------


class TestCitiesListShape:
    """Response envelope, per-entry field shape, and the expected set."""

    @pytest.mark.asyncio
    async def test_returns_200_with_wrapped_cities_list(
        self, api_client: AsyncClient
    ) -> None:
        """The endpoint exists, returns 200, and wraps the array under ``cities``."""
        resp = await api_client.get("/api/cities")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert isinstance(body, dict), (
            f"response must be a wrapped object, not a bare array; got {type(body).__name__}"
        )
        assert "cities" in body, f"response missing 'cities' key; got {sorted(body.keys())}"
        assert isinstance(body["cities"], list)

    @pytest.mark.asyncio
    async def test_returns_at_least_the_five_seeded_cities(
        self, api_client: AsyncClient
    ) -> None:
        """ADR 0010 ships five slugs; the list must include them all.

        Asserts a *subset* relationship (not equality) so future cities
        added post-launch don't trip this test.
        """
        body = (await api_client.get("/api/cities")).json()
        slugs = {entry["slug"] for entry in body["cities"]}
        missing = _EXPECTED_SLUGS - slugs
        assert not missing, f"cities-list missing seeded slugs: {sorted(missing)}"

    @pytest.mark.asyncio
    async def test_each_entry_has_the_required_field_shape(
        self, api_client: AsyncClient
    ) -> None:
        """Every entry carries slug / name / bbox / default_zoom / timezone with valid types."""
        body = (await api_client.get("/api/cities")).json()
        for entry in body["cities"]:
            _assert_city_shape(entry)

    @pytest.mark.asyncio
    async def test_bbox_uses_lon_lat_order_and_min_less_than_max(
        self, api_client: AsyncClient
    ) -> None:
        """bbox shape: ``[min_lon, min_lat, max_lon, max_lat]`` per the City model.

        MapLibre's ``fitBounds`` expects lon-lat order; the wire format
        must match so the frontend can pass it through unchanged.
        """
        body = (await api_client.get("/api/cities")).json()
        for entry in body["cities"]:
            min_lon, min_lat, max_lon, max_lat = entry["bbox"]
            assert -180.0 <= min_lon < max_lon <= 180.0, (
                f"{entry['slug']}: invalid lon range in bbox {entry['bbox']!r}"
            )
            assert -90.0 <= min_lat < max_lat <= 90.0, (
                f"{entry['slug']}: invalid lat range in bbox {entry['bbox']!r}"
            )

    @pytest.mark.asyncio
    async def test_response_is_ordered_deterministically_by_slug(
        self, api_client: AsyncClient
    ) -> None:
        """Stable ordering keeps the response body byte-identical across calls.

        A stable body is a precondition for ETag-driven 304s — if the
        order varied, the body hash would too, and revalidation would
        never hit.
        """
        body = (await api_client.get("/api/cities")).json()
        slugs = [entry["slug"] for entry in body["cities"]]
        assert slugs == sorted(slugs), (
            f"cities not sorted by slug; got {slugs!r}"
        )


class TestCitiesListETagCaching:
    """ETag-driven conditional revalidation.

    User-confirmed strategy: ETag only (no Cache-Control max-age).
    Clients always send a request; the server returns 304 when the
    body hasn't changed. Bandwidth-efficient; the cities list is small
    but the request volume from the frontend's mount-time discovery
    is high, so saving the body on the steady-state path matters.
    """

    @pytest.mark.asyncio
    async def test_response_carries_an_etag_header(
        self, api_client: AsyncClient
    ) -> None:
        resp = await api_client.get("/api/cities")
        assert resp.status_code == 200
        etag = resp.headers.get("etag")
        assert etag is not None and etag.strip() != "", (
            f"missing or empty ETag header; got {resp.headers!r}"
        )

    @pytest.mark.asyncio
    async def test_etag_is_stable_across_calls(
        self, api_client: AsyncClient
    ) -> None:
        """Two GETs with no intervening write yield the same ETag.

        The ETag is a body hash, and the body is deterministic (the
        ordering test pins this), so the hash must match. If this
        fails the ETag is being computed from something time-varying
        (e.g., wall-clock, request id) and 304s will never fire.
        """
        first = (await api_client.get("/api/cities")).headers["etag"]
        second = (await api_client.get("/api/cities")).headers["etag"]
        assert first == second, (
            f"ETag is not stable across calls: {first!r} != {second!r}"
        )

    @pytest.mark.asyncio
    async def test_conditional_get_with_matching_etag_returns_304(
        self, api_client: AsyncClient
    ) -> None:
        """``If-None-Match: <current_etag>`` → 304 Not Modified, empty body."""
        first = await api_client.get("/api/cities")
        etag = first.headers["etag"]
        revalidation = await api_client.get(
            "/api/cities", headers={"If-None-Match": etag}
        )
        assert revalidation.status_code == 304, (
            f"expected 304 on revalidation; got {revalidation.status_code} "
            f"with body {revalidation.text!r}"
        )
        # RFC 7232: 304 responses have no body. ``response.content`` is
        # the raw bytes — empty when the body is absent.
        assert revalidation.content == b"", (
            f"304 must have an empty body; got {revalidation.content!r}"
        )

    @pytest.mark.asyncio
    async def test_conditional_get_with_stale_etag_returns_full_body(
        self, api_client: AsyncClient
    ) -> None:
        """A mismatched ``If-None-Match`` returns the full 200 response."""
        revalidation = await api_client.get(
            "/api/cities", headers={"If-None-Match": '"stale-or-bogus-etag"'}
        )
        assert revalidation.status_code == 200
        body = revalidation.json()
        assert "cities" in body
        assert len(body["cities"]) >= len(_EXPECTED_SLUGS)
