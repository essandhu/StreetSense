"""Python-vs-C++ speedup sanity gate — Task 4.8.5.

Runs the same propagation call on a 50k-edge seeded graph through:

  - The pure-Python reference implementation (``scoring.propagator.reference``).
  - The C++ engine via the pybind11 bindings (``streetsense_propagator``).

Asserts the C++ engine is **at least 10x** faster than the reference.
A release-build C++ engine that's not is a smell: either the binary
is built in debug mode, the marshalling layer is doing more work
than expected, or the reference implementation is unrealistically
optimized.

Usage:
    python -m benchmarks.propagator.python_vs_cpp
    python -m benchmarks.propagator.python_vs_cpp --json results.json

Refs:
  - conductor/tracks/phase-4-propagator/plan.md Task 4.8.5
  - scoring/propagator/reference/influence_diffusion.py
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import streetsense_propagator

from scoring.propagator.reference.influence_diffusion import (
    propagate as reference_propagate,
)
from scoring.propagator.reference.types import Edge, GraphData, Params

SEED = 42
DEFAULT_NODES = 10_000
DEFAULT_EDGES = 50_000
SPEEDUP_GATE = 10.0


@dataclass(frozen=True, slots=True)
class _Result:
    nodes: int
    edges: int
    python_seconds: float
    cpp_seconds: float
    speedup: float
    gate: float
    passed: bool


def build_random_graph(*, nodes: int, edges: int, seed: int = SEED) -> GraphData:
    """Build a deterministic random GraphData with `nodes` x `edges`."""
    rng = random.Random(seed)
    node_ids = tuple(range(nodes))
    inputs = tuple(rng.random() for _ in range(nodes))
    adjacency_lists: list[list[Edge]] = [[] for _ in range(nodes)]
    for _ in range(edges):
        u = rng.randrange(nodes)
        v = rng.randrange(nodes)
        adjacency_lists[u].append(Edge(target=v, weight=rng.random()))
    adjacency = tuple(tuple(edges_list) for edges_list in adjacency_lists)
    return GraphData(node_ids=node_ids, adjacency=adjacency, inputs=inputs)


def to_binding_graph(graph: GraphData) -> dict[str, object]:
    """Convert the reference-impl GraphData into the bindings dict shape."""
    return {
        "node_ids": list(graph.node_ids),
        "adjacency": [
            [(edge.target, edge.weight) for edge in neighbors] for neighbors in graph.adjacency
        ],
        "inputs": list(graph.inputs),
    }


def _time_seconds(callable_: object, *args: object, **kwargs: object) -> tuple[object, float]:
    start = time.perf_counter()
    result = callable_(*args, **kwargs)  # type: ignore[operator]
    return result, time.perf_counter() - start


def run(nodes: int, edges: int) -> _Result:
    graph = build_random_graph(nodes=nodes, edges=edges)
    binding_graph = to_binding_graph(graph)
    params = Params(k_hop_radius=2, decay_weight=0.5, normalize=False)
    binding_params: dict[str, object] = {
        "k_hop_radius": params.k_hop_radius,
        "decay_weight": params.decay_weight,
        "normalize": params.normalize,
    }

    # Python reference — single pass; the algorithm is O(N * K * <avg out>),
    # which for N=10k, K=2, avg-out=5 is about 100k inner iterations; sub-1s.
    _, python_seconds = _time_seconds(reference_propagate, graph, params)
    # C++ engine — should be 10x+ faster.
    _, cpp_seconds = _time_seconds(
        streetsense_propagator.propagate,
        binding_graph,
        "influence-diffusion",
        binding_params,
    )

    speedup = python_seconds / max(cpp_seconds, 1e-9)
    return _Result(
        nodes=nodes,
        edges=edges,
        python_seconds=python_seconds,
        cpp_seconds=cpp_seconds,
        speedup=speedup,
        gate=SPEEDUP_GATE,
        passed=speedup >= SPEEDUP_GATE,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", type=int, default=DEFAULT_NODES)
    parser.add_argument("--edges", type=int, default=DEFAULT_EDGES)
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="Optional path to write the result JSON.",
    )
    args = parser.parse_args(argv)

    result = run(args.nodes, args.edges)
    payload = asdict(result)
    print(json.dumps(payload, indent=2))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if not result.passed:
        print(
            f"FAIL: speedup {result.speedup:.2f}x is below the {SPEEDUP_GATE:.1f}x gate.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
