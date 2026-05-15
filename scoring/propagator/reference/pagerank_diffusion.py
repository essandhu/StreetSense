"""Pure-Python reference implementation of pagerank-diffusion.

Mirrors the C++ engine in ``scoring/propagator/src/pagerank_diffusion.cc``
byte-equivalent (modulo floating-point rounding within 1e-9) on the
same inputs. The parity property test in Phase 4.4 + ADR-finalization
calls both engines on hypothesis-generated random graphs and asserts
identical outputs.

The algorithm:

  Treat the normalized ``inputs`` as the teleportation distribution
  ``p``. Iterate the damped random-walk operator until the L1 distance
  between successive iterates falls below ``_TOLERANCE``, or
  ``_MAX_ITERATIONS`` is reached:

      pi[v] := (1 - d) * p[v]
             + d * sum_{u} pi[u] * (weight(u -> v) / out_weight(u))

  Dangling nodes (zero out-weight) redistribute their mass to the
  teleportation distribution on every step, matching standard
  PageRank.

Param reinterpretation:
  - decay_weight: damping factor (typical 0.85; our project default
    is 0.5).
  - k_hop_radius: unused (PageRank is global; locality is not a
    parameter). Preserved so the Params struct stays algorithm-
    agnostic — ADR 0006 §"Posture".
  - normalize: per-graph max-rescaling. Without it, output sums to 1
    (probability distribution).

This module is the correctness oracle, not a production codepath.

Refs:
  - ADR 0006 -- the algorithm-agnostic posture this lives behind
  - spec.md Technical Note 4 -- "parity, not performance"
"""

from __future__ import annotations

from .types import GraphData, InputValue, Params, UpliftMap, is_valid

_ALGORITHM_NAME = "pagerank-diffusion"
_ALGORITHM_VERSION = "0.1.0"

# Power-method termination thresholds. Fixed (not surfaced through
# Params) so the algorithm is reproducible -- changing these bumps the
# strategy's version() and retires prior runs' algorithm-version sentinel.
_MAX_ITERATIONS = 100
_TOLERANCE = 1e-12


def name() -> str:
    """Return the strategy name (matches the C++ engine)."""
    return _ALGORITHM_NAME


def version() -> str:
    """Return the strategy semver (matches the C++ engine)."""
    return _ALGORITHM_VERSION


def propagate(graph: GraphData, params: Params) -> UpliftMap:
    """Compute per-node uplift via damped random-walk power iteration.

    Raises:
        ValueError: if ``graph`` fails :func:`is_valid`.
    """
    if not is_valid(graph):
        msg = "graph fails invariant check; see types.is_valid()"
        raise ValueError(msg)

    n = len(graph.node_ids)

    uplift: UpliftMap = {nid: 0.0 for nid in graph.node_ids}

    input_sum = sum(graph.inputs)
    if input_sum == 0.0:
        return uplift

    teleport: list[float] = [graph.inputs[i] / input_sum for i in range(n)]

    # Start at the teleportation distribution -- a faithful power-method
    # warm-start that matches the C++ engine's initialization.
    pi: list[float] = list(teleport)

    out_weight_sum, edges = _summarize_adjacency(graph)
    damping = params.decay_weight
    one_minus_damping = 1.0 - damping

    for _ in range(_MAX_ITERATIONS):
        # Sum dangling-node mass for redistribution.
        dangling_mass = 0.0
        for u in range(n):
            if out_weight_sum[u] == 0.0:
                dangling_mass += pi[u]

        dangling_factor = damping * dangling_mass
        next_pi: list[float] = [
            one_minus_damping * teleport[v] + dangling_factor * teleport[v] for v in range(n)
        ]

        # Edge contributions.
        for u, v, weight in edges:
            out_sum = out_weight_sum[u]
            if out_sum == 0.0:
                continue
            next_pi[v] += damping * pi[u] * (weight / out_sum)

        delta = 0.0
        for v in range(n):
            delta += abs(next_pi[v] - pi[v])

        pi = next_pi
        if delta < _TOLERANCE:
            break

    for v in range(n):
        uplift[graph.node_ids[v]] = pi[v]

    if params.normalize:
        max_value = max(uplift.values(), default=0.0)
        if max_value > 0.0:
            for node_id in uplift:
                uplift[node_id] /= max_value

    return uplift


def _summarize_adjacency(
    graph: GraphData,
) -> tuple[list[float], list[tuple[int, int, InputValue]]]:
    """Pre-compute per-source out-weight totals and a flat edge list.

    Returned shape matches the C++ engine's AdjacencySummary helper so
    the two implementations sum edge contributions in identical order.
    """
    n = len(graph.node_ids)
    out_weight_sum: list[float] = [0.0] * n
    edges: list[tuple[int, int, InputValue]] = []
    for u in range(n):
        for edge in graph.adjacency[u]:
            out_weight_sum[u] += edge.weight
            edges.append((u, edge.target, edge.weight))
    return out_weight_sum, edges


__all__ = ["name", "propagate", "version"]
