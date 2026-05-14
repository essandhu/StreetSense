// GraphData implementation — Phase 4.2.5.
//
// Public surface stays plain-C++ (see include/streetsense/propagator/graph.h).
// Strategies that need Boost.Graph idioms construct a
// boost::adjacency_list internally inside their propagate()
// implementation; GraphData itself does not depend on Boost.
//
// is_valid() asserts the four invariants documented on GraphData.

#include "streetsense/propagator/graph.h"

#include <cmath>

namespace streetsense::propagator {

bool is_valid(const GraphData& graph) noexcept {
    const std::size_t n = graph.node_ids.size();
    if (n == 0) {
        return false;
    }
    if (graph.adjacency.size() != n || graph.inputs.size() != n) {
        return false;
    }
    for (const auto& neighbors : graph.adjacency) {
        for (const Edge& edge : neighbors) {
            if (edge.target >= n) {
                return false;
            }
            if (!std::isfinite(edge.weight) || edge.weight < 0.0) {
                return false;
            }
        }
    }
    for (const InputValue value : graph.inputs) {
        if (!std::isfinite(value)) {
            return false;
        }
    }
    return true;
}

}  // namespace streetsense::propagator
