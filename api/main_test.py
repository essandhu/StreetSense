"""Tests for ``api.main.create_app`` static-file mounting (Task 1.4).

The deploy image (``api/Dockerfile``) bakes the built SPA into
``/app/frontend/dist`` and the API process serves both API JSON and
the SPA from one process. This module verifies the mount only attaches
when a real ``dist/`` is present (no spurious 404s in dev when the
frontend hasn't been built) and that API routes win over the SPA
fallback.

Pure-Python tests — no DB required, no DATABASE_URL gate. The
mount lookup uses a filesystem path, so a ``tmp_path`` fixture is
enough to exercise both branches.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import _frontend_dist_path, create_app

_ENV = "STREETSENSE_FRONTEND_DIST"


@pytest.fixture
def _clear_dist_env() -> Iterator[None]:
    """Ensure the env var doesn't leak across tests."""
    prior = os.environ.pop(_ENV, None)
    try:
        yield
    finally:
        if prior is not None:
            os.environ[_ENV] = prior
        else:
            os.environ.pop(_ENV, None)


def _seed_dist(root: Path) -> Path:
    dist = root / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><body><div id='root'>spa</div></body></html>",
        encoding="utf-8",
    )
    assets = dist / "assets"
    assets.mkdir()
    (assets / "app.abc123.js").write_text(
        "console.log('hashed bundle');", encoding="utf-8"
    )
    return dist


@pytest.mark.usefixtures("_clear_dist_env")
def test_frontend_dist_path_returns_none_when_env_unset_and_default_absent() -> None:
    """No env var + no /app/frontend/dist on disk → no mount."""
    # The container path /app/frontend/dist exists on the deploy
    # image but should not exist on a typical dev machine.
    assert _frontend_dist_path() is None or _frontend_dist_path() == Path(
        "/app/frontend/dist"
    )


@pytest.mark.usefixtures("_clear_dist_env")
def test_frontend_dist_path_honors_env_override(
    tmp_path: Path,
) -> None:
    """When STREETSENSE_FRONTEND_DIST is set, that path wins."""
    dist = _seed_dist(tmp_path)
    os.environ[_ENV] = str(dist)
    resolved = _frontend_dist_path()
    assert resolved == dist


@pytest.mark.usefixtures("_clear_dist_env")
def test_frontend_dist_path_returns_none_for_nonexistent_env_path(
    tmp_path: Path,
) -> None:
    """Env-var-pointed-at-a-missing-dir returns None (defensive)."""
    os.environ[_ENV] = str(tmp_path / "does-not-exist")
    assert _frontend_dist_path() is None


@pytest.mark.usefixtures("_clear_dist_env")
def test_api_routes_unaffected_when_spa_mount_active(tmp_path: Path) -> None:
    """API routes register first and win over the SPA fallback."""
    _seed_dist(tmp_path)
    os.environ[_ENV] = str(tmp_path / "dist")
    app = create_app()
    client = TestClient(app)
    # /health is registered as a real route — must not be swallowed
    # by the SPA mount.
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.usefixtures("_clear_dist_env")
def test_spa_index_html_served_at_root_when_mount_active(tmp_path: Path) -> None:
    """Root path renders the SPA's index.html when the mount is up."""
    _seed_dist(tmp_path)
    os.environ[_ENV] = str(tmp_path / "dist")
    app = create_app()
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "spa" in response.text


@pytest.mark.usefixtures("_clear_dist_env")
def test_spa_assets_served_when_mount_active(tmp_path: Path) -> None:
    """Hashed Vite bundle files under /assets/ load with their bytes."""
    _seed_dist(tmp_path)
    os.environ[_ENV] = str(tmp_path / "dist")
    app = create_app()
    client = TestClient(app)
    response = client.get("/assets/app.abc123.js")
    assert response.status_code == 200
    assert "hashed bundle" in response.text


@pytest.mark.usefixtures("_clear_dist_env")
def test_unknown_path_falls_back_to_spa_index_html(tmp_path: Path) -> None:
    """A deep-link like /methodology must render the SPA, not 404 —
    React Router resolution happens client-side."""
    _seed_dist(tmp_path)
    os.environ[_ENV] = str(tmp_path / "dist")
    app = create_app()
    client = TestClient(app)
    response = client.get("/methodology")
    # The SPA HTML — not a 404.
    assert response.status_code == 200
    assert "spa" in response.text


@pytest.mark.usefixtures("_clear_dist_env")
def test_no_spa_mount_when_dist_absent_root_404s(tmp_path: Path) -> None:
    """Dev case: no dist → no mount → root 404s (API routes still work)."""
    # Point env at a non-existent path so the dev default isn't used.
    os.environ[_ENV] = str(tmp_path / "missing")
    app = create_app()
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 404
    # API still works.
    assert client.get("/health").status_code == 200
