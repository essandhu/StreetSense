"""Property tests for the reference influence-diffusion implementation.

Asserts algorithm-level invariants on randomly generated graphs (via
the hypothesis strategy in :mod:`strategies`). The contracts pinned
here also hold for the C++ engine -- the parity property test in
Phase 4.4 cross-checks both engines on identical inputs.

Properties:
- output is finite (no NaN, no inf) for any valid input graph.
- output is non-negative when all inputs are non-negative.
- output is permutation-invariant under edge reordering: swapping
  two same-source-same-weight edges does not change any node's
  uplift (algorithm depends on topology, not on edge ordering
  within an adjacency list).
"""

from __future__ import annotations

import math
import random

from hypothesis import HealthCheck, given, settings

from scoring.propagator.reference.influence_diffusion import propagate
from scoring.propagator.reference.strategies import graph_data
from scoring.propagator.reference.types import Edge, GraphData, Params

# Bounded sizes keep each hypothesis run under ~30s wall-clock.
_PROPERTY_SIZES: dict[str, int] = {"max_nodes": 30, "max_edges": 100}
_PARAMS = Params(k_hop_radius=2, decay_weight=0.5, normalize=False)


@given(graph=graph_data(**_PROPERTY_SIZES))
@settings(
    max_examples=100,
    deadline=10000,
    suppress_health_check=(HealthCheck.too_slow, HealthCheck.data_too_large),
)
def test_uplift_is_finite_for_any_valid_graph(graph: GraphData) -> None:
    uplift = propagate(graph, _PARAMS)
    assert len(uplift) == len(graph.node_ids)
    for value in uplift.values():
        assert math.isfinite(value), f"non-finite uplift: {value!r}"


@given(graph=graph_data(**_PROPERTY_SIZES))
@settings(
    max_examples=100,
    deadline=10000,
    suppress_health_check=(HealthCheck.too_slow, HealthCheck.data_too_large),
)
def test_uplift_is_nonnegative_when_inputs_are_nonnegative(graph: GraphData) -> None:
    # The strategy already constrains inputs to [0, max_input].
    uplift = propagate(graph, _PARAMS)
    for node_id, value in uplift.items():
        assert value >= 0.0, f"negative uplift on node {node_id}: {value!r}"


@given(graph=graph_data(**_PROPERTY_SIZES))
@settings(
    max_examples=50,
    deadline=15000,
    suppress_health_check=(HealthCheck.too_slow, HealthCheck.data_too_large),
)
def test_uplift_is_invariant_under_edge_reordering(graph: GraphData) -> None:
    """Shuffling the adjacency list of each node must not change uplifts.

    The algorithm depends on topology (which (u,v) pairs exist with
    which weights) and not on the order in which edges happen to be
    listed within a node's adjacency vector.
    """
    rng = random.Random(0xC0FFEE)
    shuffled_adjacency: list[tuple[Edge, ...]] = []
    for neighbors in graph.adjacency:
        as_list = list(neighbors)
        rng.shuffle(as_list)
        shuffled_adjacency.append(tuple(as_list))
    shuffled = GraphData(
        node_ids=graph.node_ids,
        adjacency=tuple(shuffled_adjacency),
        inputs=graph.inputs,
    )

    uplift_a = propagate(graph, _PARAMS)
    uplift_b = propagate(shuffled, _PARAMS)
    for node_id in graph.node_ids:
        assert math.isclose(uplift_a[node_id], uplift_b[node_id], abs_tol=1e-12), (
            f"edge-order changed uplift[{node_id}]: {uplift_a[node_id]!r} != {uplift_b[node_id]!r}"
        )
