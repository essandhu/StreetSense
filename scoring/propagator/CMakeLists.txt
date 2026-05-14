# StreetSense Network Risk Propagator — top-level CMakeLists.
#
# Phase 4.1.7 scaffold: declares the project, pins the C++17 standard,
# declares FetchContent for spdlog / GoogleTest / Google Benchmark
# (pinned to specific release tags), and adds the directory skeleton.
# Real targets land in Phase 4.2+ — each subdirectory has its own
# CMakeLists.txt that currently contains only the documentation marker
# for the task that will fill it in.
#
# Refs:
#   - scoring/propagator/README.md (local-dev setup, prerequisite Boost install)
#   - conductor/code_styleguides/cpp.md (style rules)
#   - docs/adr/0006-propagation-algorithm.md (algorithm-agnostic posture)

cmake_minimum_required(VERSION 3.22)

project(streetsense_propagator
    VERSION 0.1.0
    DESCRIPTION "StreetSense Network Risk Propagator (C++17 + Boost.Graph)"
    LANGUAGES CXX
)

# Enforce C++17 minimum; allow newer locally per the style guide.
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Out-of-source build is enforced via CMakePresets.json (Task 4.1.8).

# ---------------------------------------------------------------------------
# Build options
# ---------------------------------------------------------------------------
option(STREETSENSE_PROPAGATOR_BUILD_TESTS "Build GoogleTest unit suite" ON)
option(STREETSENSE_PROPAGATOR_BUILD_BENCH "Build Google Benchmark suite" ON)
option(STREETSENSE_PROPAGATOR_BUILD_BINDINGS "Build pybind11 bindings module" ON)
option(STREETSENSE_PROPAGATOR_ENABLE_ASAN "Enable AddressSanitizer (debug build)" OFF)
option(STREETSENSE_PROPAGATOR_ENABLE_UBSAN "Enable UndefinedBehaviorSanitizer (debug build)" OFF)

# ---------------------------------------------------------------------------
# Dependency declarations (FetchContent for header-only / test-only deps)
# ---------------------------------------------------------------------------
# `FetchContent_Declare` records where to fetch from; `FetchContent_MakeAvailable`
# (called from the subdirectory that needs the dep) does the actual clone +
# add_subdirectory. Phase 4.1 has no targets, so nothing is fetched at configure
# time; the configure run is fast and offline-clean.
include(FetchContent)

# spdlog: structured logging inside the propagator. MIT.
FetchContent_Declare(spdlog
    GIT_REPOSITORY https://github.com/gabime/spdlog.git
    GIT_TAG        v1.12.0
    GIT_SHALLOW    TRUE
)

# GoogleTest: C++ unit-test framework. BSD-3-Clause.
FetchContent_Declare(googletest
    GIT_REPOSITORY https://github.com/google/googletest.git
    GIT_TAG        v1.14.0
    GIT_SHALLOW    TRUE
)
# Required for GoogleTest's CMake on Windows to play nicely with the MSVC
# runtime selection chosen by the parent project.
set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)

# Google Benchmark: C++ microbenchmarking with statistical reporting. Apache-2.0.
FetchContent_Declare(benchmark
    GIT_REPOSITORY https://github.com/google/benchmark.git
    GIT_TAG        v1.8.3
    GIT_SHALLOW    TRUE
)
set(BENCHMARK_ENABLE_TESTING OFF CACHE BOOL "" FORCE)

# ---------------------------------------------------------------------------
# Boost.Graph (system dependency)
# ---------------------------------------------------------------------------
# Phase 4.1: discovery is deferred to the first sub-target that actually
# links against Boost (lands in Phase 4.2 with the graph + strategy
# registry). Configuring without Boost is intentional — empty subdirs
# don't include Boost headers, so a configure-time `find_package(Boost)`
# would force an install dependency before any code needs it.
#
# When Phase 4.2 wires it in:
#   find_package(Boost 1.81 REQUIRED COMPONENTS graph)
# Refer to scoring/propagator/README.md for the per-OS Boost install path.

# ---------------------------------------------------------------------------
# Subdirectory skeleton
# ---------------------------------------------------------------------------
# Each subdirectory carries a CMakeLists.txt that's a no-op at Phase 4.1
# and will be filled in by the Phase named in its own header comment.
add_subdirectory(src)

if(STREETSENSE_PROPAGATOR_BUILD_BINDINGS)
    add_subdirectory(bindings)
endif()

if(STREETSENSE_PROPAGATOR_BUILD_TESTS)
    enable_testing()
    add_subdirectory(tests)
endif()

if(STREETSENSE_PROPAGATOR_BUILD_BENCH)
    add_subdirectory(bench)
endif()

message(STATUS "streetsense_propagator: configured (Phase 4.1 scaffold; no targets yet)")
