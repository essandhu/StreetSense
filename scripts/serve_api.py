"""Launcher for the FastAPI service that pins the asyncio event loop.

Why this exists
---------------
On Windows, the default ``asyncio`` event loop is ``ProactorEventLoop``,
which is incompatible with ``psycopg``'s async connection pool
(``psycopg.InterfaceError: Psycopg cannot use the 'ProactorEventLoop'
to run in async mode``).

A bare ``asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy)``
in ``api/main.py`` is not enough: uvicorn (as of 0.36+) **hardcodes**
``asyncio.ProactorEventLoop`` for its built-in ``asyncio`` loop factory
on Windows (see ``uvicorn/loops/asyncio.py::asyncio_loop_factory``).
The event-loop policy is ignored.

This launcher passes a custom ``loop`` import path to
``uvicorn.Config`` so uvicorn calls *our* factory, which always
returns ``SelectorEventLoop`` on Windows. Pool connections then
succeed; ``/admin/freshness`` and friends return 200.

Usage::

    uv run python -m scripts.serve_api [--reload] [--host 0.0.0.0] [--port 8000]

Or set via env: ``UVICORN_HOST``, ``UVICORN_PORT``, ``UVICORN_RELOAD``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    """Custom uvicorn loop factory: always SelectorEventLoop on Windows.

    Uvicorn's built-in ``asyncio_loop_factory`` returns
    ``ProactorEventLoop`` on Windows. Psycopg-pool refuses to talk to
    that. This factory replaces it.
    """
    if sys.platform == "win32":
        # Set the policy too so anything else in the process that calls
        # `asyncio.get_event_loop_policy().new_event_loop()` also gets
        # the right kind.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        return asyncio.SelectorEventLoop()
    # Non-Windows: stick with the platform default.
    return asyncio.new_event_loop()


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("UVICORN_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("UVICORN_PORT", "8000")))
    parser.add_argument(
        "--reload",
        action="store_true",
        default=os.environ.get("UVICORN_RELOAD", "").lower() in {"1", "true", "yes"},
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("UVICORN_LOG_LEVEL", "info"),
        choices=["critical", "error", "warning", "info", "debug", "trace"],
    )
    args = parser.parse_args(argv)

    uvicorn.run(
        "api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
        # Custom factory: see selector_loop_factory above. Uvicorn
        # imports this dotted path and calls it once per worker.
        loop="scripts.serve_api:selector_loop_factory",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
