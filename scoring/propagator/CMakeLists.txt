# StreetSense Network Risk Propagator — top-level CMakeLists.
#
# Phase 4.1: skeletal — declares the project, pins the C++17 standard,
# wires in the FetchContent dependencies (Task 4.1.7), and exposes
# umbrella options. Real targets (engine, bindings, tests, bench) are
# added in Tasks 4.1.7 + Phase 4.2+. The propagator is built out-of-
# source under `build/` (gitignored at the repo root).
#
# Refs:
#   - scoring/propagator/README.md (local-dev setup)
#   - conductor/code_styleguides/cpp.md (style rules)
#   - docs/adr/0006-propagation-algorithm.md (algorithm-agnostic posture)

cmake_minimum_required(VERSION 3.22)

project(streetsense_propagator
    VERSION 0.1.0
    DESCRIPTION "StreetSense Network Risk Propagator (C++17 + Boost.Graph)"
    LANGUAGES CXX
)

# Enforce C++17 minimum; allow newer standards locally per the style guide.
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Build options (real wiring lands in Tasks 4.1.7+).
option(STREETSENSE_PROPAGATOR_BUILD_TESTS "Build GoogleTest unit suite" ON)
option(STREETSENSE_PROPAGATOR_BUILD_BENCH "Build Google Benchmark suite" ON)
option(STREETSENSE_PROPAGATOR_BUILD_BINDINGS "Build pybind11 bindings module" ON)
option(STREETSENSE_PROPAGATOR_ENABLE_ASAN "Enable AddressSanitizer (debug builds)" OFF)
option(STREETSENSE_PROPAGATOR_ENABLE_UBSAN "Enable UndefinedBehaviorSanitizer (debug builds)" OFF)

# Phase 4.1 is intentionally a stub. Real targets are added in subsequent
# tasks. `cmake --build` succeeds with nothing to compile — that's the
# Phase 4.1 verification (Task 4.1.7).
message(STATUS "streetsense_propagator: Phase 4.1 scaffold — no targets yet.")
