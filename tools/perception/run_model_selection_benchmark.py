"""Model-selection benchmark for Phase 3 perception.

Per ADR 0004's relaxed-validation posture, this script runs F1 against
the synthetic 5-image fixture set in ``tests/fixtures/perception/images/``.
Each image carries a binary ground-truth label for
"lane markings present and well-defined":

| Fixture | Ground truth |
|---|---|
| 01_obvious_lane_markings.png | present |
| 02_faded_lane_markings.png | present |
| 03_no_lane_markings.png | absent |
| 04_obstructed.png | absent (markings exist but not well-defined) |
| 05_lighting_edge_case.png | present |

A model is "right" when its prediction (``value >= 0.5``) matches the
label. F1 is computed in the usual way.

This is a five-sample synthetic validation; the numbers it produces
are *protocol* numbers, not Cambridge F1. ADR 0004's Decision section
records the result with that caveat. Replacing this with a real
Cambridge-labeled validation set is the follow-up trigger.

Usage::

    uv run python tools/perception/run_model_selection_benchmark.py \\
        --model tests/fixtures/perception/standin.onnx \\
        --label standin \\
        --out benchmarks/phase-3/model_selection.json

Append-mode: re-running with a different ``--model`` / ``--label``
adds a new entry to the JSON without dropping prior results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import onnxruntime as ort

from scoring.perception.scorer import _score_one_image, default_preprocess

# Synthetic ground truth, keyed by fixture filename.
_GROUND_TRUTH: dict[str, bool] = {
    "01_obvious_lane_markings.png": True,
    "02_faded_lane_markings.png": True,
    "03_no_lane_markings.png": False,
    "04_obstructed.png": False,
    "05_lighting_edge_case.png": True,
}


def _f1(predictions: dict[str, bool], truth: dict[str, bool]) -> dict[str, float]:
    tp = sum(1 for k, v in predictions.items() if v and truth[k])
    fp = sum(1 for k, v in predictions.items() if v and not truth[k])
    fn = sum(1 for k, v in predictions.items() if not v and truth[k])
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Path to the ONNX model under test.")
    parser.add_argument(
        "--label",
        required=True,
        help="Short label identifying this model (e.g., 'standin' or 'segformer-b0').",
    )
    parser.add_argument(
        "--out",
        default="benchmarks/phase-3/model_selection.json",
        help="Output JSON file (results are appended to a list).",
    )
    parser.add_argument(
        "--images-dir",
        default="tests/fixtures/perception/images",
        help="Directory of fixture images (default: the Phase 3 synthetic set).",
    )
    parser.add_argument(
        "--decision-threshold",
        type=float,
        default=0.5,
        help="Score >= threshold predicts 'lane markings present' (default: 0.5).",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    model_path = repo_root / args.model
    images_dir = repo_root / args.images_dir
    out_path = repo_root / args.out

    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])

    per_image_results: list[dict[str, Any]] = []
    predictions: dict[str, bool] = {}
    t0 = time.perf_counter()
    for fixture_name, truth in sorted(_GROUND_TRUTH.items()):
        image_path = images_dir / fixture_name
        score = _score_one_image(
            session,
            image_path.read_bytes(),
            preprocess=default_preprocess,
        )
        prediction = score.value >= args.decision_threshold
        predictions[fixture_name] = prediction
        per_image_results.append(
            {
                "fixture": fixture_name,
                "ground_truth": truth,
                "predicted_value": score.value,
                "predicted_uncertainty": score.uncertainty,
                "prediction": prediction,
                "match": prediction == truth,
            }
        )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    metrics = _f1(predictions, _GROUND_TRUTH)
    digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
    record: dict[str, Any] = {
        "label": args.label,
        "model_path": str(model_path.relative_to(repo_root)),
        "model_sha256": digest,
        "model_size_bytes": model_path.stat().st_size,
        "decision_threshold": args.decision_threshold,
        "elapsed_ms_total": round(elapsed_ms, 3),
        "ms_per_image": round(elapsed_ms / len(_GROUND_TRUTH), 3),
        "f1": round(metrics["f1"], 4),
        "precision": round(metrics["precision"], 4),
        "recall": round(metrics["recall"], 4),
        "per_image": per_image_results,
        "note": (
            "Synthetic 5-image validation per ADR 0004. F1 here is a "
            "protocol number, not a Cambridge F1. Real-Cambridge validation "
            "is the follow-up trigger."
        ),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict[str, Any]] = []
    if out_path.exists():
        existing = json.loads(out_path.read_text(encoding="utf-8"))
        if not isinstance(existing, list):
            existing = [existing]
    existing = [e for e in existing if e.get("label") != args.label]
    existing.append(record)
    out_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
