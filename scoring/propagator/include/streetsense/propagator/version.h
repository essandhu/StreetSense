// Propagator versioning.
//
// kPropagatorVersion is the *package* semver — the version of the
// streetsense_propagator C++ engine + bindings as a whole. It bumps
// when the public API or wire format changes; it does not bump for
// internal refactors or for adding a new strategy.
//
// Per-strategy versions are exposed by PropagationStrategy::version()
// (see strategy.h). Those bump when an algorithm's parameter
// defaults or output semantics change; they are written into
// scoring_runs.propagation_algorithm_version alongside the strategy
// name so the reproducibility chain identifies the exact algorithm
// that produced any persisted score row.

#pragma once

namespace streetsense::propagator {

inline constexpr const char* kPropagatorVersion = "0.1.0";

}  // namespace streetsense::propagator
