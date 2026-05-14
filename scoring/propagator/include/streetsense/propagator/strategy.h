// Abstract base for propagation strategies.
//
// Every concrete algorithm implements this interface; the
// StrategyRegistry (registry.h) keeps a string -> factory map; the
// pybind11 binding (bindings/streetsense_propagator.cc, Phase 4.4)
// looks up strategies by id and invokes propagate().
//
// Adding a new strategy is one .cc file + one static-init registration
// entry. No changes to this header, to the bindings, or to the Python
// caller. This is the seam that keeps Extension Point 4 open.
//
// Refs:
//   - docs/adr/0006-propagation-algorithm.md §"Posture"
//   - CLAUDE.md Extension Point 4
//   - spec.md Technical Note 3

#pragma once

#include <string>
#include <unordered_map>

#include "streetsense/propagator/graph.h"

namespace streetsense::propagator {

// Per-node propagation uplift, keyed by external NodeId.
using UpliftMap = std::unordered_map<NodeId, double>;

// Strategy parameters surfaced through the Python caller. Typed (not
// dict<str, Any>) so a future strategy that needs new parameters
// extends the struct rather than reshaping the interface.
struct Params {
    // Maximum hop radius for locality-bounded strategies (k-hop BFS,
    // bounded diffusion). Ignored by strategies whose semantics are
    // global (PageRank-style stationary distribution).
    int k_hop_radius{2};

    // Decay weight applied to neighbor contributions per hop.
    // Algorithm-specific; typical range [0.0, 1.0]; defaults pinned in
    // ADR 0006.
    double decay_weight{0.5};

    // Normalize the uplift to a bounded range. Strategies decide their
    // own normalization (per-graph max, per-graph mean, etc.).
    bool normalize{true};
};

// Abstract base for every concrete propagation algorithm.
//
// Concrete strategies are owned by std::unique_ptr created via the
// registry's factory function — see StrategyRegistry::register_strategy.
class PropagationStrategy {
public:
    virtual ~PropagationStrategy() = default;

    PropagationStrategy(const PropagationStrategy&) = delete;
    PropagationStrategy& operator=(const PropagationStrategy&) = delete;
    PropagationStrategy(PropagationStrategy&&) = delete;
    PropagationStrategy& operator=(PropagationStrategy&&) = delete;

    // Compute per-node uplift for `graph` under `params`. Pure
    // function of inputs; no side effects on `graph`.
    [[nodiscard]] virtual UpliftMap propagate(
        const GraphData& graph, const Params& params) const = 0;

    // Stable string identifier for this strategy (e.g.,
    // "influence-diffusion"). Used as the registry key.
    [[nodiscard]] virtual std::string name() const = 0;

    // Semver of the algorithm implementation (e.g., "0.1.0"). Written
    // into scoring_runs.propagation_algorithm_version alongside the
    // strategy name, so a future regression can identify which
    // version produced a given score row.
    [[nodiscard]] virtual std::string version() const = 0;

protected:
    PropagationStrategy() = default;
};

}  // namespace streetsense::propagator
