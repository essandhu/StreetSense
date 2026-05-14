"""Pure-Python reference implementation of the influence-diffusion strategy.

Mirrors the C++ engine in ``scoring/propagator/src/influence_diffusion.cc``
byte-equivalently (modulo floating-point rounding within 1e-9) on the
same inputs. The parity property test in Phase 4.4 calls both engines
on hypothesis-generated random graphs and asserts identical outputs.

This module is the **correctness oracle**, not a production codepath.
It is intentionally simple and slow: a hand-rolled BFS on top of
``networkx`` instead of any of the algorithmic optimizations the C++
engine relies on. A release-build C++ engine that's not at least 10x
faster than this implementation on the 50k-edge benchmark graph
(Phase 4.8.5) is a smell.

Refs:
- ADR 0006 -- the algorithm-agnostic posture this lives behind
- spec.md Technical Note 4 -- "parity, not performance"
- conductor/tracks/phase-4-propagator/plan.md Phase 4.3
"""

from __future__ import annotations

from collections import deque

import networkx as nx

from .types import GraphData, Params, UpliftMap, is_valid

_ALGORITHM_NAME = "influence-diffusion"
_ALGORITHM_VERSION = "0.1.0"


def name() -> str:
    """Return the strategy name (matches the C++ engine)."""
    return _ALGORITHM_NAME


def version() -> str:
    """Return the strategy semver (matches the C++ engine)."""
    return _ALGORITHM_VERSION


def propagate(graph: GraphData, params: Params) -> UpliftMap:
    """Compute per-node uplift for ``graph`` under ``params``.

    Algorithm: for each source node, BFS outward up to k hops.
    Each source contributes ``decay_weight^distance * inputs[source]``
    to every reachable target (excluding self). Multiple sources
    contributing to the same target accumulate additively. Final
    uplifts are optionally normalized by the per-graph maximum.

    Raises:
        ValueError: if ``graph`` fails :func:`is_valid`.
    """
    if not is_valid(graph):
        msg = "graph fails invariant check; see types.is_valid()"
        raise ValueError(msg)

    g = _to_nx(graph)
    k_max = max(1, params.k_hop_radius)
    decay = params.decay_weight

    uplift: UpliftMap = {nid: 0.0 for nid in graph.node_ids}

    for source_idx in range(len(graph.node_ids)):
        distance = _bfs_k_bounded(g, source_idx, k_max)
        src_input = graph.inputs[source_idx]
        for target_idx, d in distance.items():
            if target_idx == source_idx or d <= 0 or d > k_max:
                continue
            uplift[graph.node_ids[target_idx]] += (decay**d) * src_input

    if params.normalize:
        max_value = max(uplift.values(), default=0.0)
        if max_value > 0.0:
            for node_id in uplift:
                uplift[node_id] /= max_value

    return uplift


def _to_nx(graph: GraphData) -> nx.DiGraph:
    """Convert GraphData (index-keyed adjacency) to a networkx DiGraph.

    Internal nodes are indices 0..n-1; the external NodeId mapping is
    applied at uplift assembly time so the algorithm operates on
    contiguous integer ids regardless of the caller's id assignment.
    """
    g: nx.DiGraph = nx.DiGraph()
    g.add_nodes_from(range(len(graph.node_ids)))
    for u, neighbors in enumerate(graph.adjacency):
        for edge in neighbors:
            g.add_edge(u, edge.target, weight=edge.weight)
    return g


def _bfs_k_bounded(g: nx.DiGraph, source: int, k_max: int) -> dict[int, int]:
    """k-bounded BFS from ``source`` over the directed graph ``g``.

    Returns a mapping {node_index -> shortest_hop_count}. The source
    itself is included with distance 0. Nodes beyond k_max hops are
    omitted.

    Hand-rolled rather than ``nx.single_source_shortest_path_length``
    so the parity surface with the C++ BFS is explicit -- both
    implementations use the same frontier-expansion semantics, so
    floating-point comparisons on the resulting uplifts stay tight.
    """
    distance: dict[int, int] = {source: 0}
    frontier: deque[int] = deque([source])
    while frontier:
        u = frontier.popleft()
        if distance[u] >= k_max:
            continue
        for v in g.successors(u):
            if v not in distance:
                distance[v] = distance[u] + 1
                frontier.append(v)
    return distance


__all__ = ["name", "propagate", "version"]
