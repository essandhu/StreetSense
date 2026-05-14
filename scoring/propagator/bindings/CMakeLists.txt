# scoring/propagator/bindings/CMakeLists.txt
#
# Phase 4.4: the pybind11 module `streetsense_propagator` that wraps
# the C++ propagator engine for Python. scikit-build-core invokes
# this CMakeLists during `uv sync` and installs the resulting
# extension into the project venv.

# Bring pybind11 in from the vendored submodule (Task 4.1.6, pinned
# to v2.11.2). Add as a subdirectory so its `pybind11_add_module()`
# command becomes available. The pybind11 submodule's CMakeLists is
# self-contained -- it does not pull additional deps.
add_subdirectory(
    ${CMAKE_SOURCE_DIR}/external/pybind11
    ${CMAKE_BINARY_DIR}/_pybind11_build
    EXCLUDE_FROM_ALL
)

pybind11_add_module(streetsense_propagator MODULE
    streetsense_propagator.cc
)

target_link_libraries(streetsense_propagator
    PRIVATE
        # WHOLE_ARCHIVE keeps the static-init self-registration entries
        # in src/influence_diffusion.cc and friends -- without it the
        # linker discards .obj files whose only symbols are the
        # registry-side-effect const bools, silently un-registering
        # every strategy at module import.
        $<LINK_LIBRARY:WHOLE_ARCHIVE,propagator_engine>
)

# scikit-build-core wheel install: emit the .pyd/.so directly at the
# wheel root (so `import streetsense_propagator` resolves without a
# package wrapper).
install(TARGETS streetsense_propagator
    LIBRARY DESTINATION .
    RUNTIME DESTINATION .  # Windows .pyd is RUNTIME
)
