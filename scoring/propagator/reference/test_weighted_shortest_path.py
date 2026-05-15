"""Unit tests for the weighted-shortest-path reference implementation.

Mirrors the canonical scenarios from
``tests/test_weighted_shortest_path.cc`` on the *same fixed inputs* so
the C++ and Python engines have a shared fixed-point. The parity
property test (Phase 4.4 + ADR finalization) generalizes this with
hypothesis-generated graphs.
"""

from __future__ import annotations

import math

import pytest

from scoring.propagator.reference.types import Edge, GraphData, Params
from scoring.propagator.reference.weighted_shortest_path import (
    name,
    propagate,
    version,
)

_TOLERANCE = 1e-9


def _unnormalized(max_d: int, alpha: float) -> Params:
    return Params(k_hop_radius=max_d, decay_weight=alpha, normalize=False)


def test_name_and_version() -> None:
    """The reference engine reports the same name and semver as the C++ engine."""
    assert name() == "weighted-shortest-path"
    assert version() == "0.1.0"


def test_trivial_one_node_graph_has_zero_uplift() -> None:
    graph = GraphData(node_ids=(42,), adjacency=((),), inputs=(7.0,))
    uplift = propagate(graph, _unnormalized(max_d=2, alpha=0.5))
    assert math.isclose(uplift[42], 0.0, abs_tol=_TOLERANCE)


def test_linear_chain_unit_weights() -> None:
    """3-node directed chain 0->1->2 with unit weights, alpha=1.0, max_d=2.

    Expected (matches test_weighted_shortest_path.cc):
      uplift[0] = 0
      uplift[1] = exp(-1)
      uplift[2] = exp(-2) + exp(-1)
    """
    graph = GraphData(
        node_ids=(0, 1, 2),
        adjacency=(
            (Edge(target=1, weight=1.0),),
            (Edge(target=2, weight=1.0),),
            (),
        ),
        inputs=(1.0, 1.0, 1.0),
    )
    uplift = propagate(graph, _unnormalized(max_d=2, alpha=1.0))
    assert math.isclose(uplift[0], 0.0, abs_tol=_TOLERANCE)
    assert math.isclose(uplift[1], math.exp(-1.0), abs_tol=_TOLERANCE)
    assert math.isclose(uplift[2], math.exp(-2.0) + math.exp(-1.0), abs_tol=_TOLERANCE)


def test_distance_cutoff_excludes_farther_targets() -> None:
    """A target beyond max_distance receives zero uplift from that source."""
    graph = GraphData(
        node_ids=(0, 1, 2),
        adjacency=(
            (Edge(target=1, weight=1.5),),
            (Edge(target=2, weight=1.5),),
            (),
        ),
        inputs=(1.0, 0.0, 0.0),
    )
    uplift = propagate(graph, _unnormalized(max_d=2, alpha=1.0))
    assert math.isclose(uplift[0], 0.0, abs_tol=_TOLERANCE)
    assert math.isclose(uplift[1], math.exp(-1.5), abs_tol=_TOLERANCE)
    assert math.isclose(uplift[2], 0.0, abs_tol=_TOLERANCE)


def test_zero_input_source_contributes_nothing() -> None:
    """No source has positive input -> zero uplift everywhere."""
    graph = GraphData(
        node_ids=(0, 1, 2),
        adjacency=(
            (Edge(target=1, weight=1.0),),
            (Edge(target=2, weight=1.0),),
            (),
        ),
        inputs=(0.0, 0.0, 0.0),
    )
    uplift = propagate(graph, _unnormalized(max_d=5, alpha=1.0))
    assert all(math.isclose(v, 0.0, abs_tol=_TOLERANCE) for v in uplift.values())


def test_disconnected_components_are_isolated() -> None:
    """Two directed 2-node components with different input scales stay isolated."""
    graph = GraphData(
        node_ids=(100, 101, 200, 201),
        adjacency=(
            (Edge(target=1, weight=1.0),),
            (),
            (Edge(target=3, weight=1.0),),
            (),
        ),
        inputs=(1.0, 0.0, 100.0, 0.0),
    )
    uplift = propagate(graph, _unnormalized(max_d=5, alpha=1.0))
    assert math.isclose(uplift[100], 0.0, abs_tol=_TOLERANCE)
    assert math.isclose(uplift[101], math.exp(-1.0), abs_tol=_TOLERANCE)
    assert math.isclose(uplift[200], 0.0, abs_tol=_TOLERANCE)
    assert math.isclose(uplift[201], 100.0 * math.exp(-1.0), abs_tol=_TOLERANCE)


def test_normalize_scales_by_maximum() -> None:
    """With normalize=true the max uplift equals 1.0 and ratios are preserved."""
    graph = GraphData(
        node_ids=(0, 1, 2),
        adjacency=(
            (Edge(target=1, weight=1.0),),
            (Edge(target=2, weight=1.0),),
            (),
        ),
        inputs=(1.0, 1.0, 1.0),
    )
    uplift = propagate(graph, Params(k_hop_radius=2, decay_weight=1.0, normalize=True))
    expected_1 = math.exp(-1.0) / (math.exp(-2.0) + math.exp(-1.0))
    assert math.isclose(uplift[0], 0.0, abs_tol=_TOLERANCE)
    assert math.isclose(uplift[1], expected_1, abs_tol=_TOLERANCE)
    assert math.isclose(uplift[2], 1.0, abs_tol=_TOLERANCE)


def test_invalid_graph_raises() -> None:
    """A graph that fails is_valid() must raise ValueError."""
    bad_graph = GraphData(
        node_ids=(0, 1),
        adjacency=((Edge(target=99, weight=1.0),), ()),
        inputs=(1.0, 1.0),
    )
    with pytest.raises(ValueError, match="invariant"):
        propagate(bad_graph, Params())
