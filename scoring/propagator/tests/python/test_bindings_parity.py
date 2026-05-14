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
from scoring.propagator.reference.strategies import graph_data
from scoring.propagator.reference.types import GraphData, Params

_TOLERANCE = 1e-9
_PARAMS = Params(k_hop_radius=2, decay_weight=0.5, normalize=False)


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
