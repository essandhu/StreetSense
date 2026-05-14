# `ingestion/imagery/`

Street-level imagery ingestion. The package realizes **extension point 3**
(`CLAUDE.md`): provider implementations live behind the
`ImageryProvider(Protocol)` seam in `provider.py`, and callers
(`ingestion/imagery/job.py`, `scoring/perception/`, the API) see only the
protocol.

## Modules

| File | Role |
|---|---|
| `provider.py` | `ImageryProvider` protocol + `Waypoint` / `ImageryReference` value types |
| `mapillary.py` | Phase 3's concrete provider (lands in Task 3.2.5) |
| `job.py` | Provider-agnostic ingestion pipeline: waypoint generation, fetch, MinIO upload, `segment_imagery` row write (lands in Task 3.2.7) |
| `cassettes/` | `vcrpy` cassettes recorded against Mapillary (committed; CI uses these, no live network) |

## Extension-point check

A second provider (KartaView, fixture-only, anything) drops in by adding a
sibling module implementing `ImageryProvider`. The job in `job.py` and
every downstream caller stay untouched. Reviewers verify this by inspection
during Phase 3.2.

See `docs/adr/0005-imagery-provider.md` for the Mapillary selection and
the conditions under which a second provider would land.
