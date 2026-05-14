// pybind11 module — Phase 4.4.1.
//
// Thin marshalling layer over the C++ propagator engine. The
// bindings convert Python types to C++ types, look up the strategy
// by id, release the GIL around the propagation call, and convert
// the resulting uplift map back to Python.
//
// No business logic lives here. The strategy ABI (algorithm-agnostic
// `propagate(graph, strategy_id, params)`) means new algorithms drop
// in as new .cc files under src/strategies/ with a single static-init
// registration line — no changes to this file, no changes to the
// Python caller.
//
// Refs:
//   - docs/adr/0006-propagation-algorithm.md §"Posture"
//   - spec.md AC-3 (algorithm-agnostic, GIL-releasing bindings)
//   - conductor/tracks/phase-4-propagator/plan.md Task 4.4.1

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>

#include "streetsense/propagator/graph.h"
#include "streetsense/propagator/registry.h"
#include "streetsense/propagator/strategy.h"
#include "streetsense/propagator/version.h"

namespace py = pybind11;
namespace prop = streetsense::propagator;

namespace {

// Marshal a Python dict matching the documented GraphData shape into
// a C++ GraphData. Validation failures raise py::value_error with a
// message naming the offending field; no business logic, just type
// + range checks.
prop::GraphData marshal_graph(const py::dict& py_graph) {
    if (!py_graph.contains("node_ids")) {
        throw py::value_error("graph dict missing 'node_ids'");
    }
    if (!py_graph.contains("adjacency")) {
        throw py::value_error("graph dict missing 'adjacency'");
    }
    if (!py_graph.contains("inputs")) {
        throw py::value_error("graph dict missing 'inputs'");
    }

    prop::GraphData graph;

    for (const auto& nid : py_graph["node_ids"].cast<py::list>()) {
        graph.node_ids.push_back(nid.cast<prop::NodeId>());
    }

    for (const auto& neighbors_obj : py_graph["adjacency"].cast<py::list>()) {
        std::vector<prop::Edge> neighbors;
        for (const auto& edge_obj : neighbors_obj.cast<py::list>()) {
            const auto edge_pair = edge_obj.cast<py::tuple>();
            if (edge_pair.size() != 2) {
                throw py::value_error("adjacency entries must be (target, weight) tuples");
            }
            prop::Edge edge;
            edge.target = edge_pair[0].cast<std::size_t>();
            edge.weight = edge_pair[1].cast<prop::EdgeWeight>();
            neighbors.push_back(edge);
        }
        graph.adjacency.push_back(std::move(neighbors));
    }

    for (const auto& v : py_graph["inputs"].cast<py::list>()) {
        graph.inputs.push_back(v.cast<prop::InputValue>());
    }

    return graph;
}

prop::Params marshal_params(const py::dict& py_params) {
    prop::Params params;
    if (py_params.contains("k_hop_radius")) {
        params.k_hop_radius = py_params["k_hop_radius"].cast<int>();
    }
    if (py_params.contains("decay_weight")) {
        params.decay_weight = py_params["decay_weight"].cast<double>();
    }
    if (py_params.contains("normalize")) {
        params.normalize = py_params["normalize"].cast<bool>();
    }
    return params;
}

py::dict marshal_uplift(const prop::UpliftMap& uplift) {
    py::dict result;
    for (const auto& [node_id, value] : uplift) {
        result[py::int_(node_id)] = value;
    }
    return result;
}

py::dict propagate(const py::dict& py_graph,
                   const std::string& strategy_id,
                   const py::dict& py_params) {
    // Convert types under the GIL (Python access required).
    auto graph = marshal_graph(py_graph);
    auto params = marshal_params(py_params);

    // Strategy lookup is cheap and lock-free in steady state; do it
    // under the GIL so the value_error path is straightforward.
    auto strategy = prop::StrategyRegistry::instance().lookup(strategy_id);
    if (strategy == nullptr) {
        throw py::value_error("unknown strategy_id: " + strategy_id);
    }

    // Run the algorithm with the GIL released. The C++ engine touches
    // no Python state during propagation, so other Python threads can
    // make progress concurrently.
    prop::UpliftMap uplift;
    {
        py::gil_scoped_release release;
        uplift = strategy->propagate(graph, params);
    }

    return marshal_uplift(uplift);
}

}  // namespace

PYBIND11_MODULE(streetsense_propagator, m) {
    m.doc() =
        "StreetSense Network Risk Propagator -- native C++ engine + pybind11 bindings.\n"
        "\n"
        "Public surface:\n"
        "  propagate(graph: dict, strategy_id: str, params: dict) -> dict[int, float]\n"
        "  strategies: list[str]  -- registered strategy ids\n"
        "  version: str           -- package semver (kPropagatorVersion in C++)";

    m.def("propagate",
          &propagate,
          py::arg("graph"),
          py::arg("strategy_id"),
          py::arg("params"),
          "Run the named strategy against `graph` and return per-node uplift.\n"
          "Raises ValueError if strategy_id is not registered or graph is malformed.");

    // List the registered strategies at import time. New strategies
    // register at static-init; if a future caller imports
    // streetsense_propagator after a dynamic registration, this list
    // is point-in-time at import.
    m.attr("strategies") = prop::StrategyRegistry::instance().list_names();
    m.attr("version") = prop::kPropagatorVersion;
}
