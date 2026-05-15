"""Algorithm-selection correlation benchmark for ADR 0006 — Task 4.8.1.

For each of the three Phase-4 candidate strategies (influence-diffusion,
weighted-shortest-path, pagerank-diffusion), runs the propagator across
24 synthetic "hour-of-day" input vectors on a deterministic random
graph, then computes the Pearson correlation between each algorithm's
summed uplift and a planted topology-aware "incident density" ground
truth. Also reports mean per-call wall-clock so the selection rule
("highest correlation that meets the < 5 s budget at 500 k edges") can
be applied.

Output: ``benchmarks/propagator/algorithm_selection.json`` — the file
ADR 0006's Decision section is filled in from.

Synthetic-vs-Cambridge caveat
-----------------------------

The ADR's evaluation protocol calls for correlation against *real*
historical incident density on the seeded Cambridge graph. That
requires a live end-to-end seed + ingest pipeline (the 300 MB Geofabrik
extract + the MassDOT crash dataset), which is deferred to a separate
session in the current workstream. This benchmark therefore uses a
**synthetic graph + planted truth** as a stand-in:

- The graph is a deterministic random directed graph at a scale
  representative of a small city (default 5 k nodes, 25 k edges).
- The "incident density" is planted from quantities the three
  algorithms are *not* directly equivalent to (a mix of local input,
  1-hop neighborhood, and degree centrality + small Gaussian noise),
  so no algorithm trivially wins.
- Per-hour inputs are sparse (~10% of nodes active per hour) to
  mimic the glare-affected-segment density of a real run.

The methodology is documented in the JSON output so a future
contributor can re-run the same benchmark against real Cambridge data
when the live-stack run completes, and update the ADR's Decision
Evidence accordingly.

Usage:
    python -m benchmarks.propagator.algorithm_selection
    python -m benchmarks.propagator.algorithm_selection --nodes 5000 --edges 25000
    python -m benchmarks.propagator.algorithm_selection --json benchmarks/propagator/algorithm_selection.json

Refs:
  - docs/adr/0006-propagation-algorithm.md §"In-Track Evaluation Protocol"
  - conductor/tracks/phase-4-propagator/plan.md Task 4.8.1
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import streetsense_propagator

DEFAULT_SEED = 42
DEFAULT_NODES = 5_000
DEFAULT_EDGES = 25_000
HOURS_PER_DAY = 24
DEFAULT_ACTIVE_FRACTION = 0.10  # ~10% of nodes have non-zero input per hour
WALL_CLOCK_BUDGET_SECONDS = 5.0  # ADR 0006 §"Selection rule"
CORRELATION_FLOOR = 0.10  # ADR 0006 §"Sanity floor"


@dataclass(frozen=True, slots=True)
class _AlgoParams:
    """Per-algorithm Params struct (the algorithm-agnostic Params shared
    by every strategy, with values tuned per the ADR's default-parameter
    discussion).
    """

    k_hop_radius: int
    decay_weight: float
    normalize: bool


# Default Params per algorithm. The ADR's "Parameter Defaults" section
# is filled in from this table after the winner is chosen.
_DEFAULT_PARAMS: dict[str, _AlgoParams] = {
    "influence-diffusion": _AlgoParams(k_hop_radius=2, decay_weight=0.5, normalize=False),
    "weighted-shortest-path": _AlgoParams(k_hop_radius=2, decay_weight=0.5, normalize=False),
    "pagerank-diffusion": _AlgoParams(
        k_hop_radius=2,  # ignored by pagerank
        decay_weight=0.85,
        normalize=False,
    ),
}


@dataclass(frozen=True, slots=True)
class _AlgorithmResult:
    """One algorithm's outcome on the synthetic benchmark."""

    name: str
    correlation: float
    mean_per_call_wall_seconds: float
    p99_per_call_wall_seconds: float
    total_wall_seconds: float
    params: dict[str, Any]
    meets_wall_clock_budget: bool
    meets_correlation_floor: bool


@dataclass
class _BenchmarkResult:
    nodes: int
    edges: int
    seed: int
    hours: int
    active_fraction: float
    wall_clock_budget_seconds: float
    correlation_floor: float
    is_synthetic: bool
    synthetic_methodology: str
    algorithms: list[_AlgorithmResult] = field(default_factory=list)
    winner: str | None = None
    winner_rationale: str | None = None


