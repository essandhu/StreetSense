# scoring/environmental/

Pure-functional environmental scorers. Phase 2 ships the first concrete
implementation: a glare scorer driven by solar geometry.

## Responsibility

Implement the `scoring.interface.SubScorer` protocol for environmental
factors — currently glare, future post-launch additions may include
weather / cloud cover modulation.

Scorers in this package are:

- **Pure-functional.** Same inputs → same outputs. No I/O, no module-level
  caches, no `datetime.now()` calls.
- **Deterministic.** Property-tested with `hypothesis` for mathematical
  invariants (e.g., symmetry around solar noon for east-west road
  headings).
- **Decoupled from ingestion and storage.** They accept a `ScoringSegment`
  (segment_id + heading + lat/lon) and a `datetime`; they return a
  `SubScoreResult`. The scoring-run orchestration (Phase 2.3) is what
  materializes inputs from `road_segments` and writes outputs to
  `segment_scores`.

## Modules

- `glare.py` — solar-geometry-driven glare exposure score.

## Solar position

`pvlib` per [ADR 0003](../../docs/adr/0003-solar-position-library.md). The
`solar_position(lat, lon, at)` helper wraps `pvlib`'s pandas-flavored API
behind a plain tuple so the rest of the codebase does not import pandas.
