"""Hypothesis strategies for random GraphData generation.

The parity property test in Phase 4.4.4 uses these to drive
hypothesis-based comparisons between the C++ engine and this
reference implementation. Phase 4.3's property tests
(``test_reference_properties.py``) use a smaller-scaled variant.

Sizing rationale:
- ``max_nodes=50`` keeps each example's BFS work under ~1 ms in the
  pure-Python reference, so a 200-example hypothesis run completes
  under 30s wall-clock.
- ``max_edges=200`` saturates at ~5 outgoing edges per node, which
  is realistic for road graphs (cities rarely exceed 6-way
  intersections).
- Edge weights are non-negative finite floats in [0, 10] to avoid
  pathological floating-point edge cases (overflow on
  ``decay ** large_distance`` is bounded by k_hop_radius anyway).
"""

from __future__ import annotations

from hypothesis import strategies as st

from .types import Edge, GraphData


def graph_data(
    *,
    max_nodes: int = 50,
    max_edges: int = 200,
    max_input: float = 10.0,
) -> st.SearchStrategy[GraphData]:
    """Return a hypothesis strategy for random valid GraphData payloads.

    The generated graphs are directed; self-loops and parallel edges
    are possible but rare under the default sizes.
    """

    @st.composite
    def _graph(draw: st.DrawFn) -> GraphData:
        n = draw(st.integers(min_value=1, max_value=max_nodes))
        node_ids = tuple(range(n))
        inputs = tuple(
            draw(
                st.lists(
                    st.floats(
                        min_value=0.0,
                        max_value=max_input,
                        allow_nan=False,
                        allow_infinity=False,
                    ),
                    min_size=n,
                    max_size=n,
                )
            )
        )
        edge_count = draw(st.integers(min_value=0, max_value=max_edges))
        # Build adjacency as a list of lists keyed by source node.
        adjacency_lists: list[list[Edge]] = [[] for _ in range(n)]
        for _ in range(edge_count):
            u = draw(st.integers(min_value=0, max_value=n - 1))
            v = draw(st.integers(min_value=0, max_value=n - 1))
            weight = draw(
                st.floats(
                    min_value=0.0,
                    max_value=max_input,
                    allow_nan=False,
                    allow_infinity=False,
                )
            )
            adjacency_lists[u].append(Edge(target=v, weight=weight))
        adjacency = tuple(tuple(edges) for edges in adjacency_lists)
        return GraphData(node_ids=node_ids, adjacency=adjacency, inputs=inputs)

    return _graph()


__all__ = ["graph_data"]
