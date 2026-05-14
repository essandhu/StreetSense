# ingestion/incidents/

Historical incident ingestion — Phase 4's incident-dataset adapter
package. Mirrors `ingestion/imagery/` in shape (the
extension-point-3-style protocol pattern, see
[ADR 0005](../../docs/adr/0005-imagery-provider.md) for the analogous
imagery pattern and [ADR 0007](../../docs/adr/0007-incident-dataset.md)
for this package's dataset choice).

## What this package does

Loads historical incident records (geocoded crashes / reportable
incidents) for the configured city's bounding box into the `incidents`
PostgreSQL table (migration 0013, Phase 4.5.3). The
historical-correlation scorer (`scoring/historical/`) reads from that
table and computes per-segment proximity to historic incident density.

## Extension-point seam

`IncidentProvider(Protocol)` lives in [`provider.py`](./provider.py).
A concrete adapter for the dataset chosen by ADR 0007 lands in a
sibling module (e.g., `massdot_impact.py` or `cambridge_open_data.py`,
depending on the in-track evaluation outcome). Adding a second
provider in a future track is a new sibling module + a config switch
in `job.py` — no caller changes.

## Phase 4 status

- **Phase 4.1:** empty scaffold (this README + `__init__.py` +
  `provider.py`).
- **Phase 4.5.1:** ADR 0007 finalized — the chosen dataset is filled
  in.
- **Phase 4.5.2:** `IncidentProvider(Protocol)` + value types
  (`IncidentRecord`, severity enum) land here.
- **Phase 4.5.3:** migration `0013_incidents_table.py` creates the
  PostGIS-indexed `incidents` table.
- **Phase 4.5.4:** vcrpy cassettes capture the chosen provider's API
  responses for offline CI.
- **Phase 4.5.5:** concrete adapter implementation.
- **Phase 4.5.6:** `ingestion/incidents/job.py` — the idempotent
  ingestion job that upserts into the `incidents` table.
- **Phase 4.5.7:** `make ingest-incidents` Makefile target.

## Related

- [ADR 0007 — Historical Incident Dataset](../../docs/adr/0007-incident-dataset.md)
- [`ingestion/imagery/`](../imagery/README.md) — the analogous
  Phase 3 extension-point-3 package
- [`scoring/historical/`](../../scoring/historical/README.md) — the
  consumer of this package's data
- [`conductor/tracks/phase-4-propagator/`](../../conductor/tracks/phase-4-propagator/index.md)
