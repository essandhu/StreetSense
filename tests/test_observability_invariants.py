"""Cross-cutting observability invariants — Phase 5 Tasks 4.3 + 4.5.

These tests guard the project-wide rules from CLAUDE.md
§"Observability (from day one)" and §"Reproducibility":

1. **No `print` in shipped code.** Structured events go through
   structlog (Python) and spdlog (C++). A bare `print()` in a
   path that runs in prod is a regression — log lines need names,
   levels, and structure so we can query them.

2. **All six reproducibility fields on every persisted score row.**
   ``scoring_run_id``, ``scoring_run_timestamp``,
   ``perception_model_version``, ``osm_snapshot_date``,
   ``imagery_capture_window``, ``propagation_algorithm_version`` —
   enforced at schema level (NOT NULL constraints) in the
   initial migration. This test asserts the constraints are
   still present so a future migration can't quietly relax them.

The runtime check that "every row in a *real* scoring run carries
real values" is the integration test
``tests/api/test_runs_list.py::test_list_runs_carries_full_provenance``
(Task 3.3) — gated on DATABASE_URL. Schema-level enforcement +
runtime check together form the regression net.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories whose runtime code must be print-free. Tests
# (``*_test.py``) are exempt — they may print for fixture
# debugging.
_PROD_PACKAGES = ("api", "scoring", "ingestion", "scripts")

# Explicit allowlist for print() that's load-bearing — entries here
# need a comment justifying why structlog wouldn't work.
_PRINT_ALLOWLIST: frozenset[str] = frozenset(
    {
        # CI gate script — prints human-readable PASS/FAIL + a
        # per-benchmark delta table for CI dashboards.
        "scripts/check_propagator_perf_regression.py",
        # CLI usage helper — prints "usage: ..." to stderr.
        "scripts/run_with_dotenv.py",
    }
)

# Path fragments to exclude entirely (vendored deps + generated
# build artifacts). The propagator's C++ build pulls in third-party
# Python scripts (pybind11 source tree, spdlog/gtest build helpers)
# that we don't own.
_PATH_EXCLUDE_FRAGMENTS = (
    "propagator/build",
    "propagator/external",
)


def _iter_prod_python_files() -> list[Path]:
    files: list[Path] = []
    for pkg in _PROD_PACKAGES:
        root = _REPO_ROOT / pkg
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            name = path.name
            if name.endswith("_test.py") or name.startswith("test_"):
                continue
            if "__pycache__" in path.parts:
                continue
            posix = path.as_posix()
            if any(fragment in posix for fragment in _PATH_EXCLUDE_FRAGMENTS):
                continue
            files.append(path)
    return files


def _has_bare_print(source: str) -> list[int]:
    """Return line numbers of any ``print(...)`` call in the source.

    Uses AST so an in-string ``"print(...)"`` literal (e.g., inside
    a docstring example) doesn't trip the check.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "print":
                hits.append(node.lineno)
    return hits


@pytest.mark.parametrize(
    "path", _iter_prod_python_files(), ids=lambda p: str(p.relative_to(_REPO_ROOT))
)
def test_no_bare_print_calls_in_prod_python(path: Path) -> None:
    """Static check: prod Python paths use structlog, not print().

    Per CLAUDE.md §"Observability": "No `print` / `std::cout` in
    shipped code. Scoring runs log per-stage timing as discrete
    events, not as a single summary log line."
    """
    rel = str(path.relative_to(_REPO_ROOT)).replace("\\", "/")
    if rel in _PRINT_ALLOWLIST:
        pytest.skip(f"{rel} on the explicit allowlist")
    source = path.read_text(encoding="utf-8")
    hits = _has_bare_print(source)
    assert not hits, f"{rel} contains print() at line(s) {hits} — use structlog instead"


# ---------------------------------------------------------------------------
# Reproducibility: schema-level NOT NULL on the six fields.
# ---------------------------------------------------------------------------


_INITIAL_SCHEMA = _REPO_ROOT / "db" / "migrations" / "versions" / "0001_initial_schema.py"

_REQUIRED_NOT_NULL_FIELDS_IN_SCORING_RUNS = (
    "scoring_run_timestamp",
    "perception_model_version",
    "osm_snapshot_date",
    "imagery_capture_window",
    "propagation_algorithm_version",
)

_REQUIRED_NOT_NULL_FIELDS_IN_SEGMENT_SCORES = (
    "scoring_run_id",
    "scoring_run_timestamp",
    "perception_model_version",
    "osm_snapshot_date",
    "imagery_capture_window",
    "propagation_algorithm_version",
)


def test_initial_schema_migration_exists() -> None:
    """The migration file we're asserting against must exist."""
    assert _INITIAL_SCHEMA.is_file(), (
        f"expected {_INITIAL_SCHEMA} — has the initial schema migration been renamed?"
    )


def _extract_create_table_block(source: str, table: str) -> str:
    """Return the substring between ``CREATE TABLE <table>`` and the
    closing ``);`` so a column-name search isn't fooled by
    comments / indexes / other tables that mention the same column.
    """
    marker = f"CREATE TABLE {table}"
    if marker not in source:
        return ""
    after = source.split(marker, 1)[1]
    # Stop at the closing `);` — every CREATE TABLE in this file
    # is a triple-quoted SQL block.
    end = after.find(");")
    return after[:end] if end >= 0 else after


@pytest.mark.parametrize("field", _REQUIRED_NOT_NULL_FIELDS_IN_SCORING_RUNS)
def test_scoring_runs_reproducibility_fields_are_not_null(field: str) -> None:
    """The five provenance fields on ``scoring_runs`` carry NOT NULL.

    A future migration that drops one of these would silently allow
    a scoring run with missing provenance — exactly the regression
    CLAUDE.md §"Reproducibility" forbids.
    """
    source = _INITIAL_SCHEMA.read_text(encoding="utf-8")
    block = _extract_create_table_block(source, "scoring_runs")
    assert block, "scoring_runs table definition not found"
    for line in block.splitlines():
        if field in line:
            assert "NOT NULL" in line, (
                f"{field} is no longer NOT NULL — provenance can leak through"
            )
            return
    pytest.fail(f"could not locate the column definition line for {field}")


@pytest.mark.parametrize("field", _REQUIRED_NOT_NULL_FIELDS_IN_SEGMENT_SCORES)
def test_segment_scores_reproducibility_fields_are_not_null(field: str) -> None:
    """Same six-field invariant, but on ``segment_scores`` — every
    persisted *score row* must carry full provenance, not just the
    parent ``scoring_runs`` row."""
    source = _INITIAL_SCHEMA.read_text(encoding="utf-8")
    block = _extract_create_table_block(source, "segment_scores")
    assert block, "segment_scores table definition not found"
    for line in block.splitlines():
        if field in line:
            assert "NOT NULL" in line, f"{field} on segment_scores is no longer NOT NULL"
            return
    pytest.fail(f"could not locate the column definition for {field} on segment_scores")
