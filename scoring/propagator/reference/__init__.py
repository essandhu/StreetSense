"""Pure-Python reference implementation of the StreetSense propagator.

The C++ engine in ``scoring/propagator/src/`` is the production codepath; this
package mirrors it byte-equivalent for randomly generated graphs and is the
**correctness oracle** for the parity property tests in Phase 4.4.

Phase 4.1: empty package scaffold; real content lands in Phase 4.3 — see
``conductor/tracks/phase-4-propagator/plan.md`` for the task breakdown and
``scoring/propagator/README.md`` for the local-dev setup.
"""

from __future__ import annotations

from . import influence_diffusion, pagerank_diffusion, weighted_shortest_path
from .types import Edge, GraphData, NodeId, Params, UpliftMap, is_valid

__all__ = [
    "Edge",
    "GraphData",
    "NodeId",
    "Params",
    "UpliftMap",
    "influence_diffusion",
    "is_valid",
    "pagerank_diffusion",
    "weighted_shortest_path",
]
