"""Phase 4 segment-detail latency benchmark — Task 4.7.8.

Re-runs the Phase 3 segment-detail latency benchmark
(`segment_detail_latency.py`) against the Phase 4 API, with two extras:

  1. Asserts the response now carries the four new Phase 4 fields —
     `composite_risk`, `propagation_uplift`, `local_contribution`, and
     `propagation_algorithm` — alongside the four `SubScore`s.
  2. Auto-samples segment ids directly from PostGIS (instead of the
     Phase 3 escape hatch of requiring `--segment-ids`), so the
     benchmark is fully self-contained against a seeded Cambridge
     stack.

The latency budget is unchanged from Phase 3 / spec.md AC-7:

    server-side p95 < 100 ms

(The two new fields are existing-row reads from the same
`segment_scores` row — no JOIN added — so the Phase 3 measurement of
34 ms p95 should hold easily with the additions.)

Inputs:
    --base-url      API base URL (default: http://localhost:8000)
    --sample-size   How many segment ids to sample from `segment_scores`
                    for the Phase 4 scoring run (default 500).
    --iters         Per-segment iterations (default 5; 500 * 5 = 2500
                    samples total).
    --t-isoformat   ISO-8601 UTC instant to snap segment-detail to
                    (default 2025-06-21T16:00:00+00:00; matches the
                    `make scoring-run` default reference day).
    --budget-p95-ms p95 budget in ms (default 100; spec AC-7).

Output:
    JSON dropped under
    benchmarks/api/results/phase-4/segment_detail-{ISO}.json.

Refs:
  - conductor/tracks/phase-4-propagator/plan.md Task 4.7.8
  - conductor/tracks/phase-4-propagator/spec.md AC-7
  - benchmarks/api/segment_detail_latency.py (Phase 3 precursor)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import psycopg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "benchmarks" / "api" / "results" / "phase-4"

DEFAULT_BASE_URL = "http://localhost:8000"

# Field-presence assertions: every Phase 4 segment-detail response must
# carry these top-level keys, and `subscores` must contain all four.
REQUIRED_TOP_LEVEL_FIELDS = (
    "composite_risk",
    "propagation_uplift",
    "local_contribution",
    "propagation_algorithm",
    "sub_scores",
    "confidence",
)
REQUIRED_SUBSCORES = (
    "glare_exposure",
    "lane_marking_quality",
    "junction_complexity",
    "historical_correlation",
)


def _psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _sample_segment_ids_from_db(limit: int) -> list[UUID]:
    """Pull `limit` random segment ids from the most recent scoring run.

    Sampling from `segment_scores` (rather than `road_segments`) ensures
    every id has a hit in the segment-detail endpoint — segments without
    scoring rows would 404 and noise the benchmark.
    """
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL not set; source .env or copy .env.example to .env.")

    with psycopg.connect(_psycopg_dsn(url)) as conn, conn.cursor() as cur:
        # Sample distinct segment ids; ORDER BY random() requires the
        # randomizer in the SELECT list when used with DISTINCT, hence
        # the subquery shape.
        cur.execute(
            """
            SELECT segment_id FROM (
                SELECT DISTINCT segment_id
                FROM segment_scores
                WHERE scoring_run_id = (
                    SELECT id FROM scoring_runs
                    ORDER BY scoring_run_timestamp DESC LIMIT 1
                )
            ) AS distinct_ids
            ORDER BY random()
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
    if not rows:
        sys.exit("No rows in segment_scores; run `make scoring-run` before the benchmark.")
    return [r[0] for r in rows]


async def _bench_one(
    client: httpx.AsyncClient,
    base_url: str,
    seg_id: UUID,
    t: str,
) -> tuple[float, dict[str, Any]]:
    """Single request; return (elapsed_ms, parsed_json_body)."""
    t0 = time.perf_counter()
    r = await client.get(f"{base_url}/segments/{seg_id}", params={"t": t})
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    r.raise_for_status()
    return elapsed_ms, r.json()


def _validate_response_shape(body: dict[str, Any]) -> list[str]:
    """Return a list of missing fields (empty list = all good)."""
    missing: list[str] = []
    for field in REQUIRED_TOP_LEVEL_FIELDS:
        if field not in body:
            missing.append(field)
    sub_scores = body.get("sub_scores", {})
    if isinstance(sub_scores, dict):
        for sub in REQUIRED_SUBSCORES:
            if sub not in sub_scores:
                missing.append(f"sub_scores.{sub}")
    return missing


async def main_async(args: argparse.Namespace) -> int:
    base_url: str = args.base_url

    segment_ids = _sample_segment_ids_from_db(args.sample_size)
    print(f"Sampled {len(segment_ids)} segment ids from the latest scoring run.")

    samples_ms: list[float] = []
    shape_errors: dict[str, int] = {}

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Warm a few requests to populate the connection pool + pg cache.
        for sid in segment_ids[:3]:
            await _bench_one(client, base_url, sid, args.t_isoformat)

        # Field-presence audit on the FIRST measured response — we
        # don't validate every response (overhead) but we do gate the
        # benchmark on the API shape being right.
        first_ms, first_body = await _bench_one(client, base_url, segment_ids[0], args.t_isoformat)
        samples_ms.append(first_ms)
        missing = _validate_response_shape(first_body)
        if missing:
            sys.stderr.write(
                f"\nFAIL: Phase 4 segment-detail response is missing fields: {missing}\n"
            )
            return 1

        # Measured run.
        for _ in range(args.iters):
            for sid in segment_ids:
                ms, _ = await _bench_one(client, base_url, sid, args.t_isoformat)
                samples_ms.append(ms)

    samples_ms.sort()
    n = len(samples_ms)

    def q(p: float) -> float:
        idx = max(0, min(n - 1, int(p * n) - 1))
        return samples_ms[idx]

    record = {
        "endpoint": "GET /segments/{id}",
        "phase": "phase-4",
        "t": args.t_isoformat,
        "segments": len(segment_ids),
        "iters_per_segment": args.iters,
        "n_samples": n,
        "p50_ms": round(statistics.median(samples_ms), 3),
        "p95_ms": round(q(0.95), 3),
        "p99_ms": round(q(0.99), 3),
        "mean_ms": round(statistics.mean(samples_ms), 3),
        "stdev_ms": round(statistics.stdev(samples_ms), 3) if n > 1 else 0.0,
        "budget_p95_ms": args.budget_p95_ms,
        "shape_check_errors": shape_errors,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
    out_path = RESULTS_DIR / f"segment_detail-{timestamp}.json"
    out_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(record, indent=2))

    if args.no_assert:
        return 0

    if record["p95_ms"] >= args.budget_p95_ms:
        sys.stderr.write(
            f"\nFAIL: server-side p95 {record['p95_ms']} ms exceeds "
            f"{args.budget_p95_ms} ms budget.\n"
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--sample-size",
        type=int,
        default=500,
        help="Random sample size of segment ids from segment_scores.",
    )
    parser.add_argument("--iters", type=int, default=5)
    parser.add_argument(
        "--t-isoformat",
        default="2025-06-21T16:00:00+00:00",
        help="ISO-8601 UTC instant to snap segment-detail to.",
    )
    parser.add_argument(
        "--budget-p95-ms",
        type=float,
        default=100.0,
        help="p95 server-side latency budget in ms (spec AC-7).",
    )
    parser.add_argument(
        "--no-assert",
        action="store_true",
        help="Report results without enforcing the budget.",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
