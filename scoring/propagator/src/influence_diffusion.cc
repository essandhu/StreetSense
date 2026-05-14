// InfluenceDiffusion strategy — Phase 4.2.7.
//
// Per ADR 0006 §"Candidate A": for each source node, propagate its
// local input value outward up to k hops with exponential decay.
// Receivers accumulate weighted contributions from all sources within
// k hops. The shortest-path distance is used when multiple paths
// exist between (source, target).
//
// Implementation: backed by boost::adjacency_list (Phase 4.2.7's
// "internal use of Boost.Graph"); a hand-rolled BFS performs the
// k-bounded traversal because boost::breadth_first_search's visitor
// API does not natively support early termination at a fixed depth
// without throwing. The Boost graph type is preserved so subsequent
// algorithms (weighted-shortest-path-amplification, PageRank) can
// reuse the conversion helper.
//
// Refs:
//   - docs/adr/0006-propagation-algorithm.md
//   - conductor/tracks/phase-4-propagator/plan.md Task 4.2.7

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <memory>
#include <queue>
#include <string>
#include <utility>
#include <vector>

#include <boost/graph/adjacency_list.hpp>

#include "streetsense/propagator/graph.h"
#include "streetsense/propagator/registry.h"
#include "streetsense/propagator/strategy.h"

namespace streetsense::propagator {

namespace {

// Boost adjacency_list specialization the strategy uses internally.
// vecS for both vertex + edge containers gives O(1) vertex lookup +
// stable indices that match GraphData's adjacency vector layout.
using BglGraph = boost::adjacency_list<
    boost::vecS,
    boost::vecS,
    boost::directedS,
    boost::no_property,
    boost::property<boost::edge_weight_t, EdgeWeight>>;

BglGraph to_bgl(const GraphData& graph) {
    BglGraph bgl(graph.node_ids.size());
    for (std::size_t u = 0; u < graph.adjacency.size(); ++u) {
        for (const Edge& edge : graph.adjacency[u]) {
            boost::add_edge(u, edge.target, edge.weight, bgl);
        }
    }
    return bgl;
}

// k-bounded BFS from `source`. Returns a vector of hop distances:
// dist[v] = shortest hop count from source to v, or -1 if not
// reachable within k hops.
std::vector<int> bfs_k_bounded(const BglGraph& graph, std::size_t source, int k_max) {
    const std::size_t n = boost::num_vertices(graph);
    std::vector<int> distance(n, -1);
    distance[source] = 0;
    std::queue<std::size_t> frontier;
    frontier.push(source);

    while (!frontier.empty()) {
        const std::size_t u = frontier.front();
        frontier.pop();
        if (distance[u] >= k_max) {
            continue;
        }
        // Iterate out-edges via BGL.
        auto edges = boost::out_edges(u, graph);
        for (auto it = edges.first; it != edges.second; ++it) {
            const std::size_t v = boost::target(*it, graph);
            if (distance[v] == -1) {
                distance[v] = distance[u] + 1;
                frontier.push(v);
            }
        }
    }
    return distance;
}

class InfluenceDiffusion : public PropagationStrategy {
public:
    UpliftMap propagate(const GraphData& graph, const Params& params) const override {
        UpliftMap uplift;
        uplift.reserve(graph.node_ids.size());
        // Initialize every node to zero uplift so callers can iterate
        // the full key set deterministically.
        for (const NodeId id : graph.node_ids) {
            uplift[id] = 0.0;
        }

        const BglGraph bgl = to_bgl(graph);
        const int k_max = std::max(1, params.k_hop_radius);
        const double decay = params.decay_weight;

        for (std::size_t source = 0; source < graph.node_ids.size(); ++source) {
            const std::vector<int> distance = bfs_k_bounded(bgl, source, k_max);
            const double src_input = graph.inputs[source];

            for (std::size_t target = 0; target < graph.node_ids.size(); ++target) {
                if (target == source) {
                    continue;
                }
                const int d = distance[target];
                if (d > 0 && d <= k_max) {
                    uplift[graph.node_ids[target]] += std::pow(decay, d) * src_input;
                }
            }
        }

        if (params.normalize) {
            double max_value = 0.0;
            for (const auto& [_, value] : uplift) {
                max_value = std::max(max_value, value);
            }
            if (max_value > 0.0) {
                for (auto& [_, value] : uplift) {
                    value /= max_value;
                }
            }
        }
        return uplift;
    }

    std::string name() const override { return "influence-diffusion"; }
    std::string version() const override { return "0.1.0"; }
};

// Self-registration at static-initialization time. The (void) cast
// silences the unused-variable warning for this side-effect-only
// declaration; the bool's value is the registration result (true on
// success, false if a duplicate was already present).
const bool registered_influence_diffusion = []() {
    return StrategyRegistry::instance().register_strategy(
        "influence-diffusion",
        [] { return std::make_unique<InfluenceDiffusion>(); });
}();

}  // namespace

}  // namespace streetsense::propagator
