# `scoring/perception/`

Phase 3's perception scorer. Implements the `SubScorer` protocol from
`scoring.interface` with a model-agnostic ONNX Runtime inference path
fed by Mapillary-sourced street-level imagery.

## Layout

| File | Role |
|---|---|
| `scorer.py` | `PerceptionScorer` — `SubScorer` for `lane_marking_quality` (Task 3.3.5) |
| `aggregation.py` | Pure aggregation: per-image scores → per-segment `value` + `model_uncertainty` (Task 3.3.4, property-tested) |
| `preprocess.py` | Pillow-based image preprocessing for the ONNX session (Task 3.3.5) |
| `scorer_test.py` | Unit tests against the stand-in ONNX model + fixture images |
| `aggregation_test.py` | Hypothesis property tests on the aggregation function |

## Extension-point posture

Adding a sub-score in a later phase (e.g., sign detection, surface
condition) means a new module here implementing `SubScorer`. The scoring
run, persistence, and tile pipeline do not change — that's extension
point 1 (`CLAUDE.md`).

See `docs/adr/0004-perception-model.md` for the model-selection protocol
and the F1 floor below which fine-tuning is reopened.
