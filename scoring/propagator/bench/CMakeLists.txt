# scoring/propagator/bench/CMakeLists.txt
#
# Phase 4.1.7 placeholder. The first Google Benchmark suite lands in
# Phase 4.8.3 (`propagator_bench` — 1k / 50k / 500k-edge graphs). When
# that lands, this file calls FetchContent_MakeAvailable(benchmark) and
# wires the executable.
#
# Refs:
#   - conductor/tracks/phase-4-propagator/plan.md Phase 4.8 (Tasks 4.8.3–4.8.5)
#   - top-level CMakeLists.txt (declares Google Benchmark FetchContent at v1.8.3)
#   - benchmarks/propagator/history.jsonl (regression gate — >10% blocks PR)

# Future Phase 4.8.3 wiring (sketch):
#   FetchContent_MakeAvailable(benchmark)
#   add_executable(propagator_bench propagator_bench.cc)
#   target_link_libraries(propagator_bench
#       PRIVATE propagator_engine benchmark::benchmark_main
#   )
