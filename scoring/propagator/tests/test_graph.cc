// GraphData invariant tests — Phase 4.2.4.
//
// Asserts the four contracts documented on graph.h's GraphData struct:
//   1. adjacency.size() == node_ids.size() == inputs.size().
//   2. Every edge target is < node_ids.size().
//   3. node_ids.size() >= 1.
//   4. Every edge weight is finite and non-negative.

#include "streetsense/propagator/graph.h"

#include <cmath>
#include <limits>

#include "gtest/gtest.h"

namespace {

using streetsense::propagator::Edge;
using streetsense::propagator::GraphData;
using streetsense::propagator::is_valid;

GraphData make_valid_triangle() {
    GraphData graph;
    graph.node_ids = {10, 20, 30};
    graph.adjacency = {
        {{1, 1.0}, {2, 2.0}},
        {{0, 1.0}, {2, 1.5}},
        {{0, 2.0}, {1, 1.5}},
    };
    graph.inputs = {0.1, 0.2, 0.3};
    return graph;
}

TEST(GraphDataTest, ValidTriangleIsValid) {
    EXPECT_TRUE(is_valid(make_valid_triangle()));
}

TEST(GraphDataTest, EmptyGraphIsInvalid) {
    GraphData graph;  // zero nodes.
    EXPECT_FALSE(is_valid(graph));
}

TEST(GraphDataTest, AdjacencySizeMismatchIsInvalid) {
    GraphData graph = make_valid_triangle();
    graph.adjacency.pop_back();  // size 2 vs 3 nodes.
    EXPECT_FALSE(is_valid(graph));
}

TEST(GraphDataTest, InputsSizeMismatchIsInvalid) {
    GraphData graph = make_valid_triangle();
    graph.inputs.pop_back();
    EXPECT_FALSE(is_valid(graph));
}

TEST(GraphDataTest, EdgeTargetOutOfRangeIsInvalid) {
    GraphData graph = make_valid_triangle();
    graph.adjacency[0].push_back(Edge{99, 1.0});  // 99 >= 3 nodes.
    EXPECT_FALSE(is_valid(graph));
}

TEST(GraphDataTest, NegativeEdgeWeightIsInvalid) {
    GraphData graph = make_valid_triangle();
    graph.adjacency[0][0].weight = -1.0;
    EXPECT_FALSE(is_valid(graph));
}

TEST(GraphDataTest, NonFiniteEdgeWeightIsInvalid) {
    GraphData graph = make_valid_triangle();
    graph.adjacency[0][0].weight = std::numeric_limits<double>::infinity();
    EXPECT_FALSE(is_valid(graph));
}

TEST(GraphDataTest, NaNInputIsInvalid) {
    GraphData graph = make_valid_triangle();
    graph.inputs[0] = std::nan("");
    EXPECT_FALSE(is_valid(graph));
}

TEST(GraphDataTest, SingleNodeWithNoEdgesIsValid) {
    GraphData graph;
    graph.node_ids = {42};
    graph.adjacency = {{}};  // one node with no outgoing edges.
    graph.inputs = {0.5};
    EXPECT_TRUE(is_valid(graph));
}

TEST(GraphDataTest, SelfLoopIsValid) {
    GraphData graph;
    graph.node_ids = {1};
    graph.adjacency = {{Edge{0, 0.5}}};  // self-loop allowed.
    graph.inputs = {1.0};
    EXPECT_TRUE(is_valid(graph));
}

}  // namespace