def _build_random_graph(
    *, nodes: int, edges: int, seed: int
) -> tuple[list[int], list[list[tuple[int, float]]]]:
    """Build a deterministic random directed graph.

    Returns:
        (node_ids, adjacency) where adjacency[u] is a list of
        (target_index, edge_weight). Self-loops are filtered out.
    """
    rng = random.Random(seed)
    node_ids = list(range(nodes))
    adjacency: list[list[tuple[int, float]]] = [[] for _ in range(nodes)]
    for _ in range(edges):
        u = rng.randrange(nodes)
        v = rng.randrange(nodes)
        if u == v:
            continue  # self-loops contribute no signal
        weight = rng.uniform(0.1, 2.0)
        adjacency[u].append((v, weight))
    return node_ids, adjacency


def _generate_sparse_inputs(
    *, nodes: int, hours: int, active_fraction: float, seed: int
) -> list[list[float]]:
    """Generate per-hour input vectors with realistic sparsity.

    Returns inputs[hour][node] = score in [0, 1]. ~`active_fraction`
    of nodes have non-zero input per hour, mimicking the
    glare-affected-segment density of a real Cambridge run (only
    east-west corridors at certain hours of day).
    """
    rng = random.Random(seed + 1)
    inputs: list[list[float]] = []
    active_n = max(1, int(nodes * active_fraction))
    for _ in range(hours):
        vec = [0.0] * nodes
        active = rng.sample(range(nodes), active_n)
        for node_idx in active:
            vec[node_idx] = rng.uniform(0.1, 1.0)
        inputs.append(vec)
    return inputs


def _compute_planted_ground_truth(
    *,
    node_ids: list[int],
    adjacency: list[list[tuple[int, float]]],
    hourly_inputs: list[list[float]],
    seed: int,
) -> dict[int, float]:
    """Plant a topology-aware "incident density" the algorithms must approximate.

    The truth is a weighted mix of:
      - Local input sum across hours (50%) — what a non-network-aware
        baseline would already see.
      - 1-hop neighborhood mean input (30%) — short-range network
        amplification.
      - In-degree + out-degree centrality (20%) — corridor
        concentration; high-degree nodes are stand-ins for the
        intersection-heavy segments that correlate with incidents in
        real data.
      - Gaussian noise (stddev = 5% of signal) — to defeat any single
        algorithm trivially winning by exactly reproducing the truth.

    The mix is intentionally not the output of any of the three
    candidate algorithms; each algorithm captures *part* of the
    signal, and the benchmark reveals which one captures the most.
    """
    rng = random.Random(seed + 2)
    n = len(node_ids)

    # Local input sum across hours.
    local_sum = [0.0] * n
    for hour_vec in hourly_inputs:
        for i in range(n):
            local_sum[i] += hour_vec[i]

    # 1-hop neighborhood mean of summed inputs (forward-adjacent).
    neighborhood_mean = [0.0] * n
    for u in range(n):
        if not adjacency[u]:
            neighborhood_mean[u] = 0.0
            continue
        total = sum(local_sum[v] for v, _ in adjacency[u])
        neighborhood_mean[u] = total / len(adjacency[u])

    # Degree centrality (out + in).
    in_degree = [0] * n
    for u in range(n):
        for v, _ in adjacency[u]:
            in_degree[v] += 1
    out_degree = [len(adjacency[u]) for u in range(n)]
    raw_degree = [out_degree[i] + in_degree[i] for i in range(n)]
    # Normalize degree to similar magnitude as local_sum.
    max_degree = max(raw_degree) if raw_degree else 1
    degree_signal = [
        (raw_degree[i] / max_degree) * max(local_sum) if max_degree else 0.0 for i in range(n)
    ]

    truth = {}
    for i in range(n):
        signal = 0.5 * local_sum[i] + 0.3 * neighborhood_mean[i] + 0.2 * degree_signal[i]
        noise = rng.gauss(0.0, max(1e-9, 0.05 * abs(signal)))
        truth[node_ids[i]] = signal + noise
    return truth


