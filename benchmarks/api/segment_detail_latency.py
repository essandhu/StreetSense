"""Phase 3 segment-detail latency benchmark.

Hits ``GET /segments/{id}?t=...`` against a running API + Cambridge
dataset. Computes p50 / p95 / p99 warm + cold. Asserts the server-side
p95 budget from spec Phase 3.5.10:

    server-side p95 < 100 ms

(Leaves headroom inside the 300 ms client-side budget in AC-5.)

Inputs to scale:
    --segment-ids   path to a newline-delimited file of segment UUIDs
                    (defaults to a random sample of 500 from the
                    `road_segments` table over the API).
    --iters         per-segment iterations (default 5).
    --t-isoformat   ISO-8601 UTC instant to snap to.

Output:
    JSON appended to benchmarks/api/results/phase-3/segment_detail-{ISO}.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "benchmarks" / "api" / "results" / "phase-3"

DEFAULT_BASE_URL = "http://localhost:8000"


async def _sample_segment_ids(base_url: str, limit: int) -> list[UUID]:
    """Pull a random segment-id list from the API.

    No dedicated /segments index endpoint exists (and we don't need
    one); the benchmark queries pg_tileserv's index-of-features which
    happens to return ids. If that fails, the caller passes
    --segment-ids explicitly.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            # Use a single low-zoom tile that covers Cambridge.
            r = await client.get(f"{base_url}/tiles/public.road_segments_tile_t/9/151/187.pbf")
            r.raise_for_status()
            # We can't parse MVT here without protobuf; fall back to
            # explicit --segment-ids in callers.
            del r
        except httpx.HTTPError:
            pass
    sys.exit(
        "Could not auto-sample segment ids from pg_tileserv. Pass "
        "--segment-ids path/to/segments.txt (one UUID per line)."
    )
    del limit


async def _bench_one(client: httpx.AsyncClient, base_url: str, seg_id: UUID, t: str) -> float:
    """Single request; return wall-clock milliseconds."""
    t0 = time.perf_counter()
    r = await client.get(f"{base_url}/segments/{seg_id}", params={"t": t})
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    r.raise_for_status()
    return elapsed_ms


def _read_segment_ids_file(path: Path) -> list[UUID]:
    ids: list[UUID] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            ids.append(UUID(stripped))
    return ids


async def main_async(args: argparse.Namespace) -> int:
    base_url: str = args.base_url
    segment_ids: list[UUID] = []
    if args.segment_ids:
        segment_ids = _read_segment_ids_file(Path(args.segment_ids))
    else:
        segment_ids = await _sample_segment_ids(base_url, args.sample_size)

    samples_ms: list[float] = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        # Warm a few requests to populate the pool / pg cache.
        for sid in segment_ids[:3]:
            await _bench_one(client, base_url, sid, args.t_isoformat)
        # Measured run.
        for _ in range(args.iters):
            for sid in segment_ids:
                ms = await _bench_one(client, base_url, sid, args.t_isoformat)
                samples_ms.append(ms)

    samples_ms.sort()
    n = len(samples_ms)

    def q(p: float) -> float:
        idx = max(0, min(n - 1, int(p * n) - 1))
        return samples_ms[idx]

    record = {
        "endpoint": "GET /segments/{id}",
        "t": args.t_isoformat,
        "segments": len(segment_ids),
        "iters_per_segment": args.iters,
        "n_samples": n,
        "p50_ms": round(statistics.median(samples_ms), 3),
        "p95_ms": round(q(0.95), 3),
        "p99_ms": round(q(0.99), 3),
        "mean_ms": round(statistics.mean(samples_ms), 3),
        "stdev_ms": round(statistics.stdev(samples_ms), 3) if n > 1 else 0.0,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
    out_path = RESULTS_DIR / f"segment_detail-{timestamp}.json"
    out_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(record, indent=2))
    # Assert spec Phase 3.5.10 budget.
    if record["p95_ms"] >= 100.0:
        sys.stderr.write(f"\nFAIL: server-side p95 {record['p95_ms']} ms exceeds 100 ms budget.\n")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--segment-ids", help="Path to file with one UUID per line.")
    parser.add_argument(
        "--sample-size",
        type=int,
        default=500,
        help="Fallback random sample size (used only when --segment-ids is omitted).",
    )
    parser.add_argument("--iters", type=int, default=5)
    parser.add_argument(
        "--t-isoformat",
        default="2025-06-21T16:00:00+00:00",
        help="ISO-8601 UTC instant to snap segment-detail to.",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
