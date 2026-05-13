# tests/

Cross-cutting integration tests. Component-local unit tests live **alongside
the code they test**, not here.

## What lives here

- `tests/db/` — schema invariant tests (run against a real Postgres+PostGIS).
- `tests/ingestion/` — end-to-end ingestion tests with recorded fixtures.
- Phase 2+: cross-component property tests, regression suites.

## Discipline

- No live network calls in CI — use recorded fixtures (`vcrpy` or
  committed small PBFs).
- Bug fixes land with a regression test that fails without the fix.
