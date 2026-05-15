// WeightedShortestPath strategy unit tests — Phase 4.8.
//
// Pins the algorithm contract on canonical graphs that exercise the
// Dijkstra-driven decay semantics. Tests use normalize=false so the
// expected uplift values are direct functions of exp(-alpha * d).

#include <cmath>
#include <memory>
#include <string>
#include <vector>

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
    auto strategy = StrategyRegistry::instance().lookup("weighted-shortest-path");
    EXPECT_NE(strategy, nullptr)
        << "weighted-shortest-path must be statically registered";
    return strategy;
}

Params unnormalized_params(int max_d, double alpha) {
    Params p;
    p.k_hop_radius = max_d;
    p.decay_weight = alpha;
    p.normalize = false;
    return p;
}

TEST(WeightedShortestPathTest, StrategyIsRegistered) {
    const auto names = StrategyRegistry::instance().list_names();
    bool found = false;
    for (const auto& name : names) {
        if (name == "weighted-shortest-path") {
            found = true;
            break;
        }
    }
    EXPECT_TRUE(found);
}

TEST(WeightedShortestPathTest, NameAndVersion) {
    auto strategy = make_strategy();
    ASSERT_NE(strategy, nullptr);
    EXPECT_EQ(strategy->name(), "weighted-shortest-path");
    EXPECT_EQ(strategy->version(), "0.1.0");
}

TEST(WeightedShortestPathTest, TrivialOneNodeGraphHasZeroUplift) {
    GraphData graph;
    graph.node_ids = {42};
    graph.adjacency = {{}};
    graph.inputs = {7.0};

    auto strategy = make_strategy();
    const auto uplift = strategy->propagate(graph, unnormalized_params(2, 0.5));

    ASSERT_EQ(uplift.count(42), 1U);
    EXPECT_NEAR(uplift.at(42), 0.0, kTolerance);
}

TEST(WeightedShortestPathTest, LinearChainUnitWeights) {
    // 3-node directed chain 0->1->2 with unit edge weights. inputs all
    // 1.0. alpha=1.0, max_d=2. Expected uplifts (only forward-reachable):
    //   uplift[1] = inputs[0]*exp(-1*1) + (inputs[2] doesn't reach 1) = e^-1
    //   uplift[2] = inputs[0]*exp(-1*2) + inputs[1]*exp(-1*1) = e^-2 + e^-1
    //   uplift[0] = 0 (no incoming edges, no shorter path from later sources)
    GraphData graph;
    graph.node_ids = {0, 1, 2};
    graph.adjacency = {
        {Edge{1, 1.0}},
        {Edge{2, 1.0}},
        {},
    };
    graph.inputs = {1.0, 1.0, 1.0};

    auto strategy = make_strategy();
    const auto uplift = strategy->propagate(graph, unnormalized_params(2, 1.0));

    EXPECT_NEAR(uplift.at(0), 0.0, kTolerance);
    EXPECT_NEAR(uplift.at(1), std::exp(-1.0), kTolerance);
    EXPECT_NEAR(uplift.at(2), std::exp(-2.0) + std::exp(-1.0), kTolerance);
}

TEST(WeightedShortestPathTest, DistanceCutoffExcludesFartherTargets) {
    // 0 -> 1 (w=1.5) -> 2 (w=1.5). With max_distance=2.0, source 0
    // reaches 1 (d=1.5) but not 2 (d=3.0 > 2.0).
    GraphData graph;
    graph.node_ids = {0, 1, 2};
    graph.adjacency = {
        {Edge{1, 1.5}},
        {Edge{2, 1.5}},
        {},
    };
    graph.inputs = {1.0, 0.0, 0.0};

    auto strategy = make_strategy();
    const auto uplift = strategy->propagate(graph, unnormalized_params(2, 1.0));

    EXPECT_NEAR(uplift.at(0), 0.0, kTolerance);
    EXPECT_NEAR(uplift.at(1), std::exp(-1.5), kTolerance);
    EXPECT_NEAR(uplift.at(2), 0.0, kTolerance);
}

TEST(WeightedShortestPathTest, ZeroInputSourceContributesNothing) {
    // Same chain but all inputs are zero -> zero uplift everywhere.
    GraphData graph;
    graph.node_ids = {0, 1, 2};
    graph.adjacency = {
        {Edge{1, 1.0}},
        {Edge{2, 1.0}},
        {},
    };
    graph.inputs = {0.0, 0.0, 0.0};

    auto strategy = make_strategy();
    const auto uplift = strategy->propagate(graph, unnormalized_params(5, 1.0));
    for (const auto& [node_id, value] : uplift) {
        static_cast<void>(node_id);
        EXPECT_NEAR(value, 0.0, kTolerance);
    }
}

TEST(WeightedShortestPathTest, DisconnectedComponentsAreIsolated) {
    // Component 1: 0 -> 1 with unit weight. Component 2: 2 -> 3 with
    // unit weight. With large max_distance, C1 nodes only see C1
    // contributions and vice versa.
    GraphData graph;
    graph.node_ids = {100, 101, 200, 201};
    graph.adjacency = {
        {Edge{1, 1.0}},
        {},
        {Edge{3, 1.0}},
        {},
    };
    graph.inputs = {1.0, 0.0, 100.0, 0.0};

    auto strategy = make_strategy();
    const auto uplift = strategy->propagate(graph, unnormalized_params(5, 1.0));

    EXPECT_NEAR(uplift.at(100), 0.0, kTolerance);
    EXPECT_NEAR(uplift.at(101), std::exp(-1.0), kTolerance);
    EXPECT_NEAR(uplift.at(200), 0.0, kTolerance);
    EXPECT_NEAR(uplift.at(201), 100.0 * std::exp(-1.0), kTolerance);
}

TEST(WeightedShortestPathTest, NormalizeScalesByMaximum) {
    // Reuse the linear chain (forward-directed) where the max uplift
    // is at node 2. With normalize=true, max(uplift)=1.0 and ratios
    // are preserved.
    GraphData graph;
    graph.node_ids = {0, 1, 2};
    graph.adjacency = {
        {Edge{1, 1.0}},
        {Edge{2, 1.0}},
        {},
    };
    graph.inputs = {1.0, 1.0, 1.0};

    Params params;
    params.k_hop_radius = 2;
    params.decay_weight = 1.0;
    params.normalize = true;

    auto strategy = make_strategy();
    const auto uplift = strategy->propagate(graph, params);

    // Pre-normalization: uplift[0]=0, uplift[1]=e^-1≈0.3679,
    // uplift[2]=e^-2 + e^-1 ≈ 0.5032. Max = uplift[2].
    // Post-normalization: uplift[1] = (e^-1)/(e^-2 + e^-1) = e/(1+e).
    const double expected_1 = std::exp(-1.0) / (std::exp(-2.0) + std::exp(-1.0));
    EXPECT_NEAR(uplift.at(0), 0.0, kTolerance);
    EXPECT_NEAR(uplift.at(1), expected_1, kTolerance);
    EXPECT_NEAR(uplift.at(2), 1.0, kTolerance);
}

}  // namespace
