"""Unit tests for the Python reference implementation.

Mirrors the five canonical scenarios from
``tests/test_influence_diffusion.cc`` on the *same fixed inputs* so
the C++ and Python engines have a shared fixed-point. The parity
property test in Phase 4.4 generalizes this with hypothesis-generated
graphs; these tests pin the contract at the literal-number level.
"""

from __future__ import annotations

import math

import pytest

from scoring.propagator.reference.influence_diffusion import name, propagate, version
from scoring.propagator.reference.types import Edge, GraphData, Params

_TOLERANCE = 1e-9


def _unnormalized(k_hop: int, decay: float) -> Params:
    return Params(k_hop_radius=k_hop, decay_weight=decay, normalize=False)


def test_name_and_version() -> None:
    """The reference engine reports the same name and semver as the C++ engine."""
    assert name() == "influence-diffusion"
    assert version() == "0.1.0"


def test_trivial_one_node_graph_has_zero_uplift() -> None:
    """A single isolated node has no neighbors to contribute -> uplift 0."""
    graph = GraphData(
        node_ids=(42,),
        adjacency=((),),
        inputs=(7.0,),
    )
    uplift = propagate(graph, _unnormalized(k_hop=2, decay=0.5))
    assert math.isclose(uplift[42], 0.0, abs_tol=_TOLERANCE)


def test_linear_chain_undirected_k_equals_2() -> None:
    """3-node chain A-B-C with bidirectional edges; inputs all 1.0; k=2, decay=0.5.

    Hand-computed expected uplifts (matches test_influence_diffusion.cc):
      uplift[A] = 0.5*1 + 0.25*1 = 0.75
      uplift[B] = 0.5*1 + 0.5*1  = 1.0
      uplift[C] = 0.25*1 + 0.5*1 = 0.75
    """
    graph = GraphData(
        node_ids=(0, 1, 2),
        adjacency=(
            (Edge(target=1, weight=1.0),),
            (Edge(target=0, weight=1.0), Edge(target=2, weight=1.0)),
            (Edge(target=1, weight=1.0),),
        ),
        inputs=(1.0, 1.0, 1.0),
    )
    uplift = propagate(graph, _unnormalized(k_hop=2, decay=0.5))
    assert math.isclose(uplift[0], 0.75, abs_tol=_TOLERANCE)
    assert math.isclose(uplift[1], 1.0, abs_tol=_TOLERANCE)
    assert math.isclose(uplift[2], 0.75, abs_tol=_TOLERANCE)


def test_star_graph_undirected_k_equals_2() -> None:
    """1 center + 4 leaves; bidirectional edges; inputs all 1.0; k=2, decay=0.5.

    Expected (matches C++ test):
      uplift[center] = 4 * 0.5 = 2.0
      uplift[leaf]   = 0.5 + 3*0.25 = 1.25
    """
    graph = GraphData(
        node_ids=(0, 1, 2, 3, 4),
        adjacency=(
            (
                Edge(target=1, weight=1.0),
                Edge(target=2, weight=1.0),
                Edge(target=3, weight=1.0),
                Edge(target=4, weight=1.0),
            ),
            (Edge(target=0, weight=1.0),),
            (Edge(target=0, weight=1.0),),
            (Edge(target=0, weight=1.0),),
            (Edge(target=0, weight=1.0),),
        ),
        inputs=(1.0, 1.0, 1.0, 1.0, 1.0),
    )
    uplift = propagate(graph, _unnormalized(k_hop=2, decay=0.5))
    assert math.isclose(uplift[0], 2.0, abs_tol=_TOLERANCE)
    for leaf in (1, 2, 3, 4):
        assert math.isclose(uplift[leaf], 1.25, abs_tol=_TOLERANCE)


def test_disconnected_components_do_not_cross_contaminate() -> None:
    """Two 2-node components with very different inputs stay isolated.

    Component 1 (small inputs) and component 2 (large inputs) share
    no edges; uplift in C1 must reflect only C1's inputs and vice
    versa.
    """
    graph = GraphData(
        node_ids=(100, 101, 200, 201),
        adjacency=(
            (Edge(target=1, weight=1.0),),
            (Edge(target=0, weight=1.0),),
            (Edge(target=3, weight=1.0),),
            (Edge(target=2, weight=1.0),),
        ),
        inputs=(1.0, 1.0, 100.0, 100.0),
    )
    uplift = propagate(graph, _unnormalized(k_hop=5, decay=0.5))
    assert math.isclose(uplift[100], 0.5, abs_tol=_TOLERANCE)
    assert math.isclose(uplift[101], 0.5, abs_tol=_TOLERANCE)
    assert math.isclose(uplift[200], 50.0, abs_tol=_TOLERANCE)
    assert math.isclose(uplift[201], 50.0, abs_tol=_TOLERANCE)


def test_self_loops_are_ignored() -> None:
    """Self-loops contribute nothing -- BFS treats source distance=0 as visited.

    Contract: same uplift as the no-self-loop equivalent.
    """
    graph = GraphData(
        node_ids=(0, 1),
        adjacency=(
            (Edge(target=0, weight=1.0), Edge(target=1, weight=1.0)),
            (Edge(target=0, weight=1.0),),
        ),
        inputs=(1.0, 1.0),
    )
    uplift = propagate(graph, _unnormalized(k_hop=2, decay=0.5))
    assert math.isclose(uplift[0], 0.5, abs_tol=_TOLERANCE)
    assert math.isclose(uplift[1], 0.5, abs_tol=_TOLERANCE)


def test_normalize_scales_by_maximum() -> None:
    """With normalize=True the max uplift equals 1.0 and ratios are preserved."""
    graph = GraphData(
        node_ids=(0, 1, 2),
        adjacency=(
            (Edge(target=1, weight=1.0),),
            (Edge(target=0, weight=1.0), Edge(target=2, weight=1.0)),
            (Edge(target=1, weight=1.0),),
        ),
        inputs=(1.0, 1.0, 1.0),
    )
    uplift = propagate(graph, Params(k_hop_radius=2, decay_weight=0.5, normalize=True))
    # Pre-normalization: A=0.75, B=1.0, C=0.75; max=1.0 so no rescaling needed.
    assert math.isclose(uplift[0], 0.75, abs_tol=_TOLERANCE)
    assert math.isclose(uplift[1], 1.0, abs_tol=_TOLERANCE)
    assert math.isclose(uplift[2], 0.75, abs_tol=_TOLERANCE)


def test_invalid_graph_raises() -> None:
    """A graph that fails is_valid() must raise ValueError."""
    bad_graph = GraphData(
        node_ids=(0, 1),
        adjacency=((Edge(target=99, weight=1.0),), ()),  # target out of range
        inputs=(1.0, 1.0),
    )
    with pytest.raises(ValueError, match="invariant"):
        propagate(bad_graph, Params())
