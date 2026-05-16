"""SQL-shape and contract tests for the segment_scores delta repo.

These tests don't hit a real database — they exercise the public surface
(types, function signatures, the SQL constants the module exposes) and
assert the SQL references the right columns, uses parameter binding
(not string formatting), and orders results stably.

Integration tests against a running Postgres live in
``tests/api/test_segment_scores_repo.py`` and skip without
``DATABASE_URL``.
"""

from __future__ import annotations

import re
from uuid import UUID

from api.repos.segment_scores import (
    FETCH_PAIR_AT_HOUR_SQL,
    PAIR_COUNT_AT_HOUR_SQL,
    RUNS_EXIST_SQL,
    ScorePairRow,
    SegmentScoreRow,
)


def test_segment_score_row_is_frozen_and_typed() -> None:
    """SegmentScoreRow is immutable so a row passed between layers can't
    be mutated by accident."""
    row = SegmentScoreRow(
        segment_id=UUID("00000000-0000-0000-0000-000000000001"),
        composite_risk=0.4,
        propagation_uplift=0.1,
        sub_score_lane_marking=0.3,
        sub_score_glare=0.4,
        sub_score_junction_complexity=0.2,
        sub_score_historical=0.5,
        confidence=0.8,
    )
    # Frozen ⇒ assignment raises.
    import pytest

    with pytest.raises((TypeError, ValueError)):
        row.composite_risk = 0.5


def test_score_pair_row_pairs_two_segment_score_rows() -> None:
    """ScorePairRow exposes ``a`` and ``b`` fields plus the shared
    segment_id at the top level."""
    sid = UUID("00000000-0000-0000-0000-000000000001")
    a = SegmentScoreRow(
        segment_id=sid,
        composite_risk=0.3,
        propagation_uplift=0.0,
        sub_score_lane_marking=0.1,
        sub_score_glare=0.1,
        sub_score_junction_complexity=0.1,
        sub_score_historical=0.1,
        confidence=0.8,
    )
    b = a.model_copy(update={"composite_risk": 0.5})
    pair = ScorePairRow(segment_id=sid, a=a, b=b)
    assert pair.a.composite_risk == 0.3
    assert pair.b.composite_risk == 0.5


# --- SQL shape ------------------------------------------------------------


def test_fetch_pair_sql_references_segment_scores_table_twice() -> None:
    """The query joins segment_scores against itself (aliased a and b)."""
    sql = FETCH_PAIR_AT_HOUR_SQL.lower()
    assert sql.count("segment_scores") >= 2
    # Aliases.
    assert " a " in sql or " a\n" in sql
    assert " b " in sql or " b\n" in sql


def test_fetch_pair_sql_uses_parameter_binding_not_string_formatting() -> None:
    """All variables are bound parameters — no ``%s`` Python interpolation
    creeping in. psycopg's named binding style is ``%(name)s``."""
    sql = FETCH_PAIR_AT_HOUR_SQL
    # Find all psycopg-style named params.
    named = set(re.findall(r"%\((\w+)\)s", sql))
    # The required parameters for the delta query.
    assert {"run_a_id", "run_b_id", "target_hour", "limit", "offset"}.issubset(named)
    # No bare %s positional bindings allowed in this query — they'd
    # bypass named-arg safety.
    assert re.search(r"%s", re.sub(r"%\(\w+\)s", "", sql)) is None


def test_fetch_pair_sql_selects_all_required_columns() -> None:
    """Both runs' composite, propagation_uplift, four sub-scores, and
    confidence columns are projected. If any are missing, the route
    layer can't reconstruct SegmentScoreSnapshot."""
    sql = FETCH_PAIR_AT_HOUR_SQL.lower()
    for col in [
        "composite_risk",
        "propagation_uplift",
        "sub_score_lane_marking",
        "sub_score_glare",
        "sub_score_junction_complexity",
        "sub_score_historical",
        "confidence",
    ]:
        # Each column referenced at least twice (once per run alias).
        assert sql.count(col) >= 2, f"column {col!r} should appear at least twice in the SQL"


def test_fetch_pair_sql_filters_by_both_scoring_run_ids() -> None:
    sql = FETCH_PAIR_AT_HOUR_SQL.lower()
    # Both scoring_run_id filters present.
    assert "scoring_run_id" in sql
    # Both parameter references present (named bindings).
    assert "%(run_a_id)s" in FETCH_PAIR_AT_HOUR_SQL
    assert "%(run_b_id)s" in FETCH_PAIR_AT_HOUR_SQL


def test_fetch_pair_sql_filters_by_hour_of_day() -> None:
    sql = FETCH_PAIR_AT_HOUR_SQL.lower()
    assert "extract" in sql
    assert "hour" in sql
    assert "%(target_hour)s" in FETCH_PAIR_AT_HOUR_SQL


def test_fetch_pair_sql_has_stable_order_for_pagination() -> None:
    """Pagination requires a stable ORDER BY — segment_id is the obvious
    primary sort key since it's the JOIN column."""
    sql = FETCH_PAIR_AT_HOUR_SQL.lower()
    assert "order by" in sql
    # Must order by segment_id (or some deterministic key).
    assert "segment_id" in sql.split("order by", 1)[1].split("limit", 1)[0]


def test_fetch_pair_sql_has_limit_and_offset() -> None:
    assert "%(limit)s" in FETCH_PAIR_AT_HOUR_SQL
    assert "%(offset)s" in FETCH_PAIR_AT_HOUR_SQL


def test_count_pair_sql_references_segment_scores_twice() -> None:
    """The count uses the same JOIN shape as fetch_pair so the count
    matches what would be returned across all pages."""
    sql = PAIR_COUNT_AT_HOUR_SQL.lower()
    assert sql.count("segment_scores") >= 2
    assert "count(" in sql
    assert "%(run_a_id)s" in PAIR_COUNT_AT_HOUR_SQL
    assert "%(run_b_id)s" in PAIR_COUNT_AT_HOUR_SQL
    assert "%(target_hour)s" in PAIR_COUNT_AT_HOUR_SQL


def test_count_pair_sql_has_no_limit_or_offset() -> None:
    """The count query must return the TOTAL across all pages — having a
    LIMIT here would silently truncate the page-count math."""
    sql = PAIR_COUNT_AT_HOUR_SQL.lower()
    assert "limit" not in sql
    assert "offset" not in sql


def test_runs_exist_sql_takes_one_run_id_parameter() -> None:
    """``runs_exist`` is called twice per request (once per run) so the
    SQL is parameterized on a single run_id."""
    assert "%(run_id)s" in RUNS_EXIST_SQL
    assert "select" in RUNS_EXIST_SQL.lower()
    assert "scoring_runs" in RUNS_EXIST_SQL.lower()
