"""Phase 4b Task 2.6 — property assertions against real OSM counts per city.

The plan calls for a property test against real OSM counts as the
sanity check that the per-city Geofabrik clip is yielding the
expected order of magnitude. The fixture-driven writer tests in
``tests/ingestion/test_phase_4b_writers.py`` cover the
city-tagging plumbing; this file covers the *quantitative* claim:
"a city's ingested ``road_segments`` count makes sense for its bbox
area and urban density".

The expected ranges below are derived from the actual Phase 4b
ingestion run (2026-05-18) and recorded as loose bounds — wide
enough to absorb routine Geofabrik extract churn (~ ±5 % week to
week) but tight enough to catch a regression where, say, the bbox
clip silently flips axes or a highway-filter accidentally drops a
class.

Each test skips when the corresponding city has zero
``road_segments`` rows: in CI without ingestion, the test is a
no-op; on a developer machine that has run ``make seed`` for that
city, the assertions fire. This is the substitute the plan's
Task 2.6 entry promised.

No autouse TRUNCATE in ``tests/db/`` — these tests are read-only
and safe to run alongside live data.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

pytestmark = pytest.mark.integration


# Expected lower/upper bounds for road_segments per city, sized for the
# bbox in ``config/cities/<slug>.yaml``. Bounds are deliberately ±50 %
# around the actual count observed on 2026-05-18 so routine Geofabrik
# weekly diffs don't flake the test; a hard regression (orders of
# magnitude wrong) trips it.
EXPECTED_COUNT_BOUNDS: dict[str, tuple[int, int]] = {
    "cambridge": (15_000, 75_000),  # observed: 36,601
    "phoenix": (150_000, 600_000),  # observed: 325,052
    "san-francisco": (25_000, 130_000),  # observed: 64,033
    "austin": (100_000, 450_000),  # observed: 224,422
    "los-angeles": (200_000, 800_000),  # observed: 452,792
}


def _city_segment_count(conn: psycopg.Connection[Any], slug: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM road_segments rs
            JOIN cities c ON rs.city_id = c.id
            WHERE c.slug = %s
            """,
            (slug,),
        )
        row = cur.fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.parametrize("slug", sorted(EXPECTED_COUNT_BOUNDS.keys()))
def test_real_osm_segment_count_within_bounds(
    owner_conn: psycopg.Connection[Any], slug: str
) -> None:
    """A city's ingested ``road_segments`` count must sit in the expected band.

    Skips if the city has not been ingested (zero rows). When the
    city *has* been ingested, the count must be in
    ``EXPECTED_COUNT_BOUNDS[slug]`` — wide bands sized to absorb
    routine Geofabrik churn but catch order-of-magnitude regressions
    (axis-flip, accidental highway-class drop, wrong bbox).
    """
    count = _city_segment_count(owner_conn, slug)
    if count == 0:
        pytest.skip(
            f"{slug} has zero road_segments — run `make seed CITY={slug}` to ingest "
            f"before this test exercises the count property."
        )

    lo, hi = EXPECTED_COUNT_BOUNDS[slug]
    assert lo <= count <= hi, (
        f"{slug} road_segments={count:,} is outside expected bounds [{lo:,}, {hi:,}]. "
        "This usually means the bbox clip is wrong (axis-flip, swapped lon/lat) "
        "or the highway-class filter changed silently."
    )


def test_segment_density_orders_match_bbox_area(
    owner_conn: psycopg.Connection[Any],
) -> None:
    """The cities' segment counts must order roughly by bbox area.

    A loose monotonicity property: LA's bbox is ~10x Cambridge's,
    so LA's road_segments count must exceed Cambridge's; ditto
    Phoenix > SF (Phoenix bbox is ~7x SF's). If a city's segment
    count violates the expected partial order, the bbox or the
    clip is misconfigured.

    Reads only — no fixture state. Skips if the relevant cities
    haven't been ingested.
    """
    counts: dict[str, int] = {
        slug: _city_segment_count(owner_conn, slug)
        for slug in ("cambridge", "san-francisco", "phoenix", "los-angeles")
    }
    if any(c == 0 for c in counts.values()):
        pytest.skip(
            "Density-ordering check needs cambridge + san-francisco + phoenix + "
            "los-angeles all ingested."
        )

    assert counts["los-angeles"] > counts["cambridge"], (
        f"LA ({counts['los-angeles']:,}) should exceed Cambridge "
        f"({counts['cambridge']:,}) — LA bbox is ~10x Cambridge."
    )
    assert counts["phoenix"] > counts["san-francisco"], (
        f"Phoenix ({counts['phoenix']:,}) should exceed San Francisco "
        f"({counts['san-francisco']:,}) — Phoenix bbox is ~7x SF."
    )
    assert counts["los-angeles"] > counts["san-francisco"], (
        f"LA ({counts['los-angeles']:,}) should exceed San Francisco "
        f"({counts['san-francisco']:,}) — LA bbox is ~17x SF."
    )


def test_every_road_segment_is_city_scoped(
    owner_conn: psycopg.Connection[Any],
) -> None:
    """No ``road_segments`` row may exist without a resolvable ``city_id``.

    Reinforces the migration-0017 NOT NULL FK at the data layer:
    once the city_id column is populated, no future ingestion path
    can leak a city-less row in. The check matches the spec's AC-5
    ("Reproducibility preserved … city_id foreign key").
    """
    with owner_conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM road_segments rs
            LEFT JOIN cities c ON rs.city_id = c.id
            WHERE c.id IS NULL
            """
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 0, (
        f"Found {row[0]} road_segments with a city_id that doesn't resolve to "
        "the cities table — either the migration backfill missed rows or a "
        "subsequent insert wrote a stale city_id."
    )
