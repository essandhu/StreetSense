# scoring/propagator/src/CMakeLists.txt
#
# Phase 4.2: propagator_engine static library — the core graph-compute
# engine. Strategies live in src/strategies/ (added in Phase 4.2.7).
# Bindings (Phase 4.4) and tests (Phase 4.2.2+) link against this
# target.

# Boost.Graph is header-only; we resolve its include directory via a
# portable find_path so this works across Linux (apt: /usr/include),
# macOS (brew: /opt/homebrew/include), and Windows (manual install:
# $ENV{BOOST_ROOT}). CMake's FindBoost module was removed in 4.x;
# Boost CONFIG-mode files don't ship with the manual Windows tarball.
find_path(BOOST_GRAPH_INCLUDE_DIR
    NAMES boost/graph/adjacency_list.hpp
    HINTS
        $ENV{BOOST_ROOT}
        $ENV{Boost_DIR}
    PATHS
        /usr/include
        /usr/local/include
        /opt/homebrew/include
        /opt/local/include
    REQUIRED
)
message(STATUS "Boost.Graph headers found at: ${BOOST_GRAPH_INCLUDE_DIR}")

# spdlog is declared at the project root; pull it in here so
# propagator_engine can use it for structured per-call logging.
FetchContent_MakeAvailable(spdlog)

add_library(propagator_engine STATIC
    graph.cc
    influence_diffusion.cc
    pagerank_diffusion.cc
    registry.cc
    weighted_shortest_path.cc
)

target_include_directories(propagator_engine
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/../include
        ${BOOST_GRAPH_INCLUDE_DIR}
)

target_link_libraries(propagator_engine PRIVATE spdlog::spdlog)

# C++17 propagated to consumers via INTERFACE.
target_compile_features(propagator_engine PUBLIC cxx_std_17)

# Default warning-as-error posture for our own code (third-party deps
# fetched via FetchContent keep their own settings).
if(MSVC)
    target_compile_options(propagator_engine PRIVATE /W4 /permissive-)
else()
    target_compile_options(propagator_engine PRIVATE -Wall -Wextra -Wpedantic)
endif()

# Sanitizers (debug preset). Wired here so every subsequent target
# (tests, bench, bindings) inherits the flags transparently when
# linking against propagator_engine.
if(STREETSENSE_PROPAGATOR_ENABLE_ASAN AND NOT MSVC)
    target_compile_options(propagator_engine PUBLIC -fsanitize=address -fno-omit-frame-pointer)
    target_link_options(propagator_engine PUBLIC -fsanitize=address)
endif()
if(STREETSENSE_PROPAGATOR_ENABLE_UBSAN AND NOT MSVC)
    target_compile_options(propagator_engine PUBLIC -fsanitize=undefined -fno-omit-frame-pointer)
    target_link_options(propagator_engine PUBLIC -fsanitize=undefined)
endif()
# MSVC supports /fsanitize=address but not UBSan; the propagator-cpp CI
# job runs on Linux where clang's UBSan is available, so we don't try
# to enable a Windows-MSVC ASan path here.
