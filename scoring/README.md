# scoring/

Scoring components. Each scorer implements the per-segment-score interface
defined in Phase 4 — new risk factors are addable by implementing this
interface alone (Extension Point #1).

## Subdirectories

| Path | Phase | Tech |
|---|---|---|
| `environmental/` | 2 | Python (pure-functional glare + weather) |
| `perception/` | 3 | Python + ONNX Runtime (CV pipeline) |
| `propagator/` | 4 | C++17 + Boost.Graph + pybind11 |

## Determinism

Every scoring run must populate the six reproducibility fields enforced at
the database level:

1. `scoring_run_id`
2. `scoring_run_timestamp`
3. `perception_model_version`
4. `osm_snapshot_date`
5. `imagery_capture_window`
6. `propagation_algorithm_version`

Refusal to run is preferable to running without these fields.
