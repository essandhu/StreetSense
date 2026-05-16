"""Tests for the bootstrap orchestration (Task 1.6).

The bootstrap is a thin sequencer over already-tested CLIs — the
tests here verify the *ordering* and the skip flags, not the inner
steps (the seed / scoring CLIs have their own tests). Subprocess
calls are mocked so the tests run without Postgres / MinIO / OSM
extracts.
"""

from __future__ import annotations

from collections.abc import Callable
from unittest import mock

import pytest

from scripts.bootstrap_deploy import bootstrap


def _ok(*_args: str, **_kwargs: object) -> mock.Mock:
    """Subprocess.run stand-in that always succeeds with rc=0."""
    return mock.Mock(returncode=0)


def _fail_at(
    failing_step_substring: str,
) -> Callable[[list[str]], mock.Mock]:
    """Factory: subprocess.run stand-in that fails on the chosen step."""

    def _runner(cmd: list[str], **_kwargs: object) -> mock.Mock:
        if any(failing_step_substring in part for part in cmd):
            return mock.Mock(returncode=2)
        return mock.Mock(returncode=0)

    return _runner


def test_bootstrap_runs_all_steps_in_order_by_default() -> None:
    with mock.patch("scripts.bootstrap_deploy.subprocess.run", side_effect=_ok) as run:
        rc = bootstrap("cambridge")
    assert rc == 0
    calls = [call.args[0] for call in run.call_args_list]
    # Five steps in canonical order: migrate → seed → imagery → incidents → scoring.
    assert any("alembic" in c[0] for c in calls)
    seed_idx = next(i for i, c in enumerate(calls) if "ingestion.cli" in c and "seed" in c)
    imagery_idx = next(i for i, c in enumerate(calls) if "ingestion.cli" in c and "imagery" in c)
    incidents_idx = next(
        i for i, c in enumerate(calls) if "ingestion.cli" in c and "incidents" in c
    )
    scoring_idx = next(i for i, c in enumerate(calls) if "scoring.cli" in c and "run" in c)
    # Strictly increasing.
    assert seed_idx < imagery_idx < incidents_idx < scoring_idx


def test_bootstrap_skip_imagery_omits_imagery_call() -> None:
    with mock.patch("scripts.bootstrap_deploy.subprocess.run", side_effect=_ok) as run:
        rc = bootstrap("cambridge", skip_imagery=True)
    assert rc == 0
    calls = [call.args[0] for call in run.call_args_list]
    assert not any("ingestion.cli" in c and "imagery" in c for c in calls)


def test_bootstrap_skip_incidents_omits_incidents_call() -> None:
    with mock.patch("scripts.bootstrap_deploy.subprocess.run", side_effect=_ok) as run:
        rc = bootstrap("cambridge", skip_incidents=True)
    assert rc == 0
    calls = [call.args[0] for call in run.call_args_list]
    assert not any("ingestion.cli" in c and "incidents" in c for c in calls)


def test_bootstrap_skip_scoring_omits_scoring_call() -> None:
    with mock.patch("scripts.bootstrap_deploy.subprocess.run", side_effect=_ok) as run:
        rc = bootstrap("cambridge", skip_scoring=True)
    assert rc == 0
    calls = [call.args[0] for call in run.call_args_list]
    assert not any("scoring.cli" in c and "run" in c for c in calls)


def test_bootstrap_halts_on_step_failure() -> None:
    """First failing step aborts — later steps are not attempted."""
    with mock.patch(
        "scripts.bootstrap_deploy.subprocess.run",
        side_effect=_fail_at("imagery"),
    ) as run, pytest.raises(SystemExit) as excinfo:
        bootstrap("cambridge")
    assert excinfo.value.code == 2
    calls = [call.args[0] for call in run.call_args_list]
    # Scoring never ran — bootstrap aborted after imagery failed.
    assert not any("scoring.cli" in c for c in calls)


def test_bootstrap_passes_city_through_to_each_step() -> None:
    with mock.patch("scripts.bootstrap_deploy.subprocess.run", side_effect=_ok) as run:
        bootstrap("boston")
    calls = [call.args[0] for call in run.call_args_list]
    # Every ingestion + scoring call carries --city boston.
    relevant = [c for c in calls if "ingestion.cli" in c or "scoring.cli" in c]
    assert all("boston" in c for c in relevant)
