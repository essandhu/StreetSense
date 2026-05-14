// StrategyRegistry implementation — Phase 4.2.3.
//
// See include/streetsense/propagator/registry.h for the contract.

#include "streetsense/propagator/registry.h"

#include <utility>

namespace streetsense::propagator {

StrategyRegistry& StrategyRegistry::instance() {
    // Meyers singleton — initialized on first call under the C++17
    // function-local-static thread-safety guarantee.
    static StrategyRegistry registry;
    return registry;
}

bool StrategyRegistry::register_strategy(std::string id, FactoryFn factory) {
    const std::lock_guard<std::mutex> lock(mutex_);
    const auto [it, inserted] = strategies_.emplace(std::move(id), std::move(factory));
    (void)it;
    return inserted;
}

std::unique_ptr<PropagationStrategy> StrategyRegistry::lookup(const std::string& id) const {
    const std::lock_guard<std::mutex> lock(mutex_);
    const auto it = strategies_.find(id);
    if (it == strategies_.end()) {
        return nullptr;
    }
    return it->second();
}

std::vector<std::string> StrategyRegistry::list_names() const {
    const std::lock_guard<std::mutex> lock(mutex_);
    std::vector<std::string> names;
    names.reserve(strategies_.size());
    for (const auto& [id, _] : strategies_) {
        names.push_back(id);
    }
    return names;
}

}  // namespace streetsense::propagator