def _pearson_correlation(xs: list[float], ys: list[float]) -> float:
    """Pearson correlation coefficient between two equal-length series."""
    if len(xs) != len(ys) or len(xs) < 2:
        return float("nan")
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sum_xy = 0.0
    sum_x2 = 0.0
    sum_y2 = 0.0
    for x, y in zip(xs, ys, strict=True):
        dx = x - mean_x
        dy = y - mean_y
        sum_xy += dx * dy
        sum_x2 += dx * dx
        sum_y2 += dy * dy
    denom = math.sqrt(sum_x2 * sum_y2)
    if denom == 0.0:
        return 0.0
    return sum_xy / denom


def _to_binding_graph(
    node_ids: list[int], adjacency: list[list[tuple[int, float]]]
) -> dict[str, Any]:
    return {
        "node_ids": node_ids,
        "adjacency": [list(neighbors) for neighbors in adjacency],
        "inputs": [0.0] * len(node_ids),  # placeholder; overridden per call
    }


def _run_algorithm(
    *,
    strategy_id: str,
    params: _AlgoParams,
    binding_graph: dict[str, Any],
    hourly_inputs: list[list[float]],
    truth: dict[int, float],
    node_ids: list[int],
) -> _AlgorithmResult:
    """Run one algorithm across all 24 hours, sum uplift, correlate against truth."""
    binding_params: dict[str, Any] = {
        "k_hop_radius": params.k_hop_radius,
        "decay_weight": params.decay_weight,
        "normalize": params.normalize,
    }

    per_call_wall_seconds: list[float] = []
    summed_uplift: dict[int, float] = {nid: 0.0 for nid in node_ids}

    t_total_start = time.perf_counter()
    for hour_vec in hourly_inputs:
        # Update inputs for this hour (graph topology stays the same).
        graph_for_hour = dict(binding_graph)
        graph_for_hour["inputs"] = hour_vec
        t_start = time.perf_counter()
        hour_uplift = streetsense_propagator.propagate(graph_for_hour, strategy_id, binding_params)
        per_call_wall_seconds.append(time.perf_counter() - t_start)
        for node_id, value in hour_uplift.items():
            summed_uplift[node_id] += value
    total_wall_seconds = time.perf_counter() - t_total_start

    # Align the two series for Pearson correlation: enumerate node_ids
    # so both vectors are in the same order. Avoids a stochastic order
    # quirk when iterating an unordered_map-backed dict.
    xs = [summed_uplift[nid] for nid in node_ids]
    ys = [truth[nid] for nid in node_ids]
    correlation = _pearson_correlation(xs, ys)

    sorted_calls = sorted(per_call_wall_seconds)
    p99_idx = max(0, math.ceil(0.99 * len(sorted_calls)) - 1)
    p99 = sorted_calls[p99_idx]

    return _AlgorithmResult(
        name=strategy_id,
        correlation=correlation,
        mean_per_call_wall_seconds=statistics.mean(per_call_wall_seconds),
        p99_per_call_wall_seconds=p99,
        total_wall_seconds=total_wall_seconds,
        params=asdict(params),
        meets_wall_clock_budget=p99 < WALL_CLOCK_BUDGET_SECONDS,
        meets_correlation_floor=correlation >= CORRELATION_FLOOR,
    )


def _pick_winner(results: list[_AlgorithmResult]) -> tuple[str | None, str]:
    """Apply ADR 0006 §"Selection rule" to pick the winner.

    Returns (winner_name, rationale). Winner is the algorithm with the
    highest correlation that meets the < 5 s wall-clock budget. If two
    are within 0.02 absolute correlation, the cheaper one wins. If no
    algorithm meets the correlation floor, returns (None, ...) and the
    ADR reopens.
    """
    eligible = [r for r in results if r.meets_wall_clock_budget and r.meets_correlation_floor]
    if not eligible:
        return None, (
            "No algorithm meets both the wall-clock budget and the correlation "
            "floor; ADR 0006 §'Sanity floor' triggers — reopen the ADR."
        )

    # Sort by correlation (descending). If top two are within 0.02,
    # prefer the one with lower mean wall-clock.
    eligible.sort(key=lambda r: r.correlation, reverse=True)
    top = eligible[0]
    if len(eligible) >= 2:
        runner_up = eligible[1]
        if (
            abs(top.correlation - runner_up.correlation) <= 0.02
            and runner_up.mean_per_call_wall_seconds < top.mean_per_call_wall_seconds
        ):
            return runner_up.name, (
                f"Top two algorithms ({top.name}, {runner_up.name}) are within "
                f"0.02 correlation; ADR 0006 §'Selection rule' picks the cheaper "
                f"one ({runner_up.name}: {runner_up.mean_per_call_wall_seconds:.4f}s/call "
                f"vs {top.mean_per_call_wall_seconds:.4f}s/call)."
            )

    return top.name, (
        f"{top.name} has the highest correlation ({top.correlation:.4f}) of all "
        f"eligible algorithms; meets the < {WALL_CLOCK_BUDGET_SECONDS} s wall-clock "
        f"budget (p99 = {top.p99_per_call_wall_seconds:.4f}s) and the "
        f">{CORRELATION_FLOOR} correlation floor."
    )


