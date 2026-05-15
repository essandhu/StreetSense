"""C++/Python parity property test — Phase 4.4.4.

The headline correctness gate for the entire propagator stack:
hypothesis-generated random graphs are fed to BOTH the C++ engine
(via streetsense_propagator) and the pure-Python reference
implementation. Per-node uplift values must agree to within 1e-9
absolute tolerance.

This test closes the AC-1 + AC-2 + AC-3 loop from the spec:
- AC-1: the C++ engine exists and runs.
- AC-2: the Python reference exists and runs.
- AC-3: the bindings marshal types without losing fidelity.

Failure modes this catches:
- Algorithm divergence (one engine treats self-loops differently).
- Floating-point rounding drift large enough to suggest an algorithmic
  bug rather than per-operation noise.
- Bindings marshalling bugs (e.g., truncating NodeId from u64 to i32).
"""

from __future__ import annotations

import math

import streetsense_propagator
from hypothesis import HealthCheck, given, settings

from scoring.propagator.reference.influence_diffusion import propagate as reference_propagate
from scoring.propagator.reference.pagerank_diffusion import (
    propagate as pagerank_reference_propagate,
)
from scoring.propagator.reference.strategies import graph_data
from scoring.propagator.reference.types import GraphData, Params
from scoring.propagator.reference.weighted_shortest_path import (
    propagate as wsp_reference_propagate,
)

# influence-diffusion's BFS produces integer-distance arithmetic so 1e-9 is
# safe. weighted-shortest-path sums floating-point edge weights along Dijkstra
# paths, where two Dijkstra implementations can take ULP-different routes
# even when the *minimum* distance is mathematically unique; a 1e-7 tolerance
# absorbs that without inviting algorithmic divergence. pagerank-diffusion's
# power-method converges to within 1e-12 internally, so 1e-9 is fine after the
# normalize-by-max rescale.
_TOLERANCE = 1e-9
_WSP_TOLERANCE = 1e-7
_PARAMS = Params(k_hop_radius=2, decay_weight=0.5, normalize=False)
_PAGERANK_PARAMS = Params(k_hop_radius=2, decay_weight=0.85, normalize=False)


def _to_binding_graph(graph: GraphData) -> dict[str, object]:
    """Convert reference GraphData (dataclass) into the dict shape the bindings expect."""
    return {
        "node_ids": list(graph.node_ids),
        "adjacency": [[(e.target, e.weight) for e in neighbors] for neighbors in graph.adjacency],
        "inputs": list(graph.inputs),
    }


def _to_binding_params(params: Params) -> dict[str, object]:
    return {
        "k_hop_radius": params.k_hop_radius,
        "decay_weight": params.decay_weight,
        "normalize": params.normalize,
    }


@given(graph=graph_data(max_nodes=30, max_edges=150))
@settings(
    max_examples=100,
    deadline=30000,
    suppress_health_check=(HealthCheck.too_slow, HealthCheck.data_too_large),
)
def test_cpp_and_python_engines_agree_on_random_graphs(graph: GraphData) -> None:
    """Per-node uplift from the C++ engine equals the Python reference within 1e-9."""
    binding_graph = _to_binding_graph(graph)
    binding_params = _to_binding_params(_PARAMS)

    cpp_uplift = streetsense_propagator.propagate(
        binding_graph, "influence-diffusion", binding_params
    )
    python_uplift = reference_propagate(graph, _PARAMS)

    # Same node-id keyset.
    assert set(cpp_uplift) == set(python_uplift), (
        f"key set differs: C++={sorted(cpp_uplift)} vs Python={sorted(python_uplift)}"
    )

    # Per-node value parity.
    for node_id in cpp_uplift:
        cpp_value = cpp_uplift[node_id]
        py_value = python_uplift[node_id]
        assert math.isclose(cpp_value, py_value, abs_tol=_TOLERANCE), (
            f"uplift mismatch on node {node_id}: C++={cpp_value!r} Python={py_value!r}"
        )


@given(graph=graph_data(max_nodes=30, max_edges=150))
@settings(
    max_examples=100,
    deadline=30000,
    suppress_health_check=(HealthCheck.too_slow, HealthCheck.data_too_large),
)
def test_weighted_shortest_path_cpp_python_parity(graph: GraphData) -> None:
    """C++ Dijkstra-driven decay matches the Python heapq reference within 1e-7."""
    binding_graph = _to_binding_graph(graph)
    binding_params = _to_binding_params(_PARAMS)

    cpp_uplift = streetsense_propagator.propagate(
        binding_graph, "weighted-shortest-path", binding_params
    )
    python_uplift = wsp_reference_propagate(graph, _PARAMS)

    assert set(cpp_uplift) == set(python_uplift), (
        f"key set differs: C++={sorted(cpp_uplift)} vs Python={sorted(python_uplift)}"
    )

    for node_id in cpp_uplift:
        cpp_value = cpp_uplift[node_id]
        py_value = python_uplift[node_id]
        assert math.isclose(cpp_value, py_value, abs_tol=_WSP_TOLERANCE), (
            f"weighted-shortest-path mismatch on node {node_id}: "
            f"C++={cpp_value!r} Python={py_value!r}"
        )


@given(graph=graph_data(max_nodes=30, max_edges=150))
@settings(
    max_examples=50,
    deadline=30000,
    suppress_health_check=(HealthCheck.too_slow, HealthCheck.data_too_large),
)
def test_pagerank_diffusion_cpp_python_parity(graph: GraphData) -> None:
    """C++ power-method matches the Python reference within 1e-9."""
    binding_graph = _to_binding_graph(graph)
    binding_params = _to_binding_params(_PAGERANK_PARAMS)

    cpp_uplift = streetsense_propagator.propagate(
        binding_graph, "pagerank-diffusion", binding_params
    )
    python_uplift = pagerank_reference_propagate(graph, _PAGERANK_PARAMS)

    assert set(cpp_uplift) == set(python_uplift), (
        f"key set differs: C++={sorted(cpp_uplift)} vs Python={sorted(python_uplift)}"
    )

    for node_id in cpp_uplift:
        cpp_value = cpp_uplift[node_id]
        py_value = python_uplift[node_id]
        assert math.isclose(cpp_value, py_value, abs_tol=_TOLERANCE), (
            f"pagerank-diffusion mismatch on node {node_id}: C++={cpp_value!r} Python={py_value!r}"
        )
