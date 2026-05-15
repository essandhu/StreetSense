// PageRankDiffusion strategy — Phase 4.8 ADR-finalization track.
//
// Per ADR 0006 §"Candidate C": treat the per-hour input vector as the
// teleportation distribution `p` and iterate a damped random-walk
// operator until the L1 difference between successive iterates falls
// below kTolerance, or kMaxIterations is reached. The stationary
// distribution `pi[v]` is the propagation uplift at `v` — segments
// well-connected (along edge-weighted out-paths) to high-input
// neighbors accumulate uplift regardless of distance.
//
// Param reinterpretation (every strategy shares the Params struct):
//
//   - decay_weight  — damping factor `d`. With probability `d` the
//                     walker follows an outgoing edge weighted by edge
//                     weight; with probability `(1 - d)` it teleports
//                     to a node drawn from `p`. Typical PageRank value
//                     is 0.85; our project default of 0.5 is fine but
//                     gives less centrality emphasis. The ADR's
//                     in-track benchmark sweeps the parameter.
//   - k_hop_radius  — unused. PageRank's semantics are global; locality
//                     is not a parameter. The field is ignored on this
//                     strategy (preserved so the Params struct stays
//                     algorithm-agnostic — ADR 0006 §"Posture" requires
//                     a typed struct, not a free-form dict).
//   - normalize     — per-graph max-rescaling (same semantics as
//                     influence-diffusion). Without it, the output is
//                     a probability distribution summing to 1.
//
// Edge weight semantics: each step the walker follows out-edge (u → v)
// with probability `weight(u → v) / sum_{w} weight(u → w)`. Edges with
// zero weight are kept as transitions of zero probability — the
// renormalization over `sum_w` handles them naturally. Dangling nodes
// (zero out-strength) redistribute their mass to the teleportation
// distribution `p` on each iteration, matching the standard PageRank
// formulation.
//
// Refs:
//   - docs/adr/0006-propagation-algorithm.md §"Candidate C"
//   - scoring/propagator/reference/pagerank_diffusion.py — the
//     correctness oracle this engine must match byte-equivalent on
//     hypothesis-generated random graphs.

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include <spdlog/spdlog.h>

#include "streetsense/propagator/graph.h"
#include "streetsense/propagator/registry.h"
#include "streetsense/propagator/strategy.h"

