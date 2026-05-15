"""24-call propagator orchestration — Phase 4.6.7.

The Network Risk Propagator runs **once per hour-of-day** on each
scoring run (Technical Note 2 in spec.md). The 24 calls are
independent -- they share the static topology but each carries a
different per-hour input vector (glare(t) + the three time-invariant
scorers). This module fans them out across a ThreadPoolExecutor
sized to a configurable worker count.

The bindings release the GIL during each propagation
(``py::gil_scoped_release`` in bindings/streetsense_propagator.cc),
so a Python ThreadPoolExecutor parallelises usefully here.

Inputs and outputs are pure Python: callers supply the graph
topology, the per-hour input vectors keyed by NodeId, and the
strategy parameters. This module returns a
``{segment_id: list[float]}`` map with one uplift value per hour --
exactly the shape the composite-assembly step (Phase 4.6.8) expects.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Final

import streetsense_propagator

log = logging.getLogger(__name__)


# Default worker count -- the propagator is CPU-bound and releases the
# GIL, so > 1 worker gives real parallelism. 4 is a sane local-dev
# default; CI runners and production scoring boxes can override.
DEFAULT_WORKERS: Final[int] = 4

# Phase 4 ships one production algorithm (chosen by ADR 0006). The
# runner accepts a strategy_id at call time so future algorithms slot
# in without changing this module.
#
# The ADR's in-track benchmark picked ``pagerank-diffusion`` as the
# production strategy on highest correlation + lowest wall-clock; the
# other two registered candidates (``influence-diffusion``,
# ``weighted-shortest-path``) remain registered for posterity and as
# comparison baselines on the next benchmark run.
PHASE_4_DEFAULT_STRATEGY: Final[str] = "pagerank-diffusion"


@dataclass(frozen=True, slots=True)
class PropagationCallInputs:
    """One propagator call's payload (one hour of the 24-way fan-out).

    The graph dict shape matches the bindings module's contract.
    ``hour_index`` is the 0..23 hour-of-day; the orchestrator pairs
    uplift outputs back to their hour for composite assembly.
    """

    hour_index: int
    graph: dict[str, object]  # matches the bindings' GraphData dict shape
    params: dict[str, object]


@dataclass(frozen=True, slots=True)
class PropagationRunResult:
    """Result of the 24-way orchestration.

    ``per_hour_uplift`` is keyed by hour_index -> {segment_id ->
    uplift}. ``per_hour_wall_seconds`` carries per-call elapsed time
    for the structured-logging step the scoring run emits at the end
    of each scoring run (per spec.md AC-6).
    """

    per_hour_uplift: dict[int, dict[int, float]]
    per_hour_wall_seconds: dict[int, float]
    total_wall_seconds: float


def run_24_hourly(
    inputs: Sequence[PropagationCallInputs],
    *,
    strategy_id: str = PHASE_4_DEFAULT_STRATEGY,
    max_workers: int = DEFAULT_WORKERS,
) -> PropagationRunResult:
    """Run the propagator on all 24 hourly inputs and return per-hour uplift maps.

    Order independent: results are reassembled by ``hour_index`` after
    fan-out. ThreadPoolExecutor exploits the binding's GIL-release so
    workers run truly in parallel inside the C++ engine.

    ``strategy_id`` defaults to the ADR 0006 production choice but can
    be overridden for in-track benchmarking (Phase 4.8.1) or for
    future strategies.
    """
    if not inputs:
        return PropagationRunResult(
            per_hour_uplift={},
            per_hour_wall_seconds={},
            total_wall_seconds=0.0,
        )

    per_hour_uplift: dict[int, dict[int, float]] = {}
    per_hour_wall_seconds: dict[int, float] = {}
    t_start_total = time.perf_counter()

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="propagate") as pool:
        future_to_hour = {}
        for call in inputs:
            future = pool.submit(_propagate_one, call, strategy_id)
            future_to_hour[future] = call.hour_index

        for future in as_completed(future_to_hour):
            hour = future_to_hour[future]
            uplift, elapsed = future.result()
            per_hour_uplift[hour] = uplift
            per_hour_wall_seconds[hour] = elapsed

    total_wall_seconds = time.perf_counter() - t_start_total
    log.info(
        "propagator_runner_complete",
        extra={
            "strategy_id": strategy_id,
            "hours_run": len(inputs),
            "max_workers": max_workers,
            "total_wall_seconds": round(total_wall_seconds, 4),
        },
    )
    return PropagationRunResult(
        per_hour_uplift=per_hour_uplift,
        per_hour_wall_seconds=per_hour_wall_seconds,
        total_wall_seconds=total_wall_seconds,
    )


def _propagate_one(
    call: PropagationCallInputs,
    strategy_id: str,
) -> tuple[dict[int, float], float]:
    """One worker's body: call the binding + record wall-clock."""
    t_start = time.perf_counter()
    uplift = streetsense_propagator.propagate(call.graph, strategy_id, call.params)
    elapsed = time.perf_counter() - t_start
    return uplift, elapsed


def assemble_per_segment_hourly_uplift(
    per_hour_uplift: Mapping[int, Mapping[int, float]],
    segment_ids: Sequence[int],
    hours: Sequence[int] = tuple(range(24)),
) -> dict[int, list[float]]:
    """Repack per-hour uplift maps into per-segment hourly time series.

    Output: ``{segment_id: [uplift_h0, uplift_h1, ..., uplift_h23]}``
    -- the shape Phase 4.6.8's composite-assembly step expects.
    Missing entries (a segment that didn't receive any uplift on a
    given hour) default to 0.0.
    """
    result: dict[int, list[float]] = {}
    for segment_id in segment_ids:
        per_hour: list[float] = []
        for hour in hours:
            uplift_map = per_hour_uplift.get(hour, {})
            per_hour.append(uplift_map.get(segment_id, 0.0))
        result[segment_id] = per_hour
    return result


__all__ = [
    "DEFAULT_WORKERS",
    "PHASE_4_DEFAULT_STRATEGY",
    "PropagationCallInputs",
    "PropagationRunResult",
    "assemble_per_segment_hourly_uplift",
    "run_24_hourly",
]
