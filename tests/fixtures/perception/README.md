# `tests/fixtures/perception/`

CI-deterministic perception fixtures.

## Files

| Path | Provenance |
|---|---|
| `standin.onnx` | A 422-byte single-`Conv` ONNX graph mapping `(1, 3, 64, 64) → (1, 1, 64, 64)`. Built by `tools/perception/build_standin_onnx.py`. The output is *not* meaningful — the model exists so the perception scorer's ONNX-Runtime path can run hermetically. |
| `images/01_obvious_lane_markings.png` | Synthetic 128×128 RGB. Black asphalt, two solid white dashed lane stripes. |
| `images/02_faded_lane_markings.png` | Same layout, low-contrast / faded stripes. |
| `images/03_no_lane_markings.png` | Uniform asphalt; no stripes. |
| `images/04_obstructed.png` | Asphalt + bright stripes with a large opaque overlay (occlusion). |
| `images/05_lighting_edge_case.png` | Asphalt + stripes + a shadow band crossing the road. |

## Why synthetic

Per ADR 0004 (the relaxed-validation posture), Phase 3 does not budget
for a hand-labeled Cambridge validation set. These five hand-built
images stand in as both the perception unit test inputs *and* the
synthetic validation set for the model-selection benchmark
(Task 3.3.6). The intent is for the perception scorer's outputs to be
*interpretable* against these inputs without needing real-world
ground truth: a higher score on `01_obvious_lane_markings.png` than
on `03_no_lane_markings.png` is the kind of monotonicity these
fixtures support.

## Regeneration

Both the stand-in ONNX and the images are byte-deterministic:

```bash
uv run --group onnx-tools python tools/perception/build_standin_onnx.py
uv run python tools/perception/build_fixture_images.py
```

If either tool changes, re-run and commit the resulting artifacts.