def run(
    *,
    nodes: int = DEFAULT_NODES,
    edges: int = DEFAULT_EDGES,
    seed: int = DEFAULT_SEED,
    active_fraction: float = DEFAULT_ACTIVE_FRACTION,
) -> _BenchmarkResult:
    """Run the benchmark and return a structured result."""
    node_ids, adjacency = _build_random_graph(nodes=nodes, edges=edges, seed=seed)
    hourly_inputs = _generate_sparse_inputs(
        nodes=nodes,
        hours=HOURS_PER_DAY,
        active_fraction=active_fraction,
        seed=seed,
    )
    truth = _compute_planted_ground_truth(
        node_ids=node_ids,
        adjacency=adjacency,
        hourly_inputs=hourly_inputs,
        seed=seed,
    )
    binding_graph = _to_binding_graph(node_ids, adjacency)

    result = _BenchmarkResult(
        nodes=nodes,
        edges=edges,
        seed=seed,
        hours=HOURS_PER_DAY,
        active_fraction=active_fraction,
        wall_clock_budget_seconds=WALL_CLOCK_BUDGET_SECONDS,
        correlation_floor=CORRELATION_FLOOR,
        is_synthetic=True,
        synthetic_methodology=(
            "Random directed graph (deterministic seed). Per-hour inputs are "
            "sparse (~10% active nodes). Planted incident density is a "
            "weighted mix of local input sum (50%), 1-hop neighborhood mean "
            "(30%), and degree centrality (20%) + small Gaussian noise. This "
            "stands in for the live Cambridge run + MassDOT incident "
            "correlation, which is deferred to a separate workstream."
        ),
    )

    for strategy_id, params in _DEFAULT_PARAMS.items():
        algo_result = _run_algorithm(
            strategy_id=strategy_id,
            params=params,
            binding_graph=binding_graph,
            hourly_inputs=hourly_inputs,
            truth=truth,
            node_ids=node_ids,
        )
        result.algorithms.append(algo_result)

    winner, rationale = _pick_winner(result.algorithms)
    result.winner = winner
    result.winner_rationale = rationale
    return result


def _result_to_dict(result: _BenchmarkResult) -> dict[str, Any]:
    return {
        "nodes": result.nodes,
        "edges": result.edges,
        "seed": result.seed,
        "hours": result.hours,
        "active_fraction": result.active_fraction,
        "wall_clock_budget_seconds": result.wall_clock_budget_seconds,
        "correlation_floor": result.correlation_floor,
        "is_synthetic": result.is_synthetic,
        "synthetic_methodology": result.synthetic_methodology,
        "algorithms": [asdict(a) for a in result.algorithms],
        "winner": result.winner,
        "winner_rationale": result.winner_rationale,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", type=int, default=DEFAULT_NODES)
    parser.add_argument("--edges", type=int, default=DEFAULT_EDGES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--active-fraction", type=float, default=DEFAULT_ACTIVE_FRACTION)
    parser.add_argument(
        "--json",
        type=Path,
        default=Path("benchmarks/propagator/algorithm_selection.json"),
        help="Path to write the result JSON.",
    )
    args = parser.parse_args(argv)

    result = run(
        nodes=args.nodes,
        edges=args.edges,
        seed=args.seed,
        active_fraction=args.active_fraction,
    )

    payload = _result_to_dict(result)
    print(json.dumps(payload, indent=2))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if result.winner is None:
        print(
            "FAIL: no algorithm meets both the wall-clock budget and correlation floor.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
