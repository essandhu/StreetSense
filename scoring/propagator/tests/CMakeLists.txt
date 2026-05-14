# scoring/propagator/tests/CMakeLists.txt
#
# Phase 4.1.7 placeholder. The first GoogleTest cases land in Phase 4.2.2
# (StrategyRegistry tests). When those land, this file calls
# FetchContent_MakeAvailable(googletest) (declared at the project root)
# and registers each `test_*.cc` with `gtest_discover_tests`.
#
# Refs:
#   - conductor/tracks/phase-4-propagator/plan.md Phase 4.2
#   - top-level CMakeLists.txt (declares GoogleTest FetchContent at v1.14.0)

# Future Phase 4.2 wiring (sketch):
#   FetchContent_MakeAvailable(googletest)
#   add_executable(propagator_unit_tests
#       test_registry.cc
#       test_graph.cc
#       test_influence_diffusion.cc
#   )
#   target_link_libraries(propagator_unit_tests
#       PRIVATE propagator_engine GTest::gtest_main
#   )
#   include(GoogleTest)
#   gtest_discover_tests(propagator_unit_tests)
