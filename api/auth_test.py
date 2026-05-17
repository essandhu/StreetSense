"""Tests for the basic-auth middleware (Task 1.5).

Tests are written **before** the middleware per the plan. The auth
gate is opt-in via the ``STREETSENSE_BASIC_AUTH`` env var (format:
``"user:bcrypt-hash"``). When the env var is unset, no auth is
enforced — the dev workflow stays frictionless. When set, every
request except ``/health`` (load-balancer probe path) must carry a
valid ``Authorization: Basic ...`` header.

The exemption list is intentionally tiny: only ``/health``. The Fly
healthcheck (``fly.toml`` ``http_service.checks``) hits this path
without credentials; if we required auth here, Fly's edge would
declare the Machine unhealthy and refuse to route traffic.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Iterator

import bcrypt
import pytest
from fastapi.testclient import TestClient

from api.main import create_app

_ENV = "STREETSENSE_BASIC_AUTH"


def _make_creds_env(username: str = "admin", password: str = "correct-horse") -> str:
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=4)).decode("utf-8")
    return f"{username}:{hashed}"


def _basic_header(username: str, password: str) -> dict[str, str]:
    raw = f"{username}:{password}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


@pytest.fixture
def _clear_auth_env() -> Iterator[None]:
    prior = os.environ.pop(_ENV, None)
    try:
        yield
    finally:
        if prior is not None:
            os.environ[_ENV] = prior
        else:
            os.environ.pop(_ENV, None)


# ---------- auth disabled (env unset) ---------------------------------------


@pytest.mark.usefixtures("_clear_auth_env")
def test_no_env_var_no_auth_enforcement() -> None:
    """Default dev mode — every route is accessible without credentials."""
    app = create_app()
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200


# ---------- auth enabled (env set) ------------------------------------------


@pytest.mark.usefixtures("_clear_auth_env")
def test_auth_enabled_missing_header_returns_401() -> None:
    os.environ[_ENV] = _make_creds_env()
    app = create_app()
    client = TestClient(app)
    response = client.get("/api/cities/cambridge/runs")
    assert response.status_code == 401
    # The WWW-Authenticate header is what cues the browser's
    # native basic-auth prompt; without it the user sees only a
    # blank 401 page.
    assert response.headers.get("www-authenticate", "").lower().startswith("basic")


@pytest.mark.usefixtures("_clear_auth_env")
def test_auth_enabled_correct_credentials_pass() -> None:
    os.environ[_ENV] = _make_creds_env("admin", "correct-horse")
    app = create_app()
    # raise_server_exceptions=False so the DB-not-configured
    # RuntimeError that /runs would raise downstream becomes a 500
    # response we can read, rather than blowing up the test client.
    # We don't care what the inner route does — we care that auth
    # admitted us past the middleware.
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(
        "/api/cities/cambridge/runs", headers=_basic_header("admin", "correct-horse")
    )
    assert response.status_code != 401


@pytest.mark.usefixtures("_clear_auth_env")
def test_auth_enabled_wrong_password_returns_401() -> None:
    os.environ[_ENV] = _make_creds_env("admin", "correct-horse")
    app = create_app()
    client = TestClient(app)
    response = client.get(
        "/api/cities/cambridge/runs", headers=_basic_header("admin", "wrong-password")
    )
    assert response.status_code == 401


@pytest.mark.usefixtures("_clear_auth_env")
def test_auth_enabled_wrong_username_returns_401() -> None:
    os.environ[_ENV] = _make_creds_env("admin", "correct-horse")
    app = create_app()
    client = TestClient(app)
    response = client.get(
        "/api/cities/cambridge/runs", headers=_basic_header("eve", "correct-horse")
    )
    assert response.status_code == 401


@pytest.mark.usefixtures("_clear_auth_env")
def test_auth_enabled_malformed_header_returns_401() -> None:
    os.environ[_ENV] = _make_creds_env()
    app = create_app()
    client = TestClient(app)
    # No "Basic " prefix.
    response = client.get(
        "/api/cities/cambridge/runs",
        headers={"Authorization": base64.b64encode(b"admin:correct-horse").decode()},
    )
    assert response.status_code == 401


@pytest.mark.usefixtures("_clear_auth_env")
def test_auth_enabled_non_base64_payload_returns_401() -> None:
    os.environ[_ENV] = _make_creds_env()
    app = create_app()
    client = TestClient(app)
    response = client.get(
        "/api/cities/cambridge/runs",
        headers={"Authorization": "Basic not-base64!!"},
    )
    assert response.status_code == 401


# ---------- /health exemption (Fly healthcheck path) ------------------------


@pytest.mark.usefixtures("_clear_auth_env")
def test_health_endpoint_exempt_from_auth_so_fly_healthchecks_work() -> None:
    """Fly's edge healthcheck hits /health with no auth header — if
    we required auth there, the Machine would be declared unhealthy
    and never receive traffic."""
    os.environ[_ENV] = _make_creds_env()
    app = create_app()
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------- env var shape ---------------------------------------------------


@pytest.mark.usefixtures("_clear_auth_env")
def test_env_var_without_colon_is_treated_as_no_auth() -> None:
    """Malformed env var (missing ``:``) is safer to treat as
    unconfigured than to half-enable a broken gate. The middleware
    logs a warning and lets requests through."""
    os.environ[_ENV] = "no-colon-here"
    app = create_app()
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200


@pytest.mark.usefixtures("_clear_auth_env")
def test_env_var_empty_string_is_treated_as_no_auth() -> None:
    os.environ[_ENV] = ""
    app = create_app()
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
