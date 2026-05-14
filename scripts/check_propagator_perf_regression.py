"""Propagator perf-regression CI gate — Task 4.8.4.

Compares the current run's Google Benchmark output against the latest
baseline committed under ``benchmarks/propagator/history.jsonl``.
Regressions > ``REGRESSION_GATE`` (10 % by default) in any size class
block the PR.

The script accepts two inputs:

  - ``--current``: a JSON file produced by ``propagator_bench
    --benchmark_format=json --benchmark_out=<path>``.
  - ``--history``: the rolling history file under
    ``benchmarks/propagator/history.jsonl``. Each line is a JSON
    object with ``commit_sha``, ``timestamp_utc``, and per-benchmark
    median wall-clocks in milliseconds.

A baseline is the most recent entry. If the history is empty (first
run), the script appends the current results and exits 0.

Usage:
    python -m scripts.check_propagator_perf_regression \\
        --current build/default/bench/propagator_bench.json \\
        --history benchmarks/propagator/history.jsonl \\
        [--commit-sha $(git rev-parse HEAD)]

Exit codes:
  0  no regression
  1  regression detected
  2  malformed input

Refs:
  - conductor/tracks/phase-4-propagator/plan.md Task 4.8.4
  - scoring/propagator/bench/propagator_bench.cc
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REGRESSION_GATE = 0.10  # 10 % regression triggers a block


def _parse_google_benchmark_output(path: Path) -> dict[str, float]:
    """Extract per-benchmark median wall-clocks (in milliseconds)."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, float] = {}
    for entry in payload.get("benchmarks", []):
        # Google Benchmark emits aggregate entries with names suffixed
        # ``_mean``, ``_median``, ``_stddev``. Filter to the
        # ``_median`` aggregates so a single regression value per size
        # class is compared.
        name = entry.get("name", "")
        if not name.endswith("_median"):
            continue
        # Strip the "_median" suffix so the canonical key matches the
        # base benchmark id.
        base_name = name[: -len("_median")]
        real_time = entry.get("real_time")
        time_unit = entry.get("time_unit", "ns")
        if real_time is None:
            continue
        # Normalize to milliseconds.
        scale = {"ns": 1e-6, "us": 1e-3, "ms": 1.0, "s": 1e3}.get(time_unit, 1.0)
        out[base_name] = float(real_time) * scale
    return out


def _read_history(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    entries: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        entries.append(json.loads(line))
    return entries


def _append_history(path: Path, entry: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True))
        f.write("\n")


def compare(
    current: dict[str, float],
    baseline: dict[str, float],
    gate: float = REGRESSION_GATE,
) -> list[tuple[str, float, float, float]]:
    """Return [(benchmark, baseline_ms, current_ms, ratio)] for regressions.

    A regression is current_ms > baseline_ms * (1 + gate). The
    returned list is empty when no regression triggers.
    """
    regressions: list[tuple[str, float, float, float]] = []
    for name, current_ms in current.items():
        baseline_ms = baseline.get(name)
        if baseline_ms is None or baseline_ms <= 0.0:
            continue
        ratio = current_ms / baseline_ms
        if ratio > 1.0 + gate:
            regressions.append((name, baseline_ms, current_ms, ratio))
    return regressions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--current",
        type=Path,
        required=True,
        help="Path to Google Benchmark JSON output for the current run.",
    )
    parser.add_argument(
        "--history",
        type=Path,
        default=Path("benchmarks/propagator/history.jsonl"),
    )
    parser.add_argument("--commit-sha", type=str, default="unknown")
    parser.add_argument(
        "--gate",
        type=float,
        default=REGRESSION_GATE,
        help="Fractional regression that triggers a block (default: 0.10).",
    )
    parser.add_argument(
        "--no-append",
        action="store_true",
        help="Do not append the current run to the history file (for dry runs).",
    )
    args = parser.parse_args(argv)

    if not args.current.exists():
        print(f"ERROR: {args.current} does not exist", file=sys.stderr)
        return 2
    current = _parse_google_benchmark_output(args.current)
    if not current:
        print("ERROR: no median benchmark entries in current results", file=sys.stderr)
        return 2

    history = _read_history(args.history)
    if not history:
        print("No prior baseline; recording current run as the first entry.")
        if not args.no_append:
            _append_history(
                args.history,
                {
                    "commit_sha": args.commit_sha,
                    "timestamp_utc": datetime.now(UTC).isoformat(),
                    "median_ms": current,
                },
            )
        return 0

    baseline_entry = history[-1]
    baseline = baseline_entry.get("median_ms")
    if not isinstance(baseline, dict):
        print("ERROR: malformed history baseline; missing median_ms", file=sys.stderr)
        return 2

    # We have validated the dict shape above; the comparator handles
    # missing per-benchmark entries gracefully (no regression
    # triggered for unknown names).
    baseline_typed: dict[str, float] = {str(k): float(v) for k, v in baseline.items()}
    regressions = compare(current, baseline_typed, gate=args.gate)
    if regressions:
        print("FAIL: propagator perf regressions:")
        for name, baseline_ms, current_ms, ratio in regressions:
            print(
                f"  {name}: {baseline_ms:.3f} ms -> {current_ms:.3f} ms "
                f"({(ratio - 1.0) * 100:.1f}% slower)"
            )
        return 1

    print(f"OK: no regression > {args.gate * 100:.0f}%. Comparison vs baseline:")
    for name, current_ms in sorted(current.items()):
        baseline_ms = baseline_typed.get(name, current_ms)
        delta_pct = ((current_ms - baseline_ms) / max(baseline_ms, 1e-9)) * 100
        print(f"  {name}: {baseline_ms:.3f} ms -> {current_ms:.3f} ms ({delta_pct:+.1f}%)")
    if not args.no_append:
        _append_history(
            args.history,
            {
                "commit_sha": args.commit_sha,
                "timestamp_utc": datetime.now(UTC).isoformat(),
                "median_ms": current,
            },
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
