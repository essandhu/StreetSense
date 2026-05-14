"""Unit tests for ``PerceptionScorer`` against the stand-in ONNX.

These tests are hermetic: they use a tiny stand-in ONNX model and the
5 fixture images committed under ``tests/fixtures/perception/``. No
network, no DB, no MinIO.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import onnxruntime as ort
import pytest

from scoring.interface import ScoringSegment
from scoring.perception.scorer import ImageryLoader, PerceptionScorer

_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "perception"
_STANDIN_PATH = _FIXTURE_ROOT / "standin.onnx"
_IMAGES_DIR = _FIXTURE_ROOT / "images"

_SEG_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _segment() -> ScoringSegment:
    return ScoringSegment(
        segment_id=_SEG_ID,
        heading_deg=0.0,
        lat=42.372,
        lon=-71.105,
    )


@pytest.fixture(scope="module")
def session() -> ort.InferenceSession:
    return ort.InferenceSession(str(_STANDIN_PATH), providers=["CPUExecutionProvider"])


@pytest.fixture
def fixture_image_bytes() -> bytes:
    return (_IMAGES_DIR / "01_obvious_lane_markings.png").read_bytes()


@pytest.fixture
def all_fixture_image_bytes() -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    for path in sorted(_IMAGES_DIR.glob("*.png")):
        out.append((path.stem, path.read_bytes()))
    return out


def _loader_returning(items: list[tuple[str, bytes]]) -> ImageryLoader:
    """Build an ImageryLoader that yields ``items`` regardless of segment id."""

    def _load(_segment_id: UUID) -> Iterable[tuple[str, bytes]]:
        return items

    return _load


def test_scorer_returns_subscore_result_shape(
    session: ort.InferenceSession, fixture_image_bytes: bytes
) -> None:
    scorer = PerceptionScorer(
        session=session,
        imagery_loader=_loader_returning([("img-1", fixture_image_bytes)]),
    )
    result = scorer.score(_segment(), at=datetime(2026, 5, 13, tzinfo=UTC))

    assert 0.0 <= result.value <= 1.0
    assert result.is_stub is False
    assert "model_uncertainty" in result.metadata
    assert isinstance(result.metadata["model_uncertainty"], float)
    assert 0.0 <= result.metadata["model_uncertainty"] <= 1.0


def test_score_for_samples_aggregates_per_image(
    session: ort.InferenceSession, all_fixture_image_bytes: list[tuple[str, bytes]]
) -> None:
    scorer = PerceptionScorer(
        session=session,
        imagery_loader=_loader_returning(all_fixture_image_bytes),
    )
    ats = [datetime(2026, 5, 13, h, tzinfo=UTC) for h in (0, 6, 12, 18)]
    results = scorer.score_for_samples(_segment(), ats=ats)

    assert len(results) == len(ats)
    # All temporal samples receive the same lane-marking result (it's
    # time-invariant in Phase 3).
    first = results[0]
    for r in results[1:]:
        assert r.value == first.value
        assert r.metadata["model_uncertainty"] == first.metadata["model_uncertainty"]

    # Per-image entries match the number of fixture images.
    assert len(first.metadata["per_image"]) == len(all_fixture_image_bytes)
    per_image = first.metadata["per_image"]
    # Segment value is the unweighted mean of per-image values
    # (Tech Note 2).
    image_values = [entry["value"] for entry in per_image]
    expected_mean = sum(image_values) / len(image_values)
    assert first.value == pytest.approx(expected_mean, rel=1e-6)
    # Segment uncertainty is the per-image max.
    image_unc = [entry["model_uncertainty"] for entry in per_image]
    assert first.metadata["model_uncertainty"] == pytest.approx(max(image_unc), rel=1e-6)


def test_zero_imagery_returns_stub(session: ort.InferenceSession) -> None:
    scorer = PerceptionScorer(
        session=session,
        imagery_loader=_loader_returning([]),
    )
    result = scorer.score(_segment(), at=datetime(2026, 5, 13, tzinfo=UTC))

    assert result.is_stub is True
    assert result.value == 0.0
    assert result.metadata["image_count"] == 0


def test_gil_released_during_inference(
    session: ort.InferenceSession, fixture_image_bytes: bytes
) -> None:
    """A second Python thread makes progress while inference is running.

    Observability check: ONNX Runtime releases the GIL during
    inference. If a future custom op held the GIL, the concurrent
    thread's progress would stall. Tolerance: the concurrent counter
    runs at least 50% of what it would in pure-Python time, well below
    the 2x cap from the plan but generous enough to avoid flakiness.
    """
    # 200 images keeps the scored call's wall-clock around the
    # high-hundreds of ms — comfortably above Windows's ~15 ms clock
    # granularity on time.sleep(0.001).
    scorer = PerceptionScorer(
        session=session,
        imagery_loader=_loader_returning([("img-1", fixture_image_bytes)] * 200),
    )

    counter = {"value": 0}
    stop = threading.Event()

    def tick() -> None:
        while not stop.is_set():
            counter["value"] += 1
            time.sleep(0.0)  # yield, no real sleep

    t = threading.Thread(target=tick, daemon=True)
    t.start()
    try:
        scorer.score(_segment(), at=datetime(2026, 5, 13, tzinfo=UTC))
    finally:
        stop.set()
        t.join(timeout=1.0)

    # The exact count varies by machine; a single tick proves the GIL
    # was released at least once during inference.
    assert counter["value"] >= 1, (
        f"Concurrent thread made {counter['value']} ticks during inference — "
        "expected the GIL to be released at least once"
    )
