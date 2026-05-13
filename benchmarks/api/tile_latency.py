"""Tile-endpoint latency benchmark.

Hits `pg_tileserv` for a configurable z/x/y range, computes p50/p95/p99
warm and cold, and asserts the budgets from CLAUDE.md / spec.md AC-3:

  warm p99 < 200 ms
  cold p99 < 800 ms

"Cold" runs hit each tile once after a fresh start (no PG cache), then
"warm" runs hit each tile again to measure the cached path. The driver
warms the cache between phases by issuing all tiles once and discarding
results.

Usage:
    python -m benchmarks.api.tile_latency \
        --base-url http://localhost:7800 \
        --layer public.road_segments_tile \
        --bbox -71.16 42.35 -71.07 42.41 \
        --zoom 14 \
        --concurrency 8

Output:
    JSON appended to benchmarks/api/results/tile_latency-{ISO}.json. Read by
    the Phase 1.7 integration check; failure is a non-zero exit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "benchmarks" / "api" / "results"


def lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    n = 2.0**zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def tiles_in_bbox(bbox: tuple[float, float, float, float], zoom: int) -> list[tuple[int, int, int]]:
    min_lon, min_lat, max_lon, max_lat = bbox
    x0, y0 = lonlat_to_tile(min_lon, max_lat, zoom)  # NW
    x1, y1 = lonlat_to_tile(max_lon, min_lat, zoom)  # SE
    tiles: list[tuple[int, int, int]] = []
    for x in range(min(x0, x1), max(x0, x1) + 1):
        for y in range(min(y0, y1), max(y0, y1) + 1):
            tiles.append((zoom, x, y))
    return tiles


async def _fetch_tile(
    client: httpx.AsyncClient,
    base_url: str,
    layer: str,
    z: int,
    x: int,
    y: int,
) -> float:
    """Fetch one MVT and return its latency in seconds."""
    url = f"{base_url}/tiles/{layer}/{z}/{x}/{y}.pbf"
    t0 = time.perf_counter()
    resp = await client.get(url, timeout=10.0)
    elapsed = time.perf_counter() - t0
    resp.raise_for_status()
    return elapsed


async def _run_phase(
    base_url: str,
    layer: str,
    tiles: list[tuple[int, int, int]],
    concurrency: int,
) -> list[float]:
    sem = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient() as client:

        async def _one(t: tuple[int, int, int]) -> float:
            z, x, y = t
            async with sem:
                return await _fetch_tile(client, base_url, layer, z, x, y)

        return list(await asyncio.gather(*(_one(t) for t in tiles)))


def _percentiles(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {"count": 0}
    sorted_samples = sorted(samples)
    return {
        "count": len(samples),
        "p50_ms": statistics.median(sorted_samples) * 1000.0,
        "p95_ms": _quantile(sorted_samples, 0.95) * 1000.0,
        "p99_ms": _quantile(sorted_samples, 0.99) * 1000.0,
        "max_ms": sorted_samples[-1] * 1000.0,
        "mean_ms": statistics.fmean(sorted_samples) * 1000.0,
    }


def _quantile(sorted_samples: list[float], q: float) -> float:
    if not sorted_samples:
        return 0.0
    idx = max(0, min(len(sorted_samples) - 1, math.ceil(q * len(sorted_samples)) - 1))
    return sorted_samples[idx]


async def benchmark(
    base_url: str,
    layer: str,
    bbox: tuple[float, float, float, float],
    zoom: int,
    concurrency: int,
) -> dict[str, object]:
    tiles = tiles_in_bbox(bbox, zoom)
    if not tiles:
        raise SystemExit(f"no tiles for bbox={bbox} zoom={zoom}")

    cold = await _run_phase(base_url, layer, tiles, concurrency)
    warm = await _run_phase(base_url, layer, tiles, concurrency)

    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "base_url": base_url,
        "layer": layer,
        "bbox": list(bbox),
        "zoom": zoom,
        "tile_count": len(tiles),
        "concurrency": concurrency,
        "cold": _percentiles(cold),
        "warm": _percentiles(warm),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tile_latency")
    parser.add_argument("--base-url", default="http://localhost:7800")
    parser.add_argument("--layer", default="public.road_segments_tile")
    parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
        default=[-71.16, 42.35, -71.07, 42.41],
    )
    parser.add_argument("--zoom", type=int, default=14)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument(
        "--budget-warm-p99-ms",
        type=float,
        default=200.0,
        help="Warm p99 budget in ms (CLAUDE.md / spec.md AC-3).",
    )
    parser.add_argument(
        "--budget-cold-p99-ms",
        type=float,
        default=800.0,
        help="Cold p99 budget in ms.",
    )
    parser.add_argument(
        "--no-assert",
        action="store_true",
        help="Record results without asserting budgets (use during phase 1.5 dev).",
    )
    args = parser.parse_args(argv)

    bbox: tuple[float, float, float, float] = (
        args.bbox[0],
        args.bbox[1],
        args.bbox[2],
        args.bbox[3],
    )

    result = asyncio.run(benchmark(args.base_url, args.layer, bbox, args.zoom, args.concurrency))

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"tile_latency-{result['timestamp'].replace(':', '-')}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print(json.dumps(result, indent=2))

    cold_p99 = float(result["cold"]["p99_ms"]) if result["cold"]["count"] else 0.0  # type: ignore[index]
    warm_p99 = float(result["warm"]["p99_ms"]) if result["warm"]["count"] else 0.0  # type: ignore[index]

    if args.no_assert:
        return 0

    failed = False
    if warm_p99 > args.budget_warm_p99_ms:
        print(
            f"FAIL warm p99 = {warm_p99:.1f} ms > budget {args.budget_warm_p99_ms:.1f} ms",
            file=sys.stderr,
        )
        failed = True
    if cold_p99 > args.budget_cold_p99_ms:
        print(
            f"FAIL cold p99 = {cold_p99:.1f} ms > budget {args.budget_cold_p99_ms:.1f} ms",
            file=sys.stderr,
        )
        failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
