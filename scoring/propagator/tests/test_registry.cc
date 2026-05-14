// StrategyRegistry unit tests — Phase 4.2.2.
//
// Asserts the four contracts from plan.md Task 4.2.2:
//   1. lookup("nonexistent") returns nullptr.
//   2. register + lookup roundtrip returns the registered strategy.
//   3. Double-registration returns false.
//   4. Concurrent reads are race-free (UBSan/TSan clean).
//
// These tests use the singleton; tests share the same registry
// instance. Each test uses a unique id prefix to avoid cross-test
// pollution.

#include "streetsense/propagator/registry.h"

#include <atomic>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include "gtest/gtest.h"

#include "streetsense/propagator/graph.h"
#include "streetsense/propagator/strategy.h"

namespace {

using streetsense::propagator::GraphData;
using streetsense::propagator::Params;
using streetsense::propagator::PropagationStrategy;
using streetsense::propagator::StrategyRegistry;
using streetsense::propagator::UpliftMap;

// Minimal strategy used by tests. Returns an empty uplift map so the
// tests' attention stays on the registry behavior.
class StubStrategy : public PropagationStrategy {
public:
    UpliftMap propagate(const GraphData& /*graph*/, const Params& /*params*/) const override {
        return {};
    }
    std::string name() const override { return "stub"; }
    std::string version() const override { return "0.0.0"; }
};

TEST(StrategyRegistryTest, LookupNonexistentReturnsNullptr) {
    auto& registry = StrategyRegistry::instance();
    EXPECT_EQ(registry.lookup("registry-test-nonexistent-xyz"), nullptr);
}

TEST(StrategyRegistryTest, RegisterThenLookupReturnsStrategy) {
    auto& registry = StrategyRegistry::instance();
    const std::string id = "registry-test-register-lookup";

    const bool registered = registry.register_strategy(
        id, [] { return std::make_unique<StubStrategy>(); });
    EXPECT_TRUE(registered);

    auto strategy = registry.lookup(id);
    ASSERT_NE(strategy, nullptr);
    EXPECT_EQ(strategy->name(), "stub");
    EXPECT_EQ(strategy->version(), "0.0.0");
}

TEST(StrategyRegistryTest, DoubleRegistrationReturnsFalse) {
    auto& registry = StrategyRegistry::instance();
    const std::string id = "registry-test-double-register";

    const bool first = registry.register_strategy(
        id, [] { return std::make_unique<StubStrategy>(); });
    const bool second = registry.register_strategy(
        id, [] { return std::make_unique<StubStrategy>(); });
    EXPECT_TRUE(first);
    EXPECT_FALSE(second);
}

TEST(StrategyRegistryTest, ListNamesIncludesRegistered) {
    auto& registry = StrategyRegistry::instance();
    const std::string id = "registry-test-list-names";
    registry.register_strategy(id, [] { return std::make_unique<StubStrategy>(); });

    const auto names = registry.list_names();
    bool found = false;
    for (const auto& name : names) {
        if (name == id) {
            found = true;
            break;
        }
    }
    EXPECT_TRUE(found);
}

TEST(StrategyRegistryTest, ConcurrentReadsAreRaceFree) {
    auto& registry = StrategyRegistry::instance();
    const std::string id = "registry-test-concurrent-reads";
    registry.register_strategy(id, [] { return std::make_unique<StubStrategy>(); });

    constexpr int kThreadCount = 8;
    constexpr int kLookupsPerThread = 1000;

    std::atomic<int> hits{0};
    std::vector<std::thread> threads;
    threads.reserve(kThreadCount);
    for (int i = 0; i < kThreadCount; ++i) {
        threads.emplace_back([&registry, &hits, &id, kLookupsPerThread] {
            for (int j = 0; j < kLookupsPerThread; ++j) {
                auto strategy = registry.lookup(id);
                if (strategy != nullptr) {
                    hits.fetch_add(1, std::memory_order_relaxed);
                }
            }
        });
    }
    for (auto& thread : threads) {
        thread.join();
    }
    EXPECT_EQ(hits.load(), kThreadCount * kLookupsPerThread);
}

}  // namespace
