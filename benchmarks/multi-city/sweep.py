"""Phase 4b Task 5.2 — per-city benchmark sweep.

Drives ``benchmarks.api.tile_latency_t_varying.benchmark`` for each
seeded city, reading the bbox from ``config/cities/{slug}.yaml``.
Records:

* warm + cold p50/p95/p99 (ms) per city
* tile_count and request_count per phase (sanity: same shape across cities)

Outputs:

* JSON per city in ``benchmarks/multi-city/results/``
* a combined ``benchmarks/multi-city/{date}.md`` table that
  Task 5.4 (PHASE_4B_DEMO.md) links to

Per CLAUDE.md / spec.md AC-3 the budget is

    warm p99 < 200 ms
    cold p99 < 800 ms

Any city exceeding the warm budget is flagged in the markdown
output but the sweep continues — the goal is *measure all five
cities*, not bail on the first breach. Whether a breach blocks the
phase is a judgment call recorded in the markdown summary.

Usage::

    python -m benchmarks.multi-city.sweep \\
        --base-url http://localhost:7800 \\
        --zoom 14 \\
        --concurrency 1

The defaults match the Phase 2 baseline run so cambridge numbers
are directly comparable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config" / "cities"
OUT_DIR = REPO_ROOT / "benchmarks" / "multi-city"
RESULTS_DIR = OUT_DIR / "results"

# Avoid an import cycle: importing tile_latency_t_varying at module
# import time also imports httpx; only do it inside main() so plain
# `--help` works without httpx installed.
sys.path.insert(0, str(REPO_ROOT))


def discover_cities() -> list[dict[str, object]]:
    """Read every ``config/cities/*.yaml`` (skipping ``__schema__``)."""
    cities: list[dict[str, object]] = []
    for path in sorted(CONFIG_DIR.glob("*.yaml")):
        if path.stem.startswith("__"):
            continue
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise SystemExit(f"unexpected YAML root in {path}")
        cities.append(cast(dict[str, object], data))
    return cities


def _fmt(v: float) -> str:
    return f"{v:.1f}"


def _budget_flag(value: float, budget: float) -> str:
    return "  " if value <= budget else " !"


def render_markdown(
    results: list[dict[str, object]],
    warm_budget_ms: float,
    cold_budget_ms: float,
    zoom: int,
    concurrency: int,
    sample_window_deg: float,
) -> str:
    """Produce the multi-city benchmark summary."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    lines: list[str] = []
    lines.append(f"# Multi-City Tile Latency — {today}")
    lines.append("")
    lines.append(
        "Per-city tile endpoint latency for the Phase 4b shipped set. "
        "Driver: `benchmarks/multi-city/sweep.py`, which calls "
        "`benchmarks/api/tile_latency_t_varying.py` per city with a "
        f"fixed {sample_window_deg:g}° × {sample_window_deg:g}° window "
        "centered on each city's bbox (matches the Cambridge bbox "
        "span — apples-to-apples per-tile latency, not 'all of LA vs a "
        "neighborhood of Cambridge'). Each phase issues "
        "`tile_count × 24` requests (one per hour of the reference day). "
        f"Zoom: {zoom}. Concurrency: {concurrency} (matches Phase 2 "
        "scrubber profile)."
    )
    lines.append("")
    lines.append(
        f"Budget per CLAUDE.md / spec.md AC-3: warm p99 < {warm_budget_ms:.0f} ms, "
        f"cold p99 < {cold_budget_ms:.0f} ms. ` !` flags a breach."
    )
    lines.append("")
    lines.append(
        "| City          | Segments (city) | Tiles/phase | Warm p99 (ms) | Cold p99 (ms) | Warm p50 (ms) | Cold p50 (ms) |"
    )
    lines.append(
        "|---------------|-----------------|-------------|---------------|---------------|---------------|---------------|"
    )

    for row in results:
        slug = cast(str, row["city_slug"])
        warm = cast(dict[str, float], row["warm"])
        cold = cast(dict[str, float], row["cold"])
        tile_count = cast(int, row["tile_count"])
        seg_count = cast(int, row.get("segment_count", 0))
        warm_p99 = warm["p99_ms"]
        cold_p99 = cold["p99_ms"]
        lines.append(
            "| {slug:<13} | {seg:>15,} | {tc:>11} | "
            "{wp99:>10}{wf} | {cp99:>10}{cf} | {wp50:>13} | {cp50:>13} |".format(
                slug=slug,
                seg=seg_count,
                tc=tile_count,
                wp99=_fmt(warm_p99),
                wf=_budget_flag(warm_p99, warm_budget_ms),
                cp99=_fmt(cold_p99),
                cf=_budget_flag(cold_p99, cold_budget_ms),
                wp50=_fmt(warm["p50_ms"]),
                cp50=_fmt(cold["p50_ms"]),
            )
        )

    lines.append("")
    lines.append("## Raw results")
    lines.append("")
    for row in results:
        slug = cast(str, row["city_slug"])
        lines.append(f"- `results/sweep-{slug}.json`")
    lines.append("")
    return "\n".join(lines)


def _sample_window(
    bbox: tuple[float, float, float, float], window_deg: float
) -> tuple[float, float, float, float]:
    """Centered ``window_deg`` × ``window_deg`` sample around bbox center.

    Cities range from ~0.06° (Cambridge) to ~0.64° (Los Angeles) in
    span; sweeping every tile inside the full bbox at z=14 ranges
    from 25 tiles (Cambridge) to ~250 tiles (Los Angeles), making
    per-city latency comparisons unfair (LA's tail is sampled 10×
    more times than Cambridge's). A fixed window centered on each
    city's bbox center samples the same number of tiles per city
    — the percentile is then a measurement of *the cost of a
    representative tile* in that city, not "all of LA vs a
    neighborhood of Cambridge".
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    cx = (min_lon + max_lon) / 2.0
    cy = (min_lat + max_lat) / 2.0
    half = window_deg / 2.0
    return (cx - half, cy - half, cx + half, cy + half)


async def run_sweep(
    base_url: str,
    zoom: int,
    reference_day: str,
    concurrency: int,
    layer: str,
    sample_window_deg: float,
) -> list[dict[str, object]]:
    from benchmarks.api.tile_latency_t_varying import benchmark as t_benchmark

    cities = discover_cities()
    results: list[dict[str, object]] = []

    for city in cities:
        slug = cast(str, city["slug"])
        bbox_list = cast(list[float], city["bbox"])
        full_bbox = (bbox_list[0], bbox_list[1], bbox_list[2], bbox_list[3])
        bbox = _sample_window(full_bbox, sample_window_deg)
        print(f"[{slug}] running tile latency sweep at z={zoom}...", flush=True)
        result = await t_benchmark(
            base_url=base_url,
            layer=layer,
            bbox=bbox,
            zoom=zoom,
            reference_day=reference_day,
            concurrency=concurrency,
            city_slug=slug,
        )
        result["city_slug"] = slug
        result["full_bbox"] = list(full_bbox)
        result["sample_window_deg"] = sample_window_deg
        results.append(result)
        # Stream the per-city result as soon as it lands so a long
        # sweep is observable, not just a wait + 5-row dump.
        print(
            f"[{slug}] warm p99 = {cast(dict[str, float], result['warm'])['p99_ms']:.1f} ms "
            f"| cold p99 = {cast(dict[str, float], result['cold'])['p99_ms']:.1f} ms",
            flush=True,
        )

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="multi-city-sweep")
    parser.add_argument("--base-url", default="http://localhost:7800")
    parser.add_argument("--layer", default="public.road_segments_tile_t")
    parser.add_argument("--zoom", type=int, default=14)
    parser.add_argument("--reference-day", default="2025-06-21")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--budget-warm-p99-ms", type=float, default=200.0)
    parser.add_argument("--budget-cold-p99-ms", type=float, default=800.0)
    parser.add_argument(
        "--sample-window-deg",
        type=float,
        default=0.06,
        help=(
            "Width of the per-city sampling window in degrees. "
            "Centered on each city's bbox center. Default 0.06 "
            "matches the Cambridge bbox span and gives ~25 tiles "
            "per city at z=14 for an apples-to-apples comparison."
        ),
    )
    parser.add_argument(
        "--segment-counts",
        nargs="+",
        default=[],
        help=(
            "Optional ``slug=count`` pairs to embed in the markdown "
            "summary (e.g. cambridge=36601). Surface-only — does not "
            "affect the latency measurement."
        ),
    )
    args = parser.parse_args(argv)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    seg_count_map: dict[str, int] = {}
    for pair in args.segment_counts:
        slug, _, count = pair.partition("=")
        if slug and count:
            seg_count_map[slug] = int(count)

    results = asyncio.run(
        run_sweep(
            base_url=args.base_url,
            zoom=args.zoom,
            reference_day=args.reference_day,
            concurrency=args.concurrency,
            layer=args.layer,
            sample_window_deg=args.sample_window_deg,
        )
    )

    for row in results:
        slug = cast(str, row["city_slug"])
        if slug in seg_count_map:
            row["segment_count"] = seg_count_map[slug]
        out_path = RESULTS_DIR / f"sweep-{slug}.json"
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(row, f, indent=2)

    md = render_markdown(
        results,
        warm_budget_ms=args.budget_warm_p99_ms,
        cold_budget_ms=args.budget_cold_p99_ms,
        zoom=args.zoom,
        concurrency=args.concurrency,
        sample_window_deg=args.sample_window_deg,
    )

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    md_path = OUT_DIR / f"{today}.md"
    with md_path.open("w", encoding="utf-8") as f:
        f.write(md)

    print(md)
    print(f"\nWrote {md_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
