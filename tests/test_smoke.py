"""Smoke tests: prove the toolchain is wired and core packages import.

These are replaced by real tests as each component lands. The point is to
keep `pytest` exit-zero on a fresh checkout before any feature tests exist.
"""

from __future__ import annotations


def test_streetsense_packages_import() -> None:
    """The three packages under mypy --strict must be importable."""
    import api  # noqa: F401
    import ingestion  # noqa: F401
    import scoring  # noqa: F401


def test_core_runtime_deps_import() -> None:
    """Smoke-check the runtime dependencies declared in pyproject.toml."""
    import fastapi  # noqa: F401
    import pydantic  # noqa: F401
    import psycopg  # noqa: F401
    import shapely  # noqa: F401
    import structlog  # noqa: F401
    import xxhash  # noqa: F401
