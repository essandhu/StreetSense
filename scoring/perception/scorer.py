"""Perception scorer — ONNX-served lane-marking quality.

Implementation lands with Task 3.3.5 alongside its fixture-image unit
tests and Hypothesis property tests for the aggregation function. This
module is intentionally empty in Phase 3.1 — its presence signals the
package shape and makes ``mypy --strict scoring/perception/`` succeed.

The public type added in Phase 3.3 is ``PerceptionScorer`` (a
``SubScorer`` implementation). The constructor takes a model artifact
path, an ``onnxruntime.InferenceSession`` factory, and a preprocessing
function — preserving the model-agnostic posture recorded in ADR 0004.
"""

from __future__ import annotations
