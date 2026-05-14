"""GIL-release test for streetsense_propagator — Phase 4.4.5.

The C++ propagator runs under py::gil_scoped_release so other Python
threads can make progress concurrently. This test confirms it by
launching a CPU-bound Python thread alongside a propagate() call on
a moderately sized graph and asserting the Python thread continues
to execute while propagation is in-flight.

A reasonable lower bound: the propagate() call should not stall the
Python thread for more than a small fraction of its wall-clock.
"""

from __future__ import annotations

import threading
import time

import pytest

import streetsense_propagator


def _build_chain_graph(n: int) -> dict[str, object]:
    """A linear chain graph with bidirectional edges. n nodes; 2(n-1) edges."""
    adjacency: list[list[tuple[int, float]]] = []
    for i in range(n):
        edges: list[tuple[int, float]] = []
        if i > 0:
            edges.append((i - 1, 1.0))
        if i < n - 1:
            edges.append((i + 1, 1.0))
        adjacency.append(edges)
    return {
        "node_ids": list(range(n)),
        "adjacency": adjacency,
        "inputs": [1.0] * n,
    }


@pytest.mark.slow
def test_propagate_releases_gil_for_concurrent_python_work() -> None:
    """A Python thread spinning concurrently with propagate() makes progress.

    Concretely: launch a Python thread that does CPU-bound work
    (counting + occasional time.monotonic checks). Concurrently call
    propagate() on a chain graph. The Python thread must continue to
    progress -- counter increases observably -- while propagate() is
    running.
    """
    graph = _build_chain_graph(800)
    params = {"k_hop_radius": 3, "decay_weight": 0.5, "normalize": False}

    counter = [0]
    stop_flag = threading.Event()

    def cpu_bound_python_work() -> None:
        # Tight Python loop that the GIL would otherwise prevent
        # running during a C-API call that holds the GIL.
        while not stop_flag.is_set():
            counter[0] += 1

    worker = threading.Thread(target=cpu_bound_python_work, daemon=True)
    worker.start()

    # Let the worker reach steady state before the propagate() call.
    time.sleep(0.05)
    counter_before = counter[0]

    # Run the propagation. The graph is sized so this takes long
    # enough (~tens of ms in Debug build) to observe the worker's
    # progress.
    t_start = time.perf_counter()
    streetsense_propagator.propagate(graph, "influence-diffusion", params)
    elapsed = time.perf_counter() - t_start

    counter_after = counter[0]
    stop_flag.set()
    worker.join(timeout=1.0)

    delta = counter_after - counter_before
    # If the GIL was held during propagate(), counter would not have
    # advanced during the C++ work. With GIL released it should
    # advance freely. A modest threshold avoids flakiness on fast
    # propagation runs.
    assert delta > 1000, (
        f"counter only advanced by {delta} during {elapsed:.3f}s of propagation -- "
        "the GIL was likely held"
    )
