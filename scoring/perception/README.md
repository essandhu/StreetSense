# scoring/perception/

CV pipeline + ONNX inference. **Phase 3 — not populated in Phase 1.**

## Phase 3 plan

- Thin Python wrapper over ONNX Runtime. Model artifact is swappable.
- Records `perception_model_version` (semver or git SHA) on every score row.
- Imagery providers sit behind `ImagerySource(Protocol)` (Extension Point #3).
