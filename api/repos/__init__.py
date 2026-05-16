"""Repository layer for the FastAPI service (Phase 5+).

Phase 1-4 queries lived inline in route handlers because each endpoint
issued one or two simple queries. Phase 5's delta endpoint needs a
single non-trivial paginated JOIN across two scoring_runs at a chosen
hour-of-day, with a separate ``count(*)`` for the response's
``total`` field. That pair of queries lives here so the route stays
thin and the SQL is independently testable.
"""
