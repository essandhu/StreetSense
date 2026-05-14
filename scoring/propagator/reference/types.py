"""Python-side counterparts to the C++ graph + strategy types.

Mirrors include/streetsense/propagator/{graph,strategy}.h closely enough
that the parity property test in Phase 4.4 can feed identical inputs
to both engines and compare per-node uplift outputs byte-equivalent
(modulo 1e-9 floating-point tolerance).

The dataclasses are frozen + slotted for cheap hash/equality and
small memory footprint -- the reference impl is the correctness
oracle, not the production codepath, but no need to be wasteful.
"""

from __future__ import annotations

from dataclasses import dataclass

# Externally-meaningful identifier for a node. Stable across scoring
# runs; opaque to the propagator.
type NodeId = int

# Edge weight on the road graph. Non-negative; finite.
type EdgeWeight = float

# Per-node input value -- the segment's local sub-score aggregate at
# the hour-of-day this graph represents.
type InputValue = float


@dataclass(frozen=True, slots=True)
class Edge:
    """Outgoing edge from a node to a neighbor.

    ``target`` is an *index* into the parent GraphData's node arrays
    (matching the C++ Edge.target shape).
    """

    target: int
    weight: EdgeWeight


@dataclass(frozen=True, slots=True)
class GraphData:
    """Algorithm-agnostic graph payload (Python mirror of GraphData.h).

    Invariants (asserted by :func:`is_valid`):

    - ``len(node_ids) == len(adjacency) == len(inputs)``.
    - Every edge target is in ``range(len(node_ids))``.
    - ``len(node_ids) >= 1``.
    - Every edge weight is finite and non-negative.
    """

    node_ids: tuple[NodeId, ...]
    adjacency: tuple[tuple[Edge, ...], ...]
    inputs: tuple[InputValue, ...]


@dataclass(frozen=True, slots=True)
class Params:
    """Strategy parameters surfaced through the Python caller.

    Same fields as the C++ Params struct; types are richer (bool vs
    int convertible) but the values map one-to-one.
    """

    k_hop_radius: int = 2
    decay_weight: float = 0.5
    normalize: bool = True


def is_valid(graph: GraphData) -> bool:
    """Return True iff ``graph`` satisfies every documented invariant.

    Mirrors C++ ``streetsense::propagator::is_valid`` for parity in
    test-fixture validation.
    """
    import math

    n = len(graph.node_ids)
    if n == 0:
        return False
    if len(graph.adjacency) != n or len(graph.inputs) != n:
        return False
    for neighbors in graph.adjacency:
        for edge in neighbors:
            if not (0 <= edge.target < n):
                return False
            if not math.isfinite(edge.weight) or edge.weight < 0.0:
                return False
    return all(math.isfinite(value) for value in graph.inputs)


# UpliftMap mirrors the C++ std::unordered_map<NodeId, double>.
type UpliftMap = dict[NodeId, float]


__all__ = [
    "Edge",
    "EdgeWeight",
    "GraphData",
    "InputValue",
    "NodeId",
    "Params",
    "UpliftMap",
    "is_valid",
]
