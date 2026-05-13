# tests/fixtures/

Small, committed fixtures used by unit and integration tests. No live
network calls in CI — these are the canonical inputs.

| File | Used by | Notes |
|---|---|---|
| `tiny_extract.osm` | `tests/ingestion/test_osm_adapter.py` | 3 highway ways inside a tight bbox, 1 outside (clip test), 1 building (filter test). |
