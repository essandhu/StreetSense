// Algorithm-agnostic strategy registry.
//
// Concrete strategies register themselves at static-initialization time
// via:
//   namespace { const bool registered_<id> =
//       StrategyRegistry::instance().register_strategy(
//           "<id>",
//           [] { return std::make_unique<ConcreteStrategy>(); });
//   }
//
// Callers (the pybind11 binding, the in-track benchmark) look up
// strategies by string id at call time. Adding a new strategy is one
// .cc file + one anonymous-namespace registration block — no header
// changes, no binding changes, no Python-caller changes.
//
// Refs:
//   - docs/adr/0006-propagation-algorithm.md
//   - spec.md Technical Note 3 — "the registry is the seam"

#pragma once

#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include "streetsense/propagator/strategy.h"

namespace streetsense::propagator {

class StrategyRegistry {
public:
    // Factory that returns a freshly-constructed strategy instance.
    // Callers receive unique ownership; the registry retains only the
    // factory.
    using FactoryFn = std::function<std::unique_ptr<PropagationStrategy>()>;

    // Function-local static (Meyers singleton). Thread-safe in C++17
    // (the initializer runs at most once under the language's
    // first-call semantics).
    static StrategyRegistry& instance();

    // Register a strategy under `id`. Returns true on success, false
    // if `id` is already registered (double-registration is a
    // programmer error caught here rather than silently overwriting).
    // Thread-safe.
    bool register_strategy(std::string id, FactoryFn factory);

    // Look up a strategy by id. Returns a freshly-constructed
    // instance owned by the caller, or nullptr if `id` is not
    // registered. Thread-safe for concurrent reads.
    [[nodiscard]] std::unique_ptr<PropagationStrategy> lookup(
        const std::string& id) const;

    // List the ids of all registered strategies, sorted lexically.
    // Used by the pybind11 module's `strategies` attribute so the
    // Python caller can introspect what's available.
    [[nodiscard]] std::vector<std::string> list_names() const;

    StrategyRegistry(const StrategyRegistry&) = delete;
    StrategyRegistry& operator=(const StrategyRegistry&) = delete;
    StrategyRegistry(StrategyRegistry&&) = delete;
    StrategyRegistry& operator=(StrategyRegistry&&) = delete;

private:
    StrategyRegistry() = default;
    ~StrategyRegistry() = default;

    mutable std::mutex mutex_;
    std::map<std::string, FactoryFn> strategies_;
};

}  // namespace streetsense::propagator
