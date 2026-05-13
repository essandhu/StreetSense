# api/

FastAPI service exposing per-segment detail, vector tile URLs (via
`pg_tileserv`), and the `/admin/freshness` endpoint.

## Conventions

- Async throughout.
- Pydantic models for every request/response shape.
- Sub-score fields are first-class — every composite-risk response carries
  the four sub-score fields and a confidence indicator. No collapsing to a
  single opaque number, ever.
- UUIDs as branded types where they cross the API boundary.
- Stable, additive response shapes — Phase 2/3/4 fill in stub values without
  breaking changes.

## Phase 1 endpoints

| Endpoint | Purpose |
|---|---|
| `GET /segments/{id}` | Per-segment detail with stub sub-scores (Phase 1) |
| `GET /admin/freshness` | Latest ingestion timestamp per data source |
| Tile URL | Served by `pg_tileserv` (see `docker-compose.yml`) |
