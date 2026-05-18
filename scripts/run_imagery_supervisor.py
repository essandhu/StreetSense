"""Resumable supervisor for ``ingestion.cli imagery`` across many cities.

Wraps the imagery ingest with:

* **Retry-on-failure with exponential backoff.** A single network blip,
  Mapillary 5xx, or psycopg pool hiccup kills the inner CLI. The
  supervisor catches the non-zero exit, logs it, sleeps, and re-invokes.
  The job's per-batch commit (``ingestion/imagery/job.py``: "ADR 0005's
  rate-limited and resumable promise depends on flushed batches being
  durable") means re-invocation skips already-stored images.
* **Per-city checkpoint file.** Tracks attempts, last error, and
  terminal status per slug. Re-running the supervisor picks up unfinished
  cities and skips ones already marked ``done``.
* **Optional per-city segment budget** via ``--max-segments``. Hard
  front-slice in the inner job; supervisor just forwards it.

The job itself remains the source of truth for idempotency. The
supervisor adds the *outer* loop that keeps a Mapillary run going
across the transient failures that hit any multi-hour API workload.

Usage:
    python -m scripts.run_imagery_supervisor \
        --cities cambridge,phoenix,san-francisco,austin,los-angeles \
        --max-segments 250 \
        --retries 5

Exit codes:
    0 — all configured cities reached terminal ``done`` state.
    1 — at least one city remained in ``failed`` after exhausting retries.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from dotenv import load_dotenv

log = structlog.get_logger(__name__)

DEFAULT_CHECKPOINT = Path("data/imagery_supervisor_checkpoint.json")
DEFAULT_BACKOFF_BASE = 30.0  # seconds
DEFAULT_BACKOFF_MAX = 600.0  # cap a single sleep at 10 min


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
    )


@dataclass
class CityState:
    slug: str
    attempts: int = 0
    status: str = "pending"  # pending | running | done | failed
    last_error: str | None = None
    last_attempt_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CityState:
        return cls(
            slug=data["slug"],
            attempts=int(data.get("attempts", 0)),
            status=str(data.get("status", "pending")),
            last_error=data.get("last_error"),
            last_attempt_at=data.get("last_attempt_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "attempts": self.attempts,
            "status": self.status,
            "last_error": self.last_error,
            "last_attempt_at": self.last_attempt_at,
        }


@dataclass
class Checkpoint:
    path: Path
    cities: dict[str, CityState] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Checkpoint:
        if not path.exists():
            return cls(path=path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            path=path,
            cities={s["slug"]: CityState.from_dict(s) for s in raw.get("cities", [])},
        )

    def get(self, slug: str) -> CityState:
        if slug not in self.cities:
            self.cities[slug] = CityState(slug=slug)
        return self.cities[slug]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"cities": [s.to_dict() for s in self.cities.values()]}
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _run_imagery(slug: str, *, max_segments: int | None) -> tuple[int, str]:
    """Invoke ``python -m ingestion.cli imagery`` as a subprocess.

    Returns ``(returncode, tail_of_stderr_or_stdout)``. We use a
    subprocess so a hard process-level crash inside Mapillary's SDK or
    a segfault from a native extension can't take the supervisor down.
    """
    cmd = [
        sys.executable,
        "-m",
        "scripts.run_with_dotenv",
        "-m",
        "ingestion.cli",
        "imagery",
        "--city",
        slug,
    ]
    if max_segments is not None:
        cmd.extend(["--max-segments", str(max_segments)])

    log.info("supervisor.subprocess.start", slug=slug, cmd=cmd)
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    # The inner job emits structlog JSON on stdout; surface the last few
    # lines (success or failure) for the supervisor's record.
    tail_source = proc.stderr if proc.returncode != 0 and proc.stderr else proc.stdout
    tail = "\n".join(tail_source.strip().splitlines()[-8:]) if tail_source else ""
    log.info(
        "supervisor.subprocess.done",
        slug=slug,
        returncode=proc.returncode,
        stdout_tail="\n".join(proc.stdout.strip().splitlines()[-3:]) if proc.stdout else "",
    )
    return proc.returncode, tail


def _backoff_seconds(attempt: int, *, base: float, cap: float) -> float:
    """Exponential backoff with a cap. ``attempt`` is 1-indexed."""
    return min(base * (2 ** (attempt - 1)), cap)


def run_supervisor(
    *,
    cities: list[str],
    max_segments: int | None,
    retries: int,
    checkpoint_path: Path,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    backoff_cap: float = DEFAULT_BACKOFF_MAX,
    sleep_fn: Any = time.sleep,
) -> int:
    checkpoint = Checkpoint.load(checkpoint_path)
    overall_ok = True

    for slug in cities:
        state = checkpoint.get(slug)
        if state.status == "done":
            log.info("supervisor.skip.already_done", slug=slug, attempts=state.attempts)
            continue

        while state.attempts < retries:
            state.attempts += 1
            state.status = "running"
            state.last_attempt_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            checkpoint.save()

            log.info(
                "supervisor.city.start",
                slug=slug,
                attempt=state.attempts,
                retries=retries,
                max_segments=max_segments,
            )
            rc, tail = _run_imagery(slug, max_segments=max_segments)
            if rc == 0:
                state.status = "done"
                state.last_error = None
                checkpoint.save()
                log.info("supervisor.city.done", slug=slug, attempts=state.attempts)
                break

            state.status = "pending"
            state.last_error = tail
            checkpoint.save()
            log.warning(
                "supervisor.city.failed",
                slug=slug,
                attempt=state.attempts,
                returncode=rc,
                tail=tail,
            )

            if state.attempts < retries:
                delay = _backoff_seconds(state.attempts, base=backoff_base, cap=backoff_cap)
                log.info("supervisor.city.backoff", slug=slug, delay_seconds=delay)
                sleep_fn(delay)

        if state.status != "done":
            state.status = "failed"
            checkpoint.save()
            overall_ok = False
            log.error(
                "supervisor.city.exhausted",
                slug=slug,
                attempts=state.attempts,
                last_error=state.last_error,
            )

    log.info(
        "supervisor.done",
        ok=overall_ok,
        summary=[s.to_dict() for s in checkpoint.cities.values()],
    )
    return 0 if overall_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_imagery_supervisor")
    parser.add_argument(
        "--cities",
        required=True,
        help="Comma-separated city slugs in order (e.g. cambridge,phoenix).",
    )
    parser.add_argument(
        "--max-segments",
        type=int,
        default=None,
        help="Cap segments per city (forwarded to ingestion.cli). Default: unlimited.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=5,
        help="Max retry attempts per city before giving up.",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=DEFAULT_CHECKPOINT,
        help=f"Per-city checkpoint JSON. Default: {DEFAULT_CHECKPOINT}",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete the checkpoint before running (start from scratch).",
    )
    args = parser.parse_args(argv)

    _configure_logging()
    load_dotenv()

    if args.reset and args.checkpoint.exists():
        args.checkpoint.unlink()
        log.info("supervisor.checkpoint.reset", path=str(args.checkpoint))

    cities = [s.strip() for s in args.cities.split(",") if s.strip()]
    if not cities:
        parser.error("--cities must contain at least one slug")

    return run_supervisor(
        cities=cities,
        max_segments=args.max_segments,
        retries=args.retries,
        checkpoint_path=args.checkpoint,
    )


if __name__ == "__main__":
    raise SystemExit(main())
