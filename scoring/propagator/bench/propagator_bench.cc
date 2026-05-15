// Google Benchmark suite for the Phase 4 propagator — Task 4.8.3.
//
// Three size classes mirror the StreetSense scaling story:
//
//   - 1k edges    — toy / smoke test; sets the per-iteration floor so a
//                   regression caused by overhead in marshalling or the
//                   strategy registry is visible.
//   - 50k edges   — mid-sized city; the Python-vs-C++ speedup gate
//                   (Task 4.8.5) runs at this size.
//   - 500k edges  — Cambridge-scale; the spec's < 5 s per-call ceiling
//                   from CLAUDE.md applies at this size.
//
// The graph generator is deterministic (fixed seed) so cross-commit
// comparisons are apples-to-apples. Each benchmark records the C++
// engine's algorithm-name + version via the kPropagatorVersion string
// so a future history-file consumer can correlate regressions to a
// specific algorithm revision.
//
// Refs:
//   - conductor/tracks/phase-4-propagator/plan.md Task 4.8.3
//   - benchmarks/propagator/history.jsonl — regression history
//   - scripts/check_propagator_perf_regression.py — the > 10 % gate

#include <benchmark/benchmark.h>

#include <cstddef>
#include <random>

#include "streetsense/propagator/graph.h"
#include "streetsense/propagator/registry.h"
#include "streetsense/propagator/strategy.h"
#include "streetsense/propagator/version.h"

namespace prop = streetsense::propagator;

namespace {

// Construct a deterministic random GraphData with `n_nodes` and
// `n_edges`. Edge weights are in [0, 1]; per-node inputs are in [0, 1].
// Seed is fixed so the benchmark is comparable across builds.
prop::GraphData MakeGraph(std::size_t n_nodes, std::size_t n_edges) {
    std::mt19937 rng(42u);
    std::uniform_real_distribution<double> weight_dist(0.0, 1.0);
    std::uniform_int_distribution<std::size_t> node_dist(0, n_nodes - 1);

    prop::GraphData graph;
    graph.node_ids.reserve(n_nodes);
    graph.adjacency.resize(n_nodes);
    graph.inputs.reserve(n_nodes);
    for (std::size_t i = 0; i < n_nodes; ++i) {
        graph.node_ids.push_back(static_cast<prop::NodeId>(i));
        graph.inputs.push_back(weight_dist(rng));
    }
    for (std::size_t e = 0; e < n_edges; ++e) {
        const auto src = node_dist(rng);
        const auto dst = node_dist(rng);
        prop::Edge edge;
        edge.target = dst;
        edge.weight = weight_dist(rng);
        graph.adjacency[src].push_back(edge);
    }
    return graph;
}

// One-shot harness: each iteration runs `propagate()` once on the
// pre-built graph. The graph build cost stays outside the timed loop
// via `state.SetItemsProcessed` (we report propagation work only).
//
// Algorithm: `pagerank-diffusion` is the production algorithm chosen
// by ADR 0006. The other two registered strategies stay benchmarkable
// via the registry but are not on the >10% perf gate.
void BM_PagerankDiffusion(benchmark::State& state) {
    const auto n_nodes = static_cast<std::size_t>(state.range(0));
    const auto n_edges = static_cast<std::size_t>(state.range(1));
    auto graph = MakeGraph(n_nodes, n_edges);
    prop::Params params;
    // ADR 0006 §"Parameter Defaults" — k_hop_radius is ignored by
    // pagerank-diffusion but the field is preserved for cross-strategy
    // Params uniformity; decay_weight is the canonical PageRank damping
    // factor; normalize=true rescales uplift onto the same magnitude
    // as the per-segment local aggregate for downstream composite
    // assembly.
    params.k_hop_radius = 2;
    params.decay_weight = 0.85;
    params.normalize = true;

    auto strategy = prop::StrategyRegistry::instance().lookup("pagerank-diffusion");
    if (strategy == nullptr) {
        state.SkipWithError("pagerank-diffusion strategy not registered");
        return;
    }

    for (auto _ : state) {
        auto uplift = strategy->propagate(graph, params);
        benchmark::DoNotOptimize(uplift);
    }
    state.SetItemsProcessed(static_cast<int64_t>(state.iterations() * n_edges));
    state.SetLabel(prop::kPropagatorVersion);
}

// Register the three size classes documented in the file header.
// Argument pairs: {node_count, edge_count}.
BENCHMARK(BM_PagerankDiffusion)
    ->Args({1'000, 1'000})
    ->Args({10'000, 50'000})
    ->Args({100'000, 500'000})
    ->Unit(benchmark::kMillisecond)
    ->ReportAggregatesOnly(true)
    ->Repetitions(5);

}  // namespace

BENCHMARK_MAIN();
