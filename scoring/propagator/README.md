# scoring/propagator/

StreetSense's Network Risk Propagator — the **only native C++ component**
in the system (`CLAUDE.md` §"The FFI Boundary"). Implemented in C++17+
on Boost.Graph, exposed to Python via a thin pybind11 binding, with a
pure-Python reference implementation as the correctness oracle.

## Why this is the FFI boundary

Propagating risk over a city-scale graph (~500 k edges) in under 5
seconds on commodity hardware is the one performance budget where
interpreted code is genuinely insufficient. Every other component stays
in Python or TypeScript. Adding a second native component requires an
ADR.

## Layout

```
scoring/propagator/
├── CMakeLists.txt                       — top-level CMake
├── CMakePresets.json                    — default (release) + debug (ASan/UBSan)
├── include/streetsense/propagator/      — public C++ headers
├── src/                                 — C++ implementation
│   └── strategies/                      — algorithm implementations behind the registry
├── bindings/                            — pybind11 module (thin layer only)
├── reference/                           — pure-Python correctness oracle
├── tests/                               — GoogleTest unit suite
├── bench/                               — Google Benchmark suite
└── external/pybind11/                   — vendored submodule (pinned v2.11.x)
```

The core graph library is **pure C++** and independently testable. The
`bindings/` target is the *only* code that imports
`<pybind11/pybind11.h>`. `reference/` is a Python package — its
correctness-oracle role is described in
[`reference/README.md`](./reference/README.md) (written in Phase 4.3).

## Invariants

- **Algorithm-agnostic public API.** Callers pass a graph and a
  `strategy_id`. Adding a new algorithm is one C++ class + one registry
  entry, with zero changes to bindings, Python caller, or API.
- **Pure-Python reference implementation alongside the C++ engine** is
  the correctness oracle. A property-based parity test asserts the two
  produce byte-equivalent output on random graphs.
- **Benchmark publishes on every PR.** Perf regressions > 10 % vs the
  committed baseline block the PR
  (`benchmarks/propagator/history.jsonl`).
- **GIL released around long-running C++ work** via
  `py::gil_scoped_release` in the bindings; concurrent Python work
  makes progress while propagation runs.

## Local-dev setup

### Prerequisites

| Tool | Min version | Install hint |
|---|---|---|
| C++ compiler | GCC 11 / Clang 13 / MSVC 19.30 (VS 2022 17.0+ Build Tools) | per-OS package manager |
| CMake | 3.22 | https://cmake.org/download/ |
| Boost (with `boost-graph`) | 1.81 | see below |
| Ninja (recommended) | any | `apt install ninja-build` / `brew install ninja` / bundled with VS |

### Boost install per OS

- **Linux (Debian/Ubuntu).** `sudo apt install libboost-graph-dev`
  (also pulls `libboost-dev`). CMake's `find_package(Boost)` picks it
  up from the system path with no extra config.
- **macOS.** `brew install boost`. CMake reads `BOOST_ROOT` from
  homebrew automatically.
- **Windows.** Download the matching MSVC prebuilt from
  https://www.boost.org and extract to `C:\local\boost_<version>\`.
  Set `BOOST_ROOT` (e.g.,
  `setx BOOST_ROOT C:\local\boost_1_91_0`) so CMake's
  `find_package(Boost)` resolves it. The repo's CI uses
  `libboost-graph-dev` (Linux); Windows local-dev follows this
  manual-install pattern.

### One-time submodule init

After cloning the repo:

```bash
git submodule update --init --recursive
```

This brings in pybind11 at the version pinned by the project. CMake
`FetchContent` handles GoogleTest, Google Benchmark, and spdlog
automatically at configure time.

### Build + test

```bash
# From scoring/propagator/:
cmake --preset default               # configure (out-of-source build/ dir)
cmake --build --preset default       # build engine + bindings + tests + bench
ctest --preset default               # run GoogleTest suite

# Debug build with sanitizers:
cmake --preset debug
cmake --build --preset debug
ctest --preset debug                 # zero leaks / UB expected
```

Or, from the repo root:

```bash
make build-propagator                # wraps the configure + build steps
```

`uv sync` runs `scikit-build-core` to build the pybind11 bindings
module (`streetsense_propagator`) and install it editable into the
project venv. After a C++ code change in `src/` or `bindings/`,
re-run `uv sync` to pick up the rebuild.

### Lint + format

```bash
clang-format --dry-run --Werror $(git ls-files '*.cc' '*.h')
clang-tidy --warnings-as-errors='*' $(git ls-files '*.cc')
```

Config lives at the repo root (`.clang-format` and `.clang-tidy`,
added in Task 4.1.9).

### Benchmarks

```bash
cmake --build --preset default --target propagator_bench
./build/bench/propagator_bench       # writes JSON to stdout
```

A wrapper script appends the per-run wall-clock to
`benchmarks/propagator/history.jsonl`; the regression check in
`scripts/check_propagator_perf_regression.py` (added in Task 4.8.4)
blocks PRs that regress > 10 %.

## Related

- [ADR 0006 — Propagation Algorithm](../../docs/adr/0006-propagation-algorithm.md)
- [`docs/StreetSense_Architecture.md` §3.3.3 — why this is native](../../docs/StreetSense_Architecture.md)
- [`conductor/code_styleguides/cpp.md` — C++ style guide](../../conductor/code_styleguides/cpp.md)
- [`conductor/tracks/phase-4-propagator/`](../../conductor/tracks/phase-4-propagator/index.md)
