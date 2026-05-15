// PageRankDiffusion strategy unit tests — Phase 4.8.
//
// Pins the algorithm contract on graphs whose stationary distribution
// is hand-computable. Tests use normalize=false where possible so the
// pre-normalization values stay legible.

#include <cmath>
#include <memory>
#include <numeric>
#include <string>

#include "gtest/gtest.h"

#include "streetsense/propagator/graph.h"
#include "streetsense/propagator/registry.h"
#include "streetsense/propagator/strategy.h"

namespace {

using streetsense::propagator::Edge;
using streetsense::propagator::GraphData;
using streetsense::propagator::Params;
using streetsense::propagator::PropagationStrategy;
using streetsense::propagator::StrategyRegistry;

constexpr double kTolerance = 1e-9;

std::unique_ptr<PropagationStrategy> make_strategy() {
    auto strategy = StrategyRegistry::instance().lookup("pagerank-diffusion");
    EXPECT_NE(strategy, nullptr)
        << "pagerank-diffusion must be statically registered";
    return strategy;
}

Params pagerank_params(double damping, bool normalize) {
    Params p;
    p.k_hop_radius = 2;  // ignored by pagerank-diffusion
    p.decay_weight = damping;
    p.normalize = normalize;
    return p;
}

TEST(PageRankDiffusionTest, StrategyIsRegistered) {
    const auto names = StrategyRegistry::instance().list_names();
    bool found = false;
    for (const auto& name : names) {
        if (name == "pagerank-diffusion") {
            found = true;
            break;
        }
    }
    EXPECT_TRUE(found);
}

TEST(PageRankDiffusionTest, NameAndVersion) {
    auto strategy = make_strategy();
    ASSERT_NE(strategy, nullptr);
    EXPECT_EQ(strategy->name(), "pagerank-diffusion");
    EXPECT_EQ(strategy->version(), "0.1.0");
}

TEST(PageRankDiffusionTest, AllZeroInputsProduceZeroUplift) {
    GraphData graph;
    graph.node_ids = {0, 1};
    graph.adjacency = {{Edge{1, 1.0}}, {Edge{0, 1.0}}};
    graph.inputs = {0.0, 0.0};

    auto strategy = make_strategy();
    const auto uplift = strategy->propagate(
        graph, pagerank_params(/*damping=*/0.85, /*normalize=*/false));

    for (const auto& [node_id, value] : uplift) {
        static_cast<void>(node_id);
        EXPECT_NEAR(value, 0.0, kTolerance);
    }
}

TEST(PageRankDiffusionTest, DampingZeroReproducesTeleportDistribution) {
    // damping=0 means the walker always teleports -- the stationary
    // distribution IS the teleportation distribution. With inputs
    // proportional to {1, 3}, pi = {0.25, 0.75}.
    GraphData graph;
    graph.node_ids = {0, 1};
    graph.adjacency = {{Edge{1, 1.0}}, {Edge{0, 1.0}}};
    graph.inputs = {1.0, 3.0};

    auto strategy = make_strategy();
    const auto uplift = strategy->propagate(
        graph, pagerank_params(/*damping=*/0.0, /*normalize=*/false));

    EXPECT_NEAR(uplift.at(0), 0.25, kTolerance);
    EXPECT_NEAR(uplift.at(1), 0.75, kTolerance);
}

TEST(PageRankDiffusionTest, UnnormalizedOutputSumsToOne) {
    // The stationary distribution of any finite ergodic chain is a
    // probability distribution -- pi sums to 1.0 modulo floating-point.
    GraphData graph;
    graph.node_ids = {0, 1, 2, 3};
    graph.adjacency = {
        {Edge{1, 1.0}, Edge{2, 1.0}},
        {Edge{2, 1.0}, Edge{3, 1.0}},
        {Edge{3, 1.0}},
        {Edge{0, 1.0}},
    };
    graph.inputs = {1.0, 1.0, 1.0, 1.0};

    auto strategy = make_strategy();
    const auto uplift = strategy->propagate(
        graph, pagerank_params(/*damping=*/0.85, /*normalize=*/false));

    double sum = 0.0;
    for (const auto& [node_id, value] : uplift) {
        static_cast<void>(node_id);
        sum += value;
    }
    // Power-method tolerance is 1e-12 internally; allow a small
    // accumulation slack on the L1 sum across 4 nodes.
    EXPECT_NEAR(sum, 1.0, 1e-10);
}

TEST(PageRankDiffusionTest, DanglingNodeRedistributesToTeleport) {
    // 0 -> 1; node 1 is dangling (no out-edges). With damping > 0
    // the only sink for mass is the teleport distribution; the
    // stationary pi reflects this.
    //
    // Uniform teleport (inputs all 1.0) means pi must be skewed
    // toward node 1 (which absorbs every step) but bounded by the
    // teleport-back rate. With damping=0.85 the analytical fixed
    // point is:
    //   pi[1] = 0.15*0.5 + 0.85*pi[0]*1 + 0.85*pi[1]*0.5_teleport
    // ...but let's not pin the exact value -- assert the inequality
    // that pi[1] > pi[0] (mass accumulates at the sink).
    GraphData graph;
    graph.node_ids = {0, 1};
    graph.adjacency = {{Edge{1, 1.0}}, {}};
    graph.inputs = {1.0, 1.0};

    auto strategy = make_strategy();
    const auto uplift = strategy->propagate(
        graph, pagerank_params(/*damping=*/0.85, /*normalize=*/false));

    EXPECT_GT(uplift.at(1), uplift.at(0));
    // Both still positive (teleport ensures every node is visited).
    EXPECT_GT(uplift.at(0), 0.0);
    EXPECT_GT(uplift.at(1), 0.0);
}

TEST(PageRankDiffusionTest, NormalizeScalesMaxToOne) {
    // Same dangling-sink graph; with normalize=true the larger value
    // (pi[1]) is rescaled to exactly 1.0.
    GraphData graph;
    graph.node_ids = {0, 1};
    graph.adjacency = {{Edge{1, 1.0}}, {}};
    graph.inputs = {1.0, 1.0};

    auto strategy = make_strategy();
    const auto uplift = strategy->propagate(
        graph, pagerank_params(/*damping=*/0.85, /*normalize=*/true));

    double max_value = 0.0;
    for (const auto& [node_id, value] : uplift) {
        static_cast<void>(node_id);
        if (value > max_value) {
            max_value = value;
        }
    }
    EXPECT_NEAR(max_value, 1.0, kTolerance);
}

}  // namespace
