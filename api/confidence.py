"""Confidence-indicator assembly — spec Tech Note 4.

The unified per-segment confidence indicator combines three signals
via a min-rule so the limiting input is identifiable at a glance:

    confidence = min(freshness, coverage, 1 - model_uncertainty)
    limiter    = argmin(...)

This module is **pure-functional**: every output is a deterministic
function of its inputs, no I/O, no global state. The API handler
(``api/routes/segments.py``) sources the inputs from Postgres /
MinIO / scoring metadata, then calls ``assemble``.

Why a min-rule (not multiplicative, not weighted-mean):

- Conservative — a single weak input drags the indicator down.
- Surfaces the limiting input directly (the UI labels *why*).
- No calibration burden vs. weighted-mean.

See ``conductor/tracks/phase-3-perception-scorer/spec.md`` Tech Note 4
for the full rationale.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final, Literal

LimiterName = Literal["freshness", "coverage", "model"]


@dataclass(frozen=True, slots=True)
class ConfidenceIndicator:
    """Per-segment confidence + the input that limited it."""

    value: float
    limiter: LimiterName


# Defaults from spec Tech Note 4. Configurable per scoring-run config
# only if a future need arises; not exposed to the API surface today.
_FULL_CREDIT_DAYS: Final[int] = 180
_ZERO_DAYS: Final[int] = 1080


def freshness(
    capture_date_max: date,
    *,
    now: date,
    full_credit_days: int = _FULL_CREDIT_DAYS,
    zero_days: int = _ZERO_DAYS,
) -> float:
    """Linearly decay from 1.0 (within full-credit window) to 0.0 (past zero point).

    - ``age_days <= full_credit_days`` ⇒ ``1.0``.
    - ``age_days >= zero_days``         ⇒ ``0.0``.
    - In between: linear interpolation.
    """
    if full_credit_days >= zero_days:
        raise ValueError("full_credit_days must be strictly less than zero_days")
    age_days = (now - capture_date_max).days
    if age_days <= full_credit_days:
        return 1.0
    if age_days >= zero_days:
        return 0.0
    span = zero_days - full_credit_days
    return float(1.0 - (age_days - full_credit_days) / span)


def coverage(actual_samples: int, target_samples: int) -> float:
    """Ratio of actual to target imagery samples, clamped to ``[0, 1]``."""
    if target_samples <= 0:
        raise ValueError("target_samples must be positive")
    if actual_samples <= 0:
        return 0.0
    ratio = actual_samples / target_samples
    return float(min(1.0, ratio))


# Order matters for the deterministic tie-break in `assemble`.
_LIMITER_PRIORITY: Final[tuple[LimiterName, ...]] = ("freshness", "coverage", "model")


def assemble(
    freshness_value: float,
    coverage_value: float,
    model_uncertainty: float,
) -> ConfidenceIndicator:
    """Combine the three signals via min-rule with a deterministic tie-break.

    Tie-break order: ``freshness`` > ``coverage`` > ``model``. So if
    freshness == coverage and both are the smallest, ``limiter`` is
    ``"freshness"`` — alphabetical-style stable ordering documented for
    consumers (the UI's label legend follows the same order).

    Inputs are clamped to ``[0, 1]`` for safety but not asserted —
    callers are expected to pass in-range values. The clamp protects
    against subtle floating-point drift (e.g., 1.0000001 from a
    division).
    """
    f = max(0.0, min(1.0, freshness_value))
    c = max(0.0, min(1.0, coverage_value))
    m = max(0.0, min(1.0, model_uncertainty))
    candidates = {
        "freshness": f,
        "coverage": c,
        "model": 1.0 - m,
    }
    # Stable argmin via the priority tuple.
    limiter = min(_LIMITER_PRIORITY, key=lambda k: candidates[k])
    return ConfidenceIndicator(value=candidates[limiter], limiter=limiter)


__all__ = [
    "ConfidenceIndicator",
    "LimiterName",
    "assemble",
    "coverage",
    "freshness",
]
