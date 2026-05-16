"""Cron-callable scoring wrapper — Phase 5 Task 4.1.

Wraps ``python -m scoring.cli run --city <city>`` with:

  * **Structured logging** at every transition (start, lock acquired,
    subprocess complete, lock released).
  * **Lock file** preventing overlapping runs — a Fly Machine spinning
    up its weekly schedule while a manual run is in flight should
    wait/abort, not race the same ``scoring_runs`` insert.
  * **Stale-lock detection** — a lock whose PID is no longer running
    (process died mid-run) is reclaimed.
  * **Exit-code propagation** — the wrapper's exit code is the
    underlying scoring CLI's exit code, so a cron sees real failure
    signals.

Plan path deviation: written as Python (``scripts/run_scoring.py``)
rather than shell (``scripts/run_scoring.sh``). Python gives us the
project's structlog convention (vs. bash's printf-only structure)
and is testable in CI without invoking the actual scorer.

Usage (cron / fly schedule):
  python -m scripts.run_scoring --city cambridge

Override the lock path for testing:
  STREETSENSE_SCORING_LOCK=/tmp/test.lock python -m scripts.run_scoring --city cambridge

Exit codes:
  0  scoring completed successfully
  1  scoring failed (non-zero from the CLI)
  75 lock held by another live process (EX_TEMPFAIL — cron should retry)
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

# EX_TEMPFAIL from /usr/include/sysexits.h — "temporary failure,
# please retry". Cron / systemd interpret this distinctly from a
# generic 1 so dashboards can separate "scoring failed" from
# "scoring was already running".
_EX_TEMPFAIL = 75

_DEFAULT_LOCK = Path("/tmp/streetsense_scoring.lock")


def _lock_path() -> Path:
    override = os.environ.get("STREETSENSE_SCORING_LOCK")
    return Path(override) if override else _DEFAULT_LOCK


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness check for a process ID.

    POSIX: ``os.kill(pid, 0)`` returns silently if the process exists
    (and we have permission to signal it) and raises ``ProcessLookupError``
    if not. Windows: we don't ship to Windows in prod (Fly Machines +
    Linux containers), but for local-dev testability fall back to
    ``True`` so the wrapper conservatively treats unknown locks as
    held rather than reclaim-then-clobber.
    """
    if sys.platform == "win32":
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but isn't ours — still alive.
        return True
    except OSError as exc:
        return exc.errno != errno.ESRCH
    return True


def _acquire_lock(lock: Path) -> bool:
    """Try to acquire the lock. Returns True on success, False when held."""
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            # Lock present — check whether it's stale.
            try:
                pid_str = lock.read_text(encoding="utf-8").strip()
                pid = int(pid_str) if pid_str else 0
            except (OSError, ValueError):
                pid = 0
            if pid == 0 or not _pid_alive(pid):
                log.warning("scoring.lock_stale_reclaim", pid=pid, lock=str(lock))
                with contextlib.suppress(FileNotFoundError):
                    lock.unlink()
                continue  # retry the create
            log.error("scoring.lock_held", holder_pid=pid, lock=str(lock))
            return False
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
        return True


def _release_lock(lock: Path) -> None:
    try:
        lock.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("scoring.lock_release_error", lock=str(lock), error=str(exc))


def run_scoring(city: str) -> int:
    """Run one scoring invocation under the lock. Returns the underlying CLI's exit code."""
    lock = _lock_path()
    log.info("scoring.start", city=city, lock=str(lock))
    if not _acquire_lock(lock):
        return _EX_TEMPFAIL
    started = time.monotonic()
    try:
        cmd = [sys.executable, "-m", "scoring.cli", "run", "--city", city]
        log.info("scoring.exec", cmd=" ".join(cmd))
        completed = subprocess.run(cmd, check=False)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if completed.returncode == 0:
            log.info("scoring.ok", city=city, elapsed_ms=elapsed_ms)
        else:
            log.error(
                "scoring.failed",
                city=city,
                elapsed_ms=elapsed_ms,
                rc=completed.returncode,
            )
        return completed.returncode
    finally:
        _release_lock(lock)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="streetsense-run-scoring", description=__doc__)
    parser.add_argument(
        "--city",
        required=True,
        help="City config slug (e.g., cambridge).",
    )
    args = parser.parse_args(argv)
    return run_scoring(args.city)


if __name__ == "__main__":
    sys.exit(main())
