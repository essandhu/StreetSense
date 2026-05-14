// Public graph data structure for the StreetSense propagator.
//
// GraphData is an algorithm-agnostic adjacency-list representation:
//   - One node per road segment, identified externally by NodeId.
//   - Outgoing edges per node, each carrying a non-negative weight.
//   - One scalar input value per node — the segment's local sub-score
//     aggregate at the hour-of-day this graph represents.
//
// The 24-call orchestrator (scoring/propagator/runner.py, Phase 4.6.7)
// builds a fresh GraphData per hour and hands it to propagate() — see
// ADR 0006 §"Posture" and spec.md Technical Note 2.
//
// Refs:
//   - docs/adr/0006-propagation-algorithm.md
//   - conductor/tracks/phase-4-propagator/plan.md Phase 4.2

#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace streetsense::propagator {

// Externally-meaningful identifier for a node (segment). Stable across
// scoring runs; opaque to the propagator.
using NodeId = std::uint64_t;

// Edge weight on the road graph. Typically a function of segment length
// or local-score-product; algorithm-specific. Must be non-negative.
using EdgeWeight = double;

// Per-node input value — the local sub-score aggregate at the hour
// this GraphData represents.
using InputValue = double;

// Outgoing edge from a node to a neighbor.
//
// target is an *index* into the parent GraphData's node arrays (not a
// NodeId). Keeping the public type plain-C++ avoids leaking the choice
// of Boost.Graph adjacency_list into the public header.
struct Edge {
    std::size_t target;
    EdgeWeight weight;
};

// Algorithm-agnostic graph payload.
//
// Invariants (asserted by is_valid()):
//   - node_ids.size() == adjacency.size() == inputs.size().
//   - For every edge e in adjacency: 0 <= e.target < node_ids.size().
//   - node_ids.size() >= 1 (a 0-node graph has no meaningful uplift).
//   - Every e.weight is finite and non-negative.
struct GraphData {
    std::vector<NodeId> node_ids;
    std::vector<std::vector<Edge>> adjacency;
    std::vector<InputValue> inputs;
};

// True iff `graph` satisfies every invariant above.
[[nodiscard]] bool is_valid(const GraphData& graph) noexcept;

}  // namespace streetsense::propagator
