// InfluenceDiffusion strategy unit tests — Phase 4.2.6.
//
// Pins the algorithm contract on five canonical graphs from plan.md
// Task 4.2.6: trivial, linear chain, star, disconnected components,
// self-loops. Each test uses normalize=false so the expected uplift
// values are direct functions of decay^hop * input.

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
using streetsense::propagator::NodeId;
using streetsense::propagator::Params;
using streetsense::propagator::PropagationStrategy;
using streetsense::propagator::StrategyRegistry;

constexpr double kTolerance = 1e-9;

std::unique_ptr<PropagationStrategy> make_strategy() {
    auto strategy = StrategyRegistry::instance().lookup("influence-diffusion");
    EXPECT_NE(strategy, nullptr)
        << "influence-diffusion must be statically registered";
    return strategy;
}

Params unnormalized_params(int k_hop, double decay) {
    Params p;
    p.k_hop_radius = k_hop;
    p.decay_weight = decay;
    p.normalize = false;
    return p;
}

TEST(InfluenceDiffusionTest, StrategyIsRegistered) {
    const auto names = StrategyRegistry::instance().list_names();
    bool found = false;
    for (const auto& name : names) {
        if (name == "influence-diffusion") {
            found = true;
            break;
        }
    }
    EXPECT_TRUE(found);
}

TEST(InfluenceDiffusionTest, NameAndVersion) {
    auto strategy = make_strategy();
    ASSERT_NE(strategy, nullptr);
    EXPECT_EQ(strategy->name(), "influence-diffusion");
    EXPECT_EQ(strategy->version(), "0.1.0");
}

TEST(InfluenceDiffusionTest, TrivialOneNodeGraphHasZeroUplift) {
    // Contract: a single node with no neighbors has nothing to receive
    // and nothing to send -- uplift is 0 (not equal to the local
    // input).
    GraphData graph;
    graph.node_ids = {42};
    graph.adjacency = {{}};
    graph.inputs = {7.0};

    auto strategy = make_strategy();
    const auto uplift = strategy->propagate(graph, unnormalized_params(2, 0.5));

    ASSERT_EQ(uplift.count(42), 1U);
    EXPECT_NEAR(uplift.at(42), 0.0, kTolerance);
}

TEST(InfluenceDiffusionTest, LinearChainUndirectedKEquals2) {
    // 3-node chain A-B-C with bidirectional edges; inputs all 1.0; k=2;
    // decay=0.5. Hand-computed expected uplifts:
    //   uplift[A] = 0.5*inputs[B] + 0.25*inputs[C] = 0.75
    //   uplift[B] = 0.5*inputs[A] + 0.5*inputs[C]  = 1.0
    //   uplift[C] = 0.25*inputs[A] + 0.5*inputs[B] = 0.75
    GraphData graph;
    graph.node_ids = {0, 1, 2};
    graph.adjacency = {
        {Edge{1, 1.0}},
        {Edge{0, 1.0}, Edge{2, 1.0}},
        {Edge{1, 1.0}},
    };
    graph.inputs = {1.0, 1.0, 1.0};

    auto strategy = make_strategy();
    const auto uplift = strategy->propagate(graph, unnormalized_params(2, 0.5));

    EXPECT_NEAR(uplift.at(0), 0.75, kTolerance);
    EXPECT_NEAR(uplift.at(1), 1.0, kTolerance);
    EXPECT_NEAR(uplift.at(2), 0.75, kTolerance);
}

TEST(InfluenceDiffusionTest, StarGraphUndirectedKEquals2) {
    // 1 center (node 0) + 4 leaves (nodes 1..4); bidirectional edges
    // 0<->1, 0<->2, 0<->3, 0<->4. Inputs all 1.0; k=2; decay=0.5.
    // Expected uplifts:
    //   uplift[0] = 4 * 0.5 = 2.0 (each leaf at d=1)
    //   uplift[leaf] = 0.5 (center at d=1) + 3*0.25 (other leaves at d=2)
    //                = 0.5 + 0.75 = 1.25
    GraphData graph;
    graph.node_ids = {0, 1, 2, 3, 4};
    graph.adjacency = {
        {Edge{1, 1.0}, Edge{2, 1.0}, Edge{3, 1.0}, Edge{4, 1.0}},
        {Edge{0, 1.0}},
        {Edge{0, 1.0}},
        {Edge{0, 1.0}},
        {Edge{0, 1.0}},
    };
    graph.inputs = {1.0, 1.0, 1.0, 1.0, 1.0};

    auto strategy = make_strategy();
    const auto uplift = strategy->propagate(graph, unnormalized_params(2, 0.5));

    EXPECT_NEAR(uplift.at(0), 2.0, kTolerance);
    EXPECT_NEAR(uplift.at(1), 1.25, kTolerance);
    EXPECT_NEAR(uplift.at(2), 1.25, kTolerance);
    EXPECT_NEAR(uplift.at(3), 1.25, kTolerance);
    EXPECT_NEAR(uplift.at(4), 1.25, kTolerance);
}

