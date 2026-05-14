"""GET /admin/freshness — Task 1.5.4.

The response is a list-shaped envelope so additional sources (imagery,
incidents) can be added in later phases without a breaking change.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_freshness_returns_list_envelope(
    api_client: AsyncClient, seed_data_sources: None
) -> None:
    del seed_data_sources
    resp = await api_client.get("/admin/freshness")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Wrapped list — not a single object.
    assert "sources" in body
    assert isinstance(body["sources"], list)
    names = {entry["name"] for entry in body["sources"]}
    # Phase 3: four sources — `imagery` carries real metadata, and
    # `perception_model` joins the registry once `make seed-model` has
    # run.
    assert names == {"osm", "imagery", "solar_position", "perception_model"}


@pytest.mark.asyncio
async def test_freshness_carries_last_ingested_at(
    api_client: AsyncClient, seed_data_sources: None
) -> None:
    del seed_data_sources
    resp = await api_client.get("/admin/freshness")
    body = resp.json()
    by_name = {entry["name"]: entry for entry in body["sources"]}
    assert by_name["osm"]["last_ingested_at"] is not None
    # Phase 3: imagery now carries a real last_ingested_at (the
    # ingestion job bumps it).
    assert by_name["imagery"]["last_ingested_at"] is not None
    assert by_name["imagery"]["metadata"].get("provider") == "mapillary"
    assert by_name["solar_position"]["last_ingested_at"] is not None
    assert by_name["solar_position"]["metadata"].get("kind") == "compute"
    # Phase 3: perception_model registers when make seed-model runs.
    assert by_name["perception_model"]["last_ingested_at"] is not None
    assert by_name["perception_model"]["metadata"].get("kind") == "model"
    assert "perception_model_version" in by_name["perception_model"]["metadata"]


@pytest.mark.asyncio
async def test_freshness_includes_server_time(
    api_client: AsyncClient, seed_data_sources: None
) -> None:
    del seed_data_sources
    resp = await api_client.get("/admin/freshness")
    body = resp.json()
    assert "server_time" in body
