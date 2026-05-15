"""Full scoring-run wall-clock benchmark — Task 4.8.6.

Measures end-to-end ``make scoring-run`` wall-clock on the seeded
Cambridge dataset with all four real Phase 4 scorers (glare,
lane-marking, junction-complexity, historical-correlation) plus the
24-way parallel propagator. Asserts the overnight-budget invariant
from ``CLAUDE.md`` and ``conductor/workflow.md``:

    End-to-end scoring run: completes within a single overnight window
    for one city.

For the perf gate, "overnight window" is treated as **8 hours by
default** (28 800 s). The threshold is configurable via
``--budget-seconds`` for environments where a tighter or looser bound
is appropriate.

Operationally, the script:

1. Spawns ``python -m scoring.cli run --city <city> --day <day>`` in a
   subprocess.
2. Wall-clocks the subprocess externally.
3. Parses the final ``scoring_cli.summary`` JSON line from stdout to
   harvest the in-process timing breakdown (per-scorer + per-hour
   propagation + composite assembly).
4. Writes a structured result JSON to
   ``benchmarks/scoring/results/phase-4/full_run_walltime-{ISO}.json``.
5. Asserts the wall-clock budget; non-zero exit on regression.

The scoring run mutates the live database (appends to
``scoring_runs`` and ``segment_scores``); the benchmark is therefore
an end-to-end production-equivalent invocation, not a synthetic
microbench. Run it against a freshly-seeded Cambridge stack
(``make seed && make ingest-imagery && make seed-model && make
ingest-incidents``) for representative timing.

Usage:
    python -m benchmarks.scoring.full_run_walltime
    python -m benchmarks.scoring.full_run_walltime --city cambridge --day 2025-06-21
    python -m benchmarks.scoring.full_run_walltime --budget-seconds 14400
    python -m benchmarks.scoring.full_run_walltime --json results.json

Refs:
  - conductor/tracks/phase-4-propagator/plan.md Task 4.8.6
  - conductor/tracks/phase-4-propagator/spec.md §"Performance budgets"
  - CLAUDE.md §"Performance Budgets (Hard)"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "benchmarks" / "scoring" / "results" / "phase-4"

DEFAULT_CITY = "cambridge"
DEFAULT_REFERENCE_DAY = "2025-06-21"

# Overnight budget — see CLAUDE.md §"Performance Budgets (Hard)".
# 8 hours = 28 800 s. Configurable via --budget-seconds for tighter or
# looser local thresholds.
DEFAULT_BUDGET_SECONDS = 28_800.0

# Propagation sub-budget — Technical Note 2 of the spec targets
# < 30 s wall-clock for the 24-hour parallel propagation step. Tracked
# separately so a propagator-only regression is visible even when the
# overall budget still holds.
DEFAULT_PROPAGATION_BUDGET_SECONDS = 60.0


def _find_summary_line(lines: list[str]) -> dict[str, Any]:
    """Pick the final ``scoring_cli.summary`` JSON line from CLI stdout.

    The scoring CLI emits structlog JSON events to stdout plus one
    final line tagged ``"event": "scoring_cli.summary"`` carrying the
    in-process timing breakdown. The summary is the source of truth
    for per-stage seconds; the externally measured wall-clock is the
    cross-check.
    """
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("event") == "scoring_cli.summary":
            return payload
    raise SystemExit(
        "Could not locate `scoring_cli.summary` in CLI stdout. Check "
        "that `scoring.cli` emitted its summary JSON line."
    )


def _invoke_scoring_run(city: str, day: str) -> tuple[float, dict[str, Any], str]:
    """Run the scoring CLI as a subprocess; return (wall_seconds, summary, stdout).

    The subprocess is launched through ``scripts/run_with_dotenv.py`` so
    ``.env`` is loaded into the child's ``os.environ`` exactly as
    ``uv run`` would. This keeps DATABASE_URL / MINIO_* available
    without requiring uv to be on PATH (Windows contributors).
    """
    cmd = [
        sys.executable,
        "scripts/run_with_dotenv.py",
        "-m",
        "scoring.cli",
        "run",
        "--city",
        city,
        "--day",
        day,
    ]
    env = os.environ.copy()

    t0 = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    wall_seconds = time.perf_counter() - t0

    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(
            f"scoring.cli exited with returncode {proc.returncode}; aborting benchmark."
        )

    summary = _find_summary_line(proc.stdout.splitlines())
    return wall_seconds, summary, proc.stdout


def _propagation_p99(per_hour: list[float]) -> float:
    if not per_hour:
        return 0.0
    sorted_calls = sorted(per_hour)
    idx = max(0, len(sorted_calls) - 1)
    return sorted_calls[idx]


def _format_seconds(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.2f}s"
    if seconds < 3600:
        return f"{seconds / 60:.2f}m"
    return f"{seconds / 3600:.2f}h"


def benchmark(
    *,
    city: str,
    day: str,
    budget_seconds: float,
    propagation_budget_seconds: float,
) -> dict[str, Any]:
    """Run the scoring CLI once; capture wall-clock + in-process timings.

    The returned dict is the canonical benchmark record, suitable for
    JSON serialization and for the assertion gate.
    """
    wall_seconds, summary, raw_stdout = _invoke_scoring_run(city, day)

    seconds_elapsed = float(summary.get("seconds_elapsed", 0.0))
    propagation_total = float(summary.get("propagation_total_seconds", 0.0))
    per_hour = [float(s) for s in summary.get("propagation_per_hour_seconds", [])]
    rows_written = int(summary.get("rows_written", 0))
    segments_processed = int(summary.get("segments_processed", 0))
    temporal_samples = int(summary.get("temporal_samples", 0))

    meets_overall_budget = wall_seconds <= budget_seconds
    meets_propagation_budget = propagation_total <= propagation_budget_seconds

    record: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "city": city,
        "reference_day": day,
        "run_id": summary.get("run_id"),
        "wall_seconds": round(wall_seconds, 3),
        "wall_human": _format_seconds(wall_seconds),
        "cli_seconds_elapsed": round(seconds_elapsed, 3),
        "rows_written": rows_written,
        "segments_processed": segments_processed,
        "temporal_samples": temporal_samples,
        "propagation_total_seconds": round(propagation_total, 3),
        "propagation_per_hour_seconds": [round(s, 4) for s in per_hour],
        "propagation_per_hour_mean": round(sum(per_hour) / len(per_hour), 4) if per_hour else 0.0,
        "propagation_per_hour_max": round(max(per_hour), 4) if per_hour else 0.0,
        "propagation_per_hour_p99": round(_propagation_p99(per_hour), 4),
        "perception_model_version": summary.get("perception_model_version"),
        "propagation_algorithm_version": summary.get("propagation_algorithm_version"),
        "imagery_capture_window": summary.get("imagery_capture_window"),
        "incidents_window": summary.get("incidents_window"),
        "composite_weights": summary.get("composite_weights"),
        "stub_fallback_lane_marking_rows": summary.get("stub_fallback_lane_marking_rows"),
        "budget_seconds": budget_seconds,
        "budget_human": _format_seconds(budget_seconds),
        "propagation_budget_seconds": propagation_budget_seconds,
        "meets_overall_budget": meets_overall_budget,
        "meets_propagation_budget": meets_propagation_budget,
        "raw_stdout_bytes": len(raw_stdout),
    }
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", default=DEFAULT_CITY)
    parser.add_argument(
        "--day",
        default=DEFAULT_REFERENCE_DAY,
        help="ISO-8601 date for the 24-hourly sample schedule.",
    )
    parser.add_argument(
        "--budget-seconds",
        type=float,
        default=DEFAULT_BUDGET_SECONDS,
        help=(
            "Overall wall-clock budget. Default: 28 800 s "
            "(8 hours, the 'single overnight window' invariant)."
        ),
    )
    parser.add_argument(
        "--propagation-budget-seconds",
        type=float,
        default=DEFAULT_PROPAGATION_BUDGET_SECONDS,
        help=(
            "Sub-budget for the 24-hour parallel propagation step "
            "(spec Technical Note 2 targets < 30 s; default here is "
            "60 s to leave headroom on slower dev machines)."
        ),
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help=(
            "Optional path to write the result JSON. If omitted, a "
            "timestamped file is dropped under "
            "benchmarks/scoring/results/phase-4/."
        ),
    )
    parser.add_argument(
        "--no-assert",
        action="store_true",
        help="Report results without enforcing the budget (exit 0 either way).",
    )
    args = parser.parse_args(argv)

    record = benchmark(
        city=args.city,
        day=args.day,
        budget_seconds=args.budget_seconds,
        propagation_budget_seconds=args.propagation_budget_seconds,
    )

    out_path = args.json
    if out_path is None:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = record["timestamp"].replace(":", "-")
        out_path = RESULTS_DIR / f"full_run_walltime-{ts}.json"
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)

    out_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))

    if args.no_assert:
        return 0

    failed = False
    if not record["meets_overall_budget"]:
        sys.stderr.write(
            f"\nFAIL: wall-clock {record['wall_human']} "
            f"({record['wall_seconds']:.1f}s) exceeds overnight budget "
            f"{record['budget_human']} ({args.budget_seconds:.0f}s).\n"
        )
        failed = True
    if not record["meets_propagation_budget"]:
        sys.stderr.write(
            f"\nFAIL: propagation total {record['propagation_total_seconds']:.1f}s "
            f"exceeds budget {args.propagation_budget_seconds:.0f}s.\n"
        )
        failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
