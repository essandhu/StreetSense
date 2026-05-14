# scoring/propagator/bindings/CMakeLists.txt
#
# Phase 4.1.7 placeholder. The pybind11 module (streetsense_propagator)
# lands in Phase 4.4. The bindings layer is intentionally thin — type
# marshalling + GIL release only; no business logic — per CLAUDE.md and
# the C++ style guide.
#
# Refs:
#   - conductor/tracks/phase-4-propagator/plan.md Phase 4.4
#   - external/pybind11 (submodule pinned to v2.11.2 in Task 4.1.6)

# Future Phase 4.4 wiring (sketch, for code-archaeology readability):
#   add_subdirectory(${CMAKE_SOURCE_DIR}/external/pybind11 pybind11_build)
#   pybind11_add_module(streetsense_propagator streetsense_propagator.cc)
#   target_link_libraries(streetsense_propagator PRIVATE propagator_engine)
