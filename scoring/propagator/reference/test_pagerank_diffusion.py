"""Unit tests for the pagerank-diffusion reference implementation.

Mirrors the canonical scenarios from
``tests/test_pagerank_diffusion.cc`` on the *same fixed inputs* so the
C++ and Python engines have a shared fixed-point.
"""

from __future__ import annotations

import math

import pytest

from scoring.propagator.reference.pagerank_diffusion import (
    name,
    propagate,
    version,
)
from scoring.propagator.reference.types import Edge, GraphData, Params

_TOLERANCE = 1e-9


def _pagerank_params(damping: float, normalize: bool) -> Params:
    return Params(k_hop_radius=2, decay_weight=damping, normalize=normalize)


def test_name_and_version() -> None:
    assert name() == "pagerank-diffusion"
    assert version() == "0.1.0"


def test_all_zero_inputs_produce_zero_uplift() -> None:
    graph = GraphData(
        node_ids=(0, 1),
        adjacency=(
            (Edge(target=1, weight=1.0),),
            (Edge(target=0, weight=1.0),),
        ),
        inputs=(0.0, 0.0),
    )
    uplift = propagate(graph, _pagerank_params(damping=0.85, normalize=False))
    assert all(math.isclose(v, 0.0, abs_tol=_TOLERANCE) for v in uplift.values())


def test_damping_zero_reproduces_teleport_distribution() -> None:
    """damping=0 -> stationary distribution equals the teleportation dist."""
    graph = GraphData(
        node_ids=(0, 1),
        adjacency=(
            (Edge(target=1, weight=1.0),),
            (Edge(target=0, weight=1.0),),
        ),
        inputs=(1.0, 3.0),
    )
    uplift = propagate(graph, _pagerank_params(damping=0.0, normalize=False))
    assert math.isclose(uplift[0], 0.25, abs_tol=_TOLERANCE)
    assert math.isclose(uplift[1], 0.75, abs_tol=_TOLERANCE)


def test_unnormalized_output_sums_to_one() -> None:
    """The stationary distribution sums to ~1.0 (probability distribution)."""
    graph = GraphData(
        node_ids=(0, 1, 2, 3),
        adjacency=(
            (Edge(target=1, weight=1.0), Edge(target=2, weight=1.0)),
            (Edge(target=2, weight=1.0), Edge(target=3, weight=1.0)),
            (Edge(target=3, weight=1.0),),
            (Edge(target=0, weight=1.0),),
        ),
        inputs=(1.0, 1.0, 1.0, 1.0),
    )
    uplift = propagate(graph, _pagerank_params(damping=0.85, normalize=False))
    total = sum(uplift.values())
    assert math.isclose(total, 1.0, abs_tol=1e-10)


def test_dangling_node_redistributes_to_teleport() -> None:
    """Node with no out-edges still receives mass via teleport."""
    graph = GraphData(
        node_ids=(0, 1),
        adjacency=(
            (Edge(target=1, weight=1.0),),
            (),
        ),
        inputs=(1.0, 1.0),
    )
    uplift = propagate(graph, _pagerank_params(damping=0.85, normalize=False))
    # Mass accumulates at the sink (node 1).
    assert uplift[1] > uplift[0]
    # Both still positive due to teleport.
    assert uplift[0] > 0.0
    assert uplift[1] > 0.0


def test_normalize_scales_max_to_one() -> None:
    """normalize=true -> the largest uplift equals exactly 1.0."""
    graph = GraphData(
        node_ids=(0, 1),
        adjacency=(
            (Edge(target=1, weight=1.0),),
            (),
        ),
        inputs=(1.0, 1.0),
    )
    uplift = propagate(graph, _pagerank_params(damping=0.85, normalize=True))
    assert math.isclose(max(uplift.values()), 1.0, abs_tol=_TOLERANCE)


def test_invalid_graph_raises() -> None:
    bad_graph = GraphData(
        node_ids=(0, 1),
        adjacency=((Edge(target=99, weight=1.0),), ()),
        inputs=(1.0, 1.0),
    )
    with pytest.raises(ValueError, match="invariant"):
        propagate(bad_graph, Params())
