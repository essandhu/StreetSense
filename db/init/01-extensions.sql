-- Bootstrap extensions required by the StreetSense schema.
-- Runs once on first container start via docker-entrypoint-initdb.d.
--
-- The postgis/postgis image already creates the postgis extension, but we
-- explicitly require pgcrypto for gen_random_uuid() and (idempotently) ensure
-- postgis is present. Subsequent schema work happens in Alembic migrations.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
