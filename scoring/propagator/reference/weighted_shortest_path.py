"""Pure-Python reference implementation of weighted-shortest-path.

Mirrors the C++ engine in ``scoring/propagator/src/weighted_shortest_path.cc``
byte-equivalent (modulo floating-point rounding within 1e-9) on the
same inputs. The parity property test in Phase 4.4 + ADR-finalization
calls both engines on hypothesis-generated random graphs and asserts
identical outputs.

The algorithm:

  For each source node `s` with `inputs[s] != 0.0`:
    1. Run single-source Dijkstra (edge weights as distance) to get
       the unique shortest distance `d(s, t)` to every reachable
       target `t`.
    2. For each target with `d(s, t) <= max_distance`:
         uplift[t] += inputs[s] * exp(-alpha * d(s, t))

  Sources with input == 0.0 are skipped — no contribution.

Param reinterpretation (Params is shared across strategies; ADR 0006
§"Posture"):
  - decay_weight: alpha for the exponential decay curve.
  - k_hop_radius: max-distance cutoff in edge-weight units (the int
    field is cast to double; "radius" generalizes from hops to weight
    units across the registry).
  - normalize: per-graph max-rescaling.

This module is the **correctness oracle**, not a production codepath.
The hand-rolled heapq Dijkstra is intentionally simple; the C++ engine
uses Boost's well-optimized `dijkstra_shortest_paths` and is expected
to be at least 10x faster on the 50k-edge bench graph (Phase 4.8.5).

Refs:
  - ADR 0006 -- the algorithm-agnostic posture this lives behind
  - spec.md Technical Note 4 -- "parity, not performance"
"""

from __future__ import annotations

import heapq
import math

from .types import GraphData, Params, UpliftMap, is_valid

_ALGORITHM_NAME = "weighted-shortest-path"
_ALGORITHM_VERSION = "0.1.0"


def name() -> str:
    """Return the strategy name (matches the C++ engine)."""
    return _ALGORITHM_NAME


def version() -> str:
    """Return the strategy semver (matches the C++ engine)."""
    return _ALGORITHM_VERSION


def propagate(graph: GraphData, params: Params) -> UpliftMap:
    """Compute per-node uplift via Dijkstra-driven exponential decay.

    Raises:
        ValueError: if ``graph`` fails :func:`is_valid`.
    """
    if not is_valid(graph):
        msg = "graph fails invariant check; see types.is_valid()"
        raise ValueError(msg)

    n = len(graph.node_ids)
    max_distance = max(0.0, float(params.k_hop_radius))
    alpha = params.decay_weight

    uplift: UpliftMap = {nid: 0.0 for nid in graph.node_ids}

    for source in range(n):
        src_input = graph.inputs[source]
        if src_input == 0.0:
            continue
        distance = _dijkstra(graph, source)
        for target, d in distance.items():
            if target == source:
                continue
            if not math.isfinite(d) or d > max_distance:
                continue
            uplift[graph.node_ids[target]] += src_input * math.exp(-alpha * d)

    if params.normalize:
        max_value = max(uplift.values(), default=0.0)
        if max_value > 0.0:
            for node_id in uplift:
                uplift[node_id] /= max_value

    return uplift


def _dijkstra(graph: GraphData, source: int) -> dict[int, float]:
    """Single-source Dijkstra over GraphData's edge weights.

    Returns ``{target_index -> shortest distance}``; unreachable
    vertices are simply absent. The source itself is included with
    distance 0.0.

    Hand-rolled with ``heapq`` rather than ``networkx.dijkstra_path_length``
    so the parity surface with Boost's implementation is explicit --
    both implementations relax the same edges in the same order modulo
    tie-breaking, and the *distance* (which is mathematically unique
    given non-negative weights) is what feeds the uplift sum.
    """
    distance: dict[int, float] = {source: 0.0}
    heap: list[tuple[float, int]] = [(0.0, source)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > distance[u]:
            continue
        for edge in graph.adjacency[u]:
            new_d = d + edge.weight
            best = distance.get(edge.target)
            if best is None or new_d < best:
                distance[edge.target] = new_d
                heapq.heappush(heap, (new_d, edge.target))
    return distance


__all__ = ["name", "propagate", "version"]
