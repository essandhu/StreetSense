# ingestion/

Python ingestion adapters. Each external data source sits behind a `Protocol`
in its own module so future providers slot in without caller changes.

## Phase 1

- `osm/` — OpenStreetMap PBF ingestion via `pyrosm`. First concrete
  `OSMSource` implementation.
- `persist.py` — Transactional write path to PostGIS.

## Future phases

- Phase 2: solar geometry source (computed, not fetched).
- Phase 3: imagery adapters (Mapillary, KartaView, …).
- Phase 4: incident-data adapters.

Adapters never call live networks in CI tests — use recorded fixtures
(`vcrpy` or stored PBFs).
