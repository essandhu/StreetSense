# scoring/propagator/

**Phase 4 — placeholder only.**

The Network Risk Propagator is the one native component in StreetSense.
C++17+ with Boost.Graph, exposed via pybind11. Built with CMake.

## Why this is the FFI boundary

Propagating risk over a city-scale graph (~500k edges) in under 5 seconds
on commodity hardware is the one performance budget where interpreted code
is genuinely insufficient. Every other component stays in Python or
TypeScript. Adding a second native component requires an ADR.

## Structure (will exist in Phase 4)

```
propagator/
├── CMakeLists.txt
├── include/streetsense/propagator/   # public C++ headers
├── src/                              # C++ implementation
├── bindings/                         # pybind11 module — thin layer only
├── reference/                        # pure-Python correctness oracle
├── tests/                            # GoogleTest or Catch2
└── bench/                            # Google Benchmark
```

## Invariants

- Public API is **algorithm-agnostic** — strategy selected by parameter.
- Pure-Python reference implementation alongside the C++ engine is the
  correctness oracle.
- Benchmark publishes on every commit; perf regressions >10% block PRs.
- GIL released around long-running C++ work via `py::gil_scoped_release`.
