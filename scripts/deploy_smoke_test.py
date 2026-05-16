"""Tests for the deploy smoke script (Task 1.7).

The smoke script makes real HTTP calls; tests mock the
``httpx.Client.get`` interface so the logic is exercised hermetically.
Tests assert: every required endpoint is hit, basic-auth header is
attached to the gated endpoints, /health is hit WITHOUT auth, exit
code reflects pass/fail aggregation.
"""

from __future__ import annotations

import base64
from typing import Any
from unittest import mock

import httpx

from scripts.deploy_smoke import smoke


def _mock_response(status: int = 200, json_body: Any = None) -> mock.Mock:
    resp = mock.Mock()
    resp.status_code = status
    resp.json = mock.Mock(return_value=json_body if json_body is not None else {})
    return resp


def _all_ok_get(url: str, headers: dict[str, str] | None = None) -> mock.Mock:
    del headers  # unused — caller asserts on it separately
    if url.endswith("/runs"):
        return _mock_response(200, {"runs": []})
    return _mock_response(200, {"ok": True})


def test_smoke_returns_0_when_every_check_passes() -> None:
    with mock.patch("httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.side_effect = _all_ok_get
        rc = smoke("https://example.com", user="admin", password="pw")
    assert rc == 0


def test_smoke_returns_nonzero_when_any_check_fails() -> None:
    def _one_fails(url: str, headers: dict[str, str] | None = None) -> mock.Mock:
        del headers
        if "/admin/freshness" in url:
            return _mock_response(503, None)
        if url.endswith("/runs"):
            return _mock_response(200, {"runs": []})
        return _mock_response(200, {"ok": True})

    with mock.patch("httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.side_effect = _one_fails
        rc = smoke("https://example.com", user="admin", password="pw")
    assert rc == 1


def test_smoke_hits_health_without_auth_and_others_with_auth() -> None:
    with mock.patch("httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.side_effect = _all_ok_get
        smoke("https://example.com", user="admin", password="hunter2")
    # Build the expected auth header for the assertion.
    expected_auth = "Basic " + base64.b64encode(b"admin:hunter2").decode("ascii")
    # Inspect every call: /health must have NO Authorization;
    # everything else must HAVE the expected Authorization.
    for call in client.get.call_args_list:
        url = call.args[0]
        headers = call.kwargs.get("headers") or {}
        if url.endswith("/health"):
            assert "Authorization" not in headers, "/health should not be auth'd"
        else:
            assert headers.get("Authorization") == expected_auth, (
                f"missing or wrong auth on {url}: {headers}"
            )


def test_smoke_hits_every_required_endpoint() -> None:
    with mock.patch("httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.side_effect = _all_ok_get
        smoke("https://example.com", user="admin", password="pw")
    urls = [call.args[0] for call in client.get.call_args_list]
    assert any(u.endswith("/health") for u in urls)
    assert any(u.endswith("https://example.com/") for u in urls)
    assert any(u.endswith("/admin/freshness") for u in urls)
    assert any(u.endswith("/runs") for u in urls)
    # Tile URL contains pg_tileserv path.
    assert any("/tiles/" in u for u in urls)


def test_smoke_treats_transport_errors_as_check_failures() -> None:
    """A connection refused / DNS failure is a real smoke failure —
    not a silent skip."""

    def _raises(_url: str, headers: dict[str, str] | None = None) -> Any:
        del headers
        raise httpx.ConnectError("nope")

    with mock.patch("httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.side_effect = _raises
        rc = smoke("https://example.com", user="admin", password="pw")
    assert rc == 1


def test_smoke_overrides_tile_via_argument() -> None:
    with mock.patch("httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.side_effect = _all_ok_get
        smoke("https://example.com", user="admin", password="pw", tile="10,123,456")
    urls = [call.args[0] for call in client.get.call_args_list]
    assert any("/10/123/456.pbf" in u for u in urls)
