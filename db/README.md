# db/

Alembic migrations and seed data for PostgreSQL 16 + PostGIS 3.4.

## Conventions

- Migrations are **forward-only**. No `downgrade` body — `raise NotImplementedError`.
- Every table with geospatial columns gets a `GIST` index.
- Every table queried by time gets a `BTREE` index on the timestamp column.
- `scoring_runs` and `segment_scores` are append-only — `REVOKE UPDATE, DELETE`
  on the application role inside the migration that creates them.
- UUID primary keys via `gen_random_uuid()`. Never `SERIAL` / `BIGSERIAL`.
- Geometries: `geometry(LineString, 4326)`. WGS84 (EPSG:4326) is canonical
  storage SRID.

## Layout

```
db/
├── migrations/    # Alembic versioned migrations
└── seeds/         # Reference data inserts (e.g., default data_sources rows)
```

## Running

```bash
alembic upgrade head        # apply pending migrations
alembic current             # show applied revision
alembic revision -m "name"  # create new migration (forward-only)
```
