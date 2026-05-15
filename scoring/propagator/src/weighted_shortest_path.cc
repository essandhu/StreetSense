// WeightedShortestPath strategy — Phase 4.8 ADR-finalization track.
//
// Per ADR 0006 §"Candidate B": for each source segment with a non-zero
// input, run single-source Dijkstra on the GraphData's edge weights as
// distance, then map each reachable target's distance to an uplift via
// an exponential-decay curve. Receivers accumulate contributions from
// every source within the `max_distance` cutoff.
//
// Param reinterpretation (the same Params struct serves every
// strategy; see ADR 0006 §"Posture"):
//
//   - decay_weight  — alpha for `exp(-alpha * distance)` decay.
//   - k_hop_radius  — max-distance cutoff in edge-weight units (the
//                     integer field is cast to double; distance > cutoff
//                     contributes zero). "Radius" generalizes naturally
//                     from hops (influence-diffusion) to weight units
//                     (this strategy) so the param vocabulary stays
//                     consistent across the registry.
//   - normalize     — per-graph max-rescaling (same semantics as
//                     influence-diffusion).
//
// Sources with input == 0.0 are short-circuited (no contribution),
// which is how the strategy avoids the V·(V+E)·log(V) worst case on
// graphs where most inputs are zero — typical for the scoring-run's
// per-hour vectors where glare-affected segments are a sparse subset.
//
// Refs:
//   - docs/adr/0006-propagation-algorithm.md §"Candidate B"
//   - scoring/propagator/reference/weighted_shortest_path.py — the
//     correctness oracle this engine must match byte-equivalent on
//     hypothesis-generated random graphs.

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <limits>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include <boost/graph/adjacency_list.hpp>
#include <boost/graph/dijkstra_shortest_paths.hpp>
#include <boost/property_map/property_map.hpp>
#include <spdlog/spdlog.h>

#include "streetsense/propagator/graph.h"
#include "streetsense/propagator/registry.h"
#include "streetsense/propagator/strategy.h"

namespace streetsense::propagator {

namespace {

// Boost adjacency_list specialization matching influence_diffusion.cc.
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

class WeightedShortestPath : public PropagationStrategy {
public:
    UpliftMap propagate(const GraphData& graph, const Params& params) const override {
        const auto t_start = std::chrono::steady_clock::now();
        const std::size_t n = graph.node_ids.size();

        UpliftMap uplift;
        uplift.reserve(n);
        for (const NodeId id : graph.node_ids) {
            uplift[id] = 0.0;
        }

        const BglGraph bgl = to_bgl(graph);
        const double max_distance = std::max(0.0, static_cast<double>(params.k_hop_radius));
        const double alpha = params.decay_weight;

        // Edge count for telemetry (computed once, not per source).
        std::size_t edge_count = 0;
        for (const auto& neighbors : graph.adjacency) {
            edge_count += neighbors.size();
        }

        std::vector<double> distance(n);
        std::size_t active_sources = 0;

        for (std::size_t source = 0; source < n; ++source) {
            const double src_input = graph.inputs[source];
            if (src_input == 0.0) {
                continue;
            }
            ++active_sources;

            std::fill(distance.begin(), distance.end(),
                      std::numeric_limits<double>::infinity());
            boost::dijkstra_shortest_paths(
                bgl,
                source,
                boost::distance_map(boost::make_iterator_property_map(
                    distance.begin(), boost::get(boost::vertex_index, bgl))));

            for (std::size_t target = 0; target < n; ++target) {
                if (target == source) {
                    continue;
                }
                const double d = distance[target];
                if (!std::isfinite(d) || d > max_distance) {
                    continue;
                }
                uplift[graph.node_ids[target]] += src_input * std::exp(-alpha * d);
            }
        }

        if (params.normalize) {
            double max_value = 0.0;
            for (const auto& [node_id, value] : uplift) {
                static_cast<void>(node_id);
                max_value = std::max(max_value, value);
            }
            if (max_value > 0.0) {
                for (auto& [node_id, value] : uplift) {
                    static_cast<void>(node_id);
                    value /= max_value;
                }
            }
        }

        const auto elapsed_us = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - t_start)
                                    .count();
        spdlog::debug(
            "weighted-shortest-path: nodes={} edges={} active_sources={} "
            "max_d={} alpha={} normalize={} elapsed_us={}",
            n,
            edge_count,
            active_sources,
            max_distance,
            alpha,
            params.normalize,
            elapsed_us);

        return uplift;
    }

    std::string name() const override { return "weighted-shortest-path"; }
    std::string version() const override { return "0.1.0"; }
};

// Self-registration at static-init time. See influence_diffusion.cc for
// the maybe_unused-attribute rationale.
[[maybe_unused]] const bool registered_weighted_shortest_path = []() {
    return StrategyRegistry::instance().register_strategy(
        "weighted-shortest-path",
        [] { return std::make_unique<WeightedShortestPath>(); });
}();

}  // namespace

}  // namespace streetsense::propagator
