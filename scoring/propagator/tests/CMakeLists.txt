# scoring/propagator/tests/CMakeLists.txt
#
# Phase 4.2+: GoogleTest unit suite for the propagator engine.
# FetchContent_MakeAvailable(googletest) pulls v1.14.0 (declared at the
# project root) at first configure time. Subsequent builds reuse the
# fetched copy.

FetchContent_MakeAvailable(googletest)

add_executable(propagator_unit_tests
    test_graph.cc
    test_influence_diffusion.cc
    test_registry.cc
)

target_link_libraries(propagator_unit_tests
    PRIVATE
        # WHOLE_ARCHIVE forces the linker to keep all .obj files from
        # propagator_engine, including those whose only symbols are the
        # static-init self-registration entries (e.g.,
        # registered_influence_diffusion in strategies/influence_diffusion.cc).
        # MSVC's linker otherwise discards .obj files whose symbols are
        # not directly referenced by the executable -- silently
        # un-registering every strategy.
        $<LINK_LIBRARY:WHOLE_ARCHIVE,propagator_engine>
        GTest::gtest
        GTest::gtest_main
)

# GoogleTest's gtest_discover_tests scans the compiled binary for
# TEST() and TEST_F() registrations and exposes each as a ctest entry.
include(GoogleTest)
gtest_discover_tests(propagator_unit_tests
    DISCOVERY_TIMEOUT 60
)