namespace streetsense::propagator {

namespace {

// Power-method termination thresholds. Fixed (not surfaced through
// Params) so the algorithm is reproducible: a future Params bump that
// changes these would itself bump the strategy's `version()` and
// retire prior runs' algorithm-version sentinel.
constexpr int kMaxIterations = 100;
constexpr double kTolerance = 1e-12;

// Build the per-source out-edge weight totals + a flat (u, v, weight)
// edge list for fast iteration during the power-method step.
//
// Returns:
//   - `out_weight_sum[u]` = sum of weights of all outgoing edges from u
//   - `edges` = flat list of (source_index, target_index, weight)
struct AdjacencySummary {
    std::vector<double> out_weight_sum;
    std::vector<std::tuple<std::size_t, std::size_t, double>> edges;
};

AdjacencySummary summarize_adjacency(const GraphData& graph) {
    const std::size_t n = graph.node_ids.size();
    AdjacencySummary summary;
    summary.out_weight_sum.assign(n, 0.0);
    for (std::size_t u = 0; u < graph.adjacency.size(); ++u) {
        for (const Edge& edge : graph.adjacency[u]) {
            summary.out_weight_sum[u] += edge.weight;
            summary.edges.emplace_back(u, edge.target, edge.weight);
        }
    }
    return summary;
}

class PageRankDiffusion : public PropagationStrategy {
public:
    UpliftMap propagate(const GraphData& graph, const Params& params) const override {
        const auto t_start = std::chrono::steady_clock::now();
        const std::size_t n = graph.node_ids.size();

        UpliftMap uplift;
        uplift.reserve(n);
        for (const NodeId id : graph.node_ids) {
            uplift[id] = 0.0;
        }

        // Build teleportation distribution `p` from inputs. If every
        // input is zero (a pathological hour), the algorithm has
        // nothing to propagate -- fall through to the zero-init uplift
        // already in place.
        double input_sum = 0.0;
        for (const InputValue value : graph.inputs) {
            input_sum += value;
        }
        if (input_sum == 0.0) {
            spdlog::debug(
                "pagerank-diffusion: nodes={} edges={} damping={} normalize={} "
                "input_sum=0 -> zero uplift",
                n,
                graph.adjacency.size(),
                params.decay_weight,
                params.normalize);
            return uplift;
        }

        std::vector<double> teleport(n);
        for (std::size_t i = 0; i < n; ++i) {
            teleport[i] = graph.inputs[i] / input_sum;
        }

        // Initialize pi to the teleportation distribution. This makes
        // the first iteration's L1 step exactly `damping * (M^T p - p)`
        // -- a faithful power-method starting point.
        std::vector<double> pi = teleport;
        std::vector<double> next_pi(n, 0.0);

        const auto summary = summarize_adjacency(graph);
        const double damping = params.decay_weight;
        const double one_minus_damping = 1.0 - damping;

        int iterations = 0;
        double last_delta = 0.0;
        for (; iterations < kMaxIterations; ++iterations) {
            // Sum mass on dangling nodes (zero out-strength). On every
            // step they redistribute their mass to the teleportation
            // distribution, matching the standard PageRank formulation.
            double dangling_mass = 0.0;
            for (std::size_t u = 0; u < n; ++u) {
                if (summary.out_weight_sum[u] == 0.0) {
                    dangling_mass += pi[u];
                }
            }

            // Baseline contribution per node: teleport mass +
            // damping-weighted dangling redistribution.
            const double dangling_factor = damping * dangling_mass;
            for (std::size_t v = 0; v < n; ++v) {
                next_pi[v] = one_minus_damping * teleport[v]
                             + dangling_factor * teleport[v];
            }

            // Edge contribution: for every (u, v, w), add
            // damping * pi[u] * (w / out_weight_sum[u]) to next_pi[v].
            // Skipping the per-edge division for u with zero out-strength
            // (those are dangling and already redistributed above).
            for (const auto& [u, v, weight] : summary.edges) {
                const double out_sum = summary.out_weight_sum[u];
                if (out_sum == 0.0) {
                    continue;
                }
                next_pi[v] += damping * pi[u] * (weight / out_sum);
            }

            // Convergence check: L1 difference between pi and next_pi.
            double delta = 0.0;
            for (std::size_t v = 0; v < n; ++v) {
                delta += std::abs(next_pi[v] - pi[v]);
            }
            last_delta = delta;

            // Swap before the convergence break so the final pi
            // reflects the most recent iterate.
            pi.swap(next_pi);
            std::fill(next_pi.begin(), next_pi.end(), 0.0);

            if (delta < kTolerance) {
                ++iterations;  // count the converging step
                break;
            }
        }

        for (std::size_t v = 0; v < n; ++v) {
            uplift[graph.node_ids[v]] = pi[v];
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
            "pagerank-diffusion: nodes={} edges={} damping={} normalize={} "
            "iterations={} final_delta={} elapsed_us={}",
            n,
            summary.edges.size(),
            damping,
            params.normalize,
            iterations,
            last_delta,
            elapsed_us);

        return uplift;
    }

    std::string name() const override { return "pagerank-diffusion"; }
    std::string version() const override { return "0.1.0"; }
};

// Self-registration at static-init time. See influence_diffusion.cc for
// the maybe_unused-attribute rationale.
[[maybe_unused]] const bool registered_pagerank_diffusion = []() {
    return StrategyRegistry::instance().register_strategy(
        "pagerank-diffusion",
        [] { return std::make_unique<PageRankDiffusion>(); });
}();

}  // namespace

}  // namespace streetsense::propagator
