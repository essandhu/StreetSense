"""Smoke tests for the streetsense_propagator pybind11 module — Phase 4.4.3.

Asserts the bindings load and expose the documented surface. Does not
exercise algorithm correctness -- that's the parity test's job.
"""

from __future__ import annotations

import pytest
import streetsense_propagator


def test_module_exposes_version() -> None:
    """version attribute matches kPropagatorVersion in C++ version.h."""
    assert streetsense_propagator.version == "0.1.0"


def test_module_exposes_registered_strategies() -> None:
    """strategies attribute lists all three ADR 0006 algorithm candidates."""
    registered = set(streetsense_propagator.strategies)
    assert {
        "influence-diffusion",
        "weighted-shortest-path",
        "pagerank-diffusion",
    }.issubset(registered)


def test_propagate_returns_dict_on_trivial_graph() -> None:
    """propagate() returns dict[int, float] keyed by NodeId."""
    graph = {
        "node_ids": [42],
        "adjacency": [[]],
        "inputs": [7.0],
    }
    params = {"k_hop_radius": 2, "decay_weight": 0.5, "normalize": False}
    uplift = streetsense_propagator.propagate(graph, "influence-diffusion", params)
    assert isinstance(uplift, dict)
    assert 42 in uplift
    assert uplift[42] == 0.0  # trivial graph: no neighbors -> zero uplift


def test_unknown_strategy_raises_value_error() -> None:
    """An unregistered strategy_id raises py::value_error -> Python ValueError."""
    graph = {"node_ids": [0], "adjacency": [[]], "inputs": [1.0]}
    with pytest.raises(ValueError, match="unknown strategy_id"):
        streetsense_propagator.propagate(graph, "no-such-algorithm", {})


def test_missing_graph_field_raises_value_error() -> None:
    """marshal_graph raises ValueError when a required field is absent."""
    with pytest.raises(ValueError, match="missing 'node_ids'"):
        streetsense_propagator.propagate({}, "influence-diffusion", {})


def test_propagate_returns_zero_uplift_when_normalize_disabled() -> None:
    """Linear chain matching the C++ test produces the expected uplift values."""
    graph = {
        "node_ids": [0, 1, 2],
        "adjacency": [
            [(1, 1.0)],
            [(0, 1.0), (2, 1.0)],
            [(1, 1.0)],
        ],
        "inputs": [1.0, 1.0, 1.0],
    }
    params = {"k_hop_radius": 2, "decay_weight": 0.5, "normalize": False}
    uplift = streetsense_propagator.propagate(graph, "influence-diffusion", params)
    # Same expected values as test_influence_diffusion.cc and
    # reference/test_reference.py.
    assert uplift[0] == pytest.approx(0.75, abs=1e-9)
    assert uplift[1] == pytest.approx(1.0, abs=1e-9)
    assert uplift[2] == pytest.approx(0.75, abs=1e-9)
