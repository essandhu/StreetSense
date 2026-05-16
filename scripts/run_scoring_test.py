"""Tests for the cron scoring wrapper (Task 4.1).

Subprocess and lock-file behavior mocked / isolated to a tmp_path
so the tests don't actually invoke the scoring CLI and don't
collide with whatever lock state might exist on the dev machine.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest

from scripts.run_scoring import _EX_TEMPFAIL, run_scoring


@pytest.fixture
def isolated_lock(tmp_path: Path) -> Iterator[Path]:
    """Point the wrapper at a tmp lock path so tests don't fight each other."""
    lock_path = tmp_path / "test_scoring.lock"
    prior = os.environ.get("STREETSENSE_SCORING_LOCK")
    os.environ["STREETSENSE_SCORING_LOCK"] = str(lock_path)
    try:
        yield lock_path
    finally:
        if prior is not None:
            os.environ["STREETSENSE_SCORING_LOCK"] = prior
        else:
            os.environ.pop("STREETSENSE_SCORING_LOCK", None)
        if lock_path.exists():
            lock_path.unlink()


def _mock_subprocess(returncode: int) -> mock.Mock:
    return mock.Mock(returncode=returncode)


@pytest.mark.usefixtures("isolated_lock")
def test_run_scoring_returns_zero_when_scorer_succeeds() -> None:
    with mock.patch(
        "scripts.run_scoring.subprocess.run",
        return_value=_mock_subprocess(0),
    ):
        rc = run_scoring("cambridge")
    assert rc == 0


@pytest.mark.usefixtures("isolated_lock")
def test_run_scoring_propagates_nonzero_exit_code() -> None:
    with mock.patch(
        "scripts.run_scoring.subprocess.run",
        return_value=_mock_subprocess(7),
    ):
        rc = run_scoring("cambridge")
    assert rc == 7


def test_run_scoring_releases_lock_on_success(isolated_lock: Path) -> None:
    with mock.patch(
        "scripts.run_scoring.subprocess.run",
        return_value=_mock_subprocess(0),
    ):
        run_scoring("cambridge")
    assert not isolated_lock.exists(), "lock should be cleaned up after a successful run"


def test_run_scoring_releases_lock_on_failure(isolated_lock: Path) -> None:
    with mock.patch(
        "scripts.run_scoring.subprocess.run",
        return_value=_mock_subprocess(1),
    ):
        run_scoring("cambridge")
    assert not isolated_lock.exists(), "lock should be cleaned up after a failed run too"


def test_run_scoring_releases_lock_even_when_subprocess_raises(
    isolated_lock: Path,
) -> None:
    with (
        mock.patch(
            "scripts.run_scoring.subprocess.run",
            side_effect=RuntimeError("boom"),
        ),
        pytest.raises(RuntimeError),
    ):
        run_scoring("cambridge")
    assert not isolated_lock.exists(), "lock must be released even on uncaught exception"


def test_run_scoring_returns_tempfail_when_lock_held_by_live_process(
    isolated_lock: Path,
) -> None:
    """A second invocation while the first is in flight returns
    EX_TEMPFAIL (75) so cron / systemd can distinguish 'try again
    later' from 'scoring is broken'."""
    # Seed the lock with the current PID (always alive).
    isolated_lock.write_text(str(os.getpid()), encoding="utf-8")
    with mock.patch("scripts.run_scoring.subprocess.run") as run:
        rc = run_scoring("cambridge")
    assert rc == _EX_TEMPFAIL
    run.assert_not_called(), "scorer must not run when the lock is held"
    # The held lock should NOT be reclaimed — it's a live process.
    assert isolated_lock.exists()


def test_run_scoring_reclaims_stale_lock_from_dead_pid(isolated_lock: Path) -> None:
    """A lock whose recorded PID is dead is treated as crashed-and-left-over —
    the wrapper reclaims it and proceeds."""
    # PID 1 always exists on POSIX (init). PID 999999 almost never
    # does. Skip this test on Windows where the liveness check
    # conservatively returns True.
    import sys

    if sys.platform == "win32":
        pytest.skip("liveness check is conservative on Windows")
    isolated_lock.write_text("999999", encoding="utf-8")
    with mock.patch(
        "scripts.run_scoring.subprocess.run",
        return_value=_mock_subprocess(0),
    ):
        rc = run_scoring("cambridge")
    assert rc == 0


@pytest.mark.usefixtures("isolated_lock")
def test_run_scoring_invokes_scoring_cli_with_city_argument() -> None:
    with mock.patch(
        "scripts.run_scoring.subprocess.run",
        return_value=_mock_subprocess(0),
    ) as run:
        run_scoring("boston")
    cmd = run.call_args.args[0]
    # The wrapper invokes `python -m scoring.cli run --city boston`.
    assert "scoring.cli" in cmd
    assert "run" in cmd
    assert "--city" in cmd
    assert "boston" in cmd
