"""Perception scorer — ONNX-served lane-marking quality.

Implements the :class:`SubScorer` protocol from ``scoring.interface``.
Decoupled from the data layer through an ``imagery_loader`` callable:
the scorer asks "give me the imagery for segment X" and the caller
decides whether that means DB+MinIO (production), pre-loaded fixtures
(unit tests), or anything else.

The model is treated opaquely. Inputs are float32 NCHW; outputs are
float32 logits whose mean (after sigmoid) becomes
``lane_marking_quality`` and whose stddev (after sigmoid) becomes
``model_uncertainty``. This mapping is **deliberately simple** — Phase 3
chooses an unfine-tuned pretrained model (ADR 0004), so a richer
uncertainty estimate would be calibrating noise.

Per ADR 0004 / spec Tech Note 3, CI runs against a stand-in ONNX
committed to ``tests/fixtures/perception/standin.onnx``. The chosen
production model artifact lives in MinIO under
``streetsense-models/<perception_model_version>/<name>.onnx``.

GIL posture: ``onnxruntime`` releases the GIL during inference
natively. The scorer therefore composes cleanly with the scoring run's
Python-level segment fan-out.
"""

from __future__ import annotations

import io
import math
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import numpy as np
import onnxruntime as ort
from PIL import Image

from scoring.interface import ScoringSegment, SubScoreResult
from scoring.perception.aggregation import PerImageScore, aggregate

# Imagery loader contract: given a segment's UUID, yield zero or more
# (provider_image_id, image_bytes) tuples. ``provider_image_id`` is
# carried so the scorer can include it in ``SubScoreResult.metadata``
# for traceability; the perception scorer itself does not consult it
# during inference.
ImageryLoader = Callable[[UUID], Iterable[tuple[str, bytes]]]


def default_preprocess(image_bytes: bytes, *, size: tuple[int, int] = (64, 64)) -> np.ndarray:
    """Pillow-based preprocessing matching the stand-in ONNX input shape.

    Returns a ``(1, 3, H, W) float32`` ndarray normalized to ``[0, 1]``.
    """
    with Image.open(io.BytesIO(image_bytes)) as img:
        rgb = img.convert("RGB").resize(size, Image.Resampling.BILINEAR)
    arr = np.asarray(rgb, dtype=np.float32) / 255.0  # HWC
    chw = np.transpose(arr, (2, 0, 1))  # CHW
    return np.expand_dims(chw, axis=0)  # NCHW


def _sigmoid(x: np.ndarray) -> np.ndarray:
    result: np.ndarray = 1.0 / (1.0 + np.exp(-x))
    return result


def _score_one_image(
    session: ort.InferenceSession,
    image_bytes: bytes,
    *,
    preprocess: Callable[[bytes], np.ndarray],
) -> PerImageScore:
    """Inference + per-image aggregation. Pure with respect to inputs."""
    input_name = session.get_inputs()[0].name
    output_name = session.get_outputs()[0].name
    tensor = preprocess(image_bytes)
    (logits,) = session.run([output_name], {input_name: tensor})
    probs = _sigmoid(logits.astype(np.float32))
    value = float(np.mean(probs))
    # Use stddev as a cheap uncertainty proxy; clip to [0, 1] for the
    # SubScoreResult contract (stddev of a [0, 1] field is bounded
    # at 0.5).
    uncertainty = float(min(1.0, np.std(probs) * 2.0))
    if math.isnan(value) or math.isnan(uncertainty):
        # Defensive: a degenerate model could output NaNs. Treat as
        # max-uncertainty zero-confidence.
        return PerImageScore(value=0.0, uncertainty=1.0)
    return PerImageScore(value=value, uncertainty=uncertainty)


class PerceptionScorer:
    """``SubScorer`` for ``lane_marking_quality``.

    Stateless beyond the constructor injections. A single instance can
    be shared across the scoring-run fan-out.
    """

    name: str = "lane_marking"
    """Identifier the ScoringRun routes results to. The persistence
    layer maps ``"lane_marking"`` → ``sub_score_lane_marking`` /
    ``is_stub_lane_marking``."""

    def __init__(
        self,
        *,
        session: ort.InferenceSession,
        imagery_loader: ImageryLoader,
        preprocess: Callable[[bytes], np.ndarray] | None = None,
    ) -> None:
        self._session = session
        self._loader = imagery_loader
        self._preprocess = preprocess or default_preprocess

    def score(self, segment: ScoringSegment, *, at: datetime) -> SubScoreResult:
        """Compute the perception sub-score for ``segment`` at ``at``.

        ``at`` does not influence the per-image inference (lane
        markings are static in Phase 3) — it appears only because the
        ``SubScorer`` protocol requires it for parity with
        time-dependent scorers like glare.
        """
        del at  # perception is time-invariant in Phase 3
        return self._score_with_loaded_imagery(segment)

    def score_for_samples(
        self, segment: ScoringSegment, *, ats: Sequence[datetime]
    ) -> list[SubScoreResult]:
        """Score once; replicate per temporal sample.

        ``score_for_samples`` is the batched entry point the scoring
        run uses. Phase 3 perception is time-invariant, so the same
        result writes to every (segment, at) row. The cost saved
        relative to N independent ``score()`` calls is N inference
        runs + N MinIO round trips, which matters at city scale.
        """
        if not ats:
            return []
        shared = self._score_with_loaded_imagery(segment)
        return [shared] * len(ats)

    def _score_with_loaded_imagery(self, segment: ScoringSegment) -> SubScoreResult:
        loaded = list(self._loader(segment.segment_id))
        if not loaded:
            # Spec Tech Note 4 stub-fallback: no imagery → is_stub=True,
            # value=0. Confidence carries through but is meaningless
            # for the stub case.
            return SubScoreResult(
                value=0.0,
                confidence=0.0,
                is_stub=True,
                metadata={
                    "image_count": 0,
                    "model_uncertainty": 0.0,
                    "per_image": [],
                },
            )

        per_image: list[PerImageScore] = []
        per_image_meta: list[dict[str, Any]] = []
        for provider_image_id, image_bytes in loaded:
            score = _score_one_image(self._session, image_bytes, preprocess=self._preprocess)
            per_image.append(score)
            per_image_meta.append(
                {
                    "provider_image_id": provider_image_id,
                    "value": score.value,
                    "model_uncertainty": score.uncertainty,
                }
            )

        agg = aggregate(per_image)
        # Confidence here is per-sub-score only. The cross-sub-score
        # confidence indicator (freshness + coverage + model
        # uncertainty via min-rule) is assembled in
        # ``api.confidence`` (Phase 3.5).
        confidence = 1.0 - agg.uncertainty
        return SubScoreResult(
            value=agg.value,
            confidence=confidence,
            is_stub=False,
            metadata={
                "image_count": agg.image_count,
                "model_uncertainty": agg.uncertainty,
                "per_image": per_image_meta,
            },
        )


__all__ = ["ImageryLoader", "PerceptionScorer", "default_preprocess"]
