# scoring/propagator/bench/CMakeLists.txt
#
# Phase 4.8.3: Google Benchmark suite for the C++ propagator. Three
# size classes (1k / 50k / 500k edges) exercise the production
# algorithm under realistic city-scale graphs.
#
# Refs:
#   - propagator_bench.cc (the suite source)
#   - scripts/check_propagator_perf_regression.py (the > 10 % gate)
#   - benchmarks/propagator/history.jsonl (regression history)

FetchContent_MakeAvailable(benchmark)

add_executable(propagator_bench propagator_bench.cc)
target_link_libraries(propagator_bench
    PRIVATE
        # WHOLE_ARCHIVE forces the linker to keep all .obj files from
        # propagator_engine, including those whose only symbols are the
        # static-init self-registration entries (e.g.,
        # registered_pagerank_diffusion in src/pagerank_diffusion.cc).
        # MSVC's linker otherwise discards .obj files whose symbols are
        # not directly referenced by the executable -- silently
        # un-registering every strategy and making the bench skip with
        # "strategy not registered". Mirrors tests/CMakeLists.txt.
        $<LINK_LIBRARY:WHOLE_ARCHIVE,propagator_engine>
        benchmark::benchmark_main
)
target_compile_features(propagator_bench PRIVATE cxx_std_17)

# Match the engine's warning posture so a fresh contributor catches
# the same issues at build time. Treat warnings as errors only on the
# CI runners (the propagator-cpp job sets CTEST_OUTPUT_ON_FAILURE).
if(MSVC)
    target_compile_options(propagator_bench PRIVATE /W4 /permissive-)
else()
    target_compile_options(propagator_bench PRIVATE -Wall -Wextra -Wpedantic)
endif()