TEST(InfluenceDiffusionTest, DisconnectedComponentsDoNotCrossContaminate) {
    // Component 1: nodes 0-1 with edge 0<->1.
    // Component 2: nodes 2-3 with edge 2<->3.
    // No edges between components -> nodes in C1 must not receive
    // anything from C2 and vice versa.
    GraphData graph;
    graph.node_ids = {100, 101, 200, 201};
    graph.adjacency = {
        {Edge{1, 1.0}},
        {Edge{0, 1.0}},
        {Edge{3, 1.0}},
        {Edge{2, 1.0}},
    };
    graph.inputs = {1.0, 1.0, 100.0, 100.0};  // component 2 has large inputs

    auto strategy = make_strategy();
    const auto uplift = strategy->propagate(graph, unnormalized_params(5, 0.5));

    // Component 1 nodes only see neighbor in C1: 0.5 * 1.0 = 0.5.
    EXPECT_NEAR(uplift.at(100), 0.5, kTolerance);
    EXPECT_NEAR(uplift.at(101), 0.5, kTolerance);
    // Component 2 nodes only see neighbor in C2: 0.5 * 100 = 50.0.
    EXPECT_NEAR(uplift.at(200), 50.0, kTolerance);
    EXPECT_NEAR(uplift.at(201), 50.0, kTolerance);
}

TEST(InfluenceDiffusionTest, SelfLoopsAreIgnored) {
    // Two nodes with a bidirectional edge between them, plus a
    // self-loop on node 0. The self-loop edge must not contribute --
    // BFS treats source's distance as 0 (already-visited), so the
    // back-to-self edge is skipped.
    //
    // Without self-loop, uplift[0] = 0.5 * inputs[1] = 0.5 (k=2, decay=0.5).
    // With self-loop ignored, the result must remain 0.5.
    GraphData graph;
    graph.node_ids = {0, 1};
    graph.adjacency = {
        {Edge{0, 1.0}, Edge{1, 1.0}},  // self-loop + edge to 1
        {Edge{0, 1.0}},
    };
    graph.inputs = {1.0, 1.0};

    auto strategy = make_strategy();
    const auto uplift = strategy->propagate(graph, unnormalized_params(2, 0.5));

    EXPECT_NEAR(uplift.at(0), 0.5, kTolerance);
    EXPECT_NEAR(uplift.at(1), 0.5, kTolerance);
}

TEST(InfluenceDiffusionTest, NormalizeScalesByMaximum) {
    // Reuse the linear-chain graph but with normalize=true. After
    // normalization the max uplift is 1.0 and ratios are preserved.
    GraphData graph;
    graph.node_ids = {0, 1, 2};
    graph.adjacency = {
        {Edge{1, 1.0}},
        {Edge{0, 1.0}, Edge{2, 1.0}},
        {Edge{1, 1.0}},
    };
    graph.inputs = {1.0, 1.0, 1.0};

    Params params;
    params.k_hop_radius = 2;
    params.decay_weight = 0.5;
    params.normalize = true;

    auto strategy = make_strategy();
    const auto uplift = strategy->propagate(graph, params);

    // Pre-normalization: A=0.75, B=1.0, C=0.75; max=1.0.
    // Post-normalization: A=0.75, B=1.0, C=0.75.
    EXPECT_NEAR(uplift.at(0), 0.75, kTolerance);
    EXPECT_NEAR(uplift.at(1), 1.0, kTolerance);
    EXPECT_NEAR(uplift.at(2), 0.75, kTolerance);
}

}  // namespace
