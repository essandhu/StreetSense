# scoring/propagator/reference/

Pure-Python reference implementation of the StreetSense propagator —
the **correctness oracle** for the C++ engine.

The C++ engine in `scoring/propagator/src/` is the production codepath
(faster than a Python implementation by design — see ADR 0006). This
package is *intentionally* simple and slow: it exists so the C++
engine has a ground-truth check, and so the in-track benchmark can
honestly report a speedup ratio (Task 4.8.5).

## Phase 4 status

- **Phase 4.1:** empty package scaffold (this README + `__init__.py`).
- **Phase 4.3:** the `InfluenceDiffusion` reference impl lands here;
  the unit tests in `test_reference.py` mirror the C++ tests in
  `tests/test_influence_diffusion.cc` on the *same* fixture inputs
  (trivial graph, linear chain, star, disconnected components,
  self-loops).
- **Phase 4.4:** the pybind11 bindings ship; the parity property test
  (`tests/python/test_bindings_parity.py`) calls both this package and
  the C++ engine on `hypothesis`-generated random graphs and asserts
  byte-equivalent per-node uplift values to within 1e-9.
- **Phase 4.8.5:** the speedup benchmark
  (`benchmarks/propagator/python_vs_cpp.py`) runs both engines on a
  50 k-edge graph and asserts a ≥ 10× speedup for the C++ engine; a
  smaller ratio is a smell (likely a debug-mode build or a
  Python-marshalling pathology).

## Why the reference impl uses only stdlib + networkx

The reference impl is the *minimum-surprise* implementation: stdlib
data structures + `networkx` for graph operations (already a
transitive dep via OSMnx). No `numpy`, no `numba`, no Cython — those
optimizations belong to the C++ engine, where they don't muddy the
parity comparison.
