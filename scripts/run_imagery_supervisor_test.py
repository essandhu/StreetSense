"""Unit tests for the imagery-ingest supervisor.

The supervisor's value is in the retry + checkpoint loop, not in
calling Mapillary — so the tests mock out the subprocess seam and
exercise the state machine directly. Real ingestion is covered by
``tests/ingestion/test_phase_4b_writers.py`` against a live DB.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.run_imagery_supervisor import (
    Checkpoint,
    CityState,
    _backoff_seconds,
    run_supervisor,
)


# ---------------------------------------------------------------------------
# Checkpoint round-trip
# ---------------------------------------------------------------------------


def test_checkpoint_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "cp.json"
    cp = Checkpoint.load(path)
    state = cp.get("phoenix")
    state.attempts = 2
    state.status = "done"
    state.last_error = "transient"
    cp.save()

    reloaded = Checkpoint.load(path)
    assert reloaded.get("phoenix").attempts == 2
    assert reloaded.get("phoenix").status == "done"
    # Idempotent: loading a missing slug yields a fresh pending state.
    assert reloaded.get("austin").status == "pending"


def test_checkpoint_load_missing_file_returns_empty(tmp_path: Path) -> None:
    cp = Checkpoint.load(tmp_path / "absent.json")
    assert cp.cities == {}


# ---------------------------------------------------------------------------
# Backoff curve
# ---------------------------------------------------------------------------


def test_backoff_grows_exponentially_then_caps() -> None:
    assert _backoff_seconds(1, base=30, cap=600) == 30.0
    assert _backoff_seconds(2, base=30, cap=600) == 60.0
    assert _backoff_seconds(3, base=30, cap=600) == 120.0
    assert _backoff_seconds(10, base=30, cap=600) == 600.0  # capped


# ---------------------------------------------------------------------------
# Supervisor state machine
# ---------------------------------------------------------------------------


def test_succeeds_first_try(tmp_path: Path) -> None:
    cp_path = tmp_path / "cp.json"
    sleeps: list[float] = []

    with patch("scripts.run_imagery_supervisor._run_imagery", return_value=(0, "ok")):
        rc = run_supervisor(
            cities=["phoenix"],
            max_segments=10,
            retries=3,
            checkpoint_path=cp_path,
            sleep_fn=sleeps.append,
        )

    assert rc == 0
    assert sleeps == []  # no retries → no sleeps
    persisted = json.loads(cp_path.read_text())
    assert persisted["cities"][0]["status"] == "done"
    assert persisted["cities"][0]["attempts"] == 1


def test_retries_then_succeeds(tmp_path: Path) -> None:
    cp_path = tmp_path / "cp.json"
    calls = iter([(1, "5xx"), (1, "timeout"), (0, "ok")])
    sleeps: list[float] = []

    with patch(
        "scripts.run_imagery_supervisor._run_imagery",
        side_effect=lambda slug, max_segments: next(calls),
    ):
        rc = run_supervisor(
            cities=["phoenix"],
            max_segments=10,
            retries=5,
            checkpoint_path=cp_path,
            backoff_base=10,
            backoff_cap=60,
            sleep_fn=sleeps.append,
        )

    assert rc == 0
    # Backoff after attempts 1 and 2; no sleep after the successful third.
    assert sleeps == [10.0, 20.0]
    persisted = json.loads(cp_path.read_text())
    assert persisted["cities"][0]["status"] == "done"
    assert persisted["cities"][0]["attempts"] == 3


def test_exhausts_retries_then_marks_failed(tmp_path: Path) -> None:
    cp_path = tmp_path / "cp.json"

    with patch(
        "scripts.run_imagery_supervisor._run_imagery",
        return_value=(2, "persistent err"),
    ):
        rc = run_supervisor(
            cities=["phoenix"],
            max_segments=None,
            retries=3,
            checkpoint_path=cp_path,
            backoff_base=1,
            backoff_cap=2,
            sleep_fn=lambda _: None,
        )

    assert rc == 1
    persisted = json.loads(cp_path.read_text())
    assert persisted["cities"][0]["status"] == "failed"
    assert persisted["cities"][0]["attempts"] == 3
    assert persisted["cities"][0]["last_error"] == "persistent err"


def test_skips_already_done_cities(tmp_path: Path) -> None:
    cp_path = tmp_path / "cp.json"
    Checkpoint(
        path=cp_path,
        cities={"cambridge": CityState(slug="cambridge", attempts=2, status="done")},
    ).save()

    invoked: list[str] = []

    def _stub(slug: str, *, max_segments: int | None) -> tuple[int, str]:
        invoked.append(slug)
        return 0, "ok"

    with patch("scripts.run_imagery_supervisor._run_imagery", side_effect=_stub):
        rc = run_supervisor(
            cities=["cambridge", "phoenix"],
            max_segments=10,
            retries=3,
            checkpoint_path=cp_path,
            sleep_fn=lambda _: None,
        )

    assert rc == 0
    # Cambridge skipped on the second invocation; phoenix ran once.
    assert invoked == ["phoenix"]


def test_resumes_after_partial_failure(tmp_path: Path) -> None:
    """If the supervisor is killed mid-run, the next invocation continues."""
    cp_path = tmp_path / "cp.json"

    # First pass: cambridge succeeds, phoenix fails three times.
    with patch(
        "scripts.run_imagery_supervisor._run_imagery",
        side_effect=lambda slug, max_segments: (0, "ok")
        if slug == "cambridge"
        else (1, "err"),
    ):
        rc = run_supervisor(
            cities=["cambridge", "phoenix"],
            max_segments=None,
            retries=3,
            checkpoint_path=cp_path,
            backoff_base=1,
            backoff_cap=1,
            sleep_fn=lambda _: None,
        )
    assert rc == 1
    state = Checkpoint.load(cp_path)
    assert state.get("cambridge").status == "done"
    assert state.get("phoenix").status == "failed"

    # Second pass with fresh attempt budget: cambridge is skipped,
    # phoenix is reset to pending=0 by the supervisor and retried.
    # NB: the supervisor as-implemented uses the cumulative attempt
    # counter, so a "failed" city won't retry without --reset; this
    # test documents the actual behavior. To force a retry, operators
    # can clear the slug's entry in the checkpoint or pass --reset.
    pre = Checkpoint.load(cp_path)
    assert pre.get("phoenix").attempts == 3  # already at the cap

    # Simulate the operator resetting just the phoenix entry to retry.
    pre.cities["phoenix"] = CityState(slug="phoenix")
    pre.save()

    with patch(
        "scripts.run_imagery_supervisor._run_imagery",
        return_value=(0, "ok"),
    ):
        rc2 = run_supervisor(
            cities=["cambridge", "phoenix"],
            max_segments=None,
            retries=3,
            checkpoint_path=cp_path,
            sleep_fn=lambda _: None,
        )
    assert rc2 == 0
    final = Checkpoint.load(cp_path)
    assert final.get("cambridge").status == "done"
    assert final.get("phoenix").status == "done"


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_rejects_empty_cities_list(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.run_imagery_supervisor import main

    with pytest.raises(SystemExit):
        main(["--cities", "", "--checkpoint", str(tmp_path / "cp.json")])
