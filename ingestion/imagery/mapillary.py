"""Mapillary v4 HTTP API adapter.

First concrete implementation of :class:`ImageryProvider` (extension
point 3). See ``docs/adr/0005-imagery-provider.md`` for the selection
rationale and the license / rate-limit posture.

Network access goes through ``httpx`` with HTTP/2 enabled (per
``pyproject.toml``). The implementation is deliberately stateless beyond
a per-instance rate limiter and HTTP session — calls with the same
inputs yield the same responses, satisfying the protocol's idempotency
contract for any caller that re-runs against the same cassette or
upstream state.

CI never hits the live network: tests live in :mod:`mapillary_test`
under ``ingestion/imagery/cassettes/`` with ``vcrpy`` recordings.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import httpx

from ingestion.imagery.provider import ImageryReference, Waypoint

# A small bbox (~10 m at Cambridge latitude) centered on each waypoint.
# The Mapillary `images` endpoint takes a `bbox`, not a point — this is
# the smallest envelope that still surfaces images captured *at* the
# waypoint without pulling in neighbors several meters away.
_DEFAULT_BBOX_DEG = 0.0001  # ~11 m at 42°N

_GRAPH_API_BASE = "https://graph.mapillary.com"

# Fields requested from the Mapillary images endpoint. The set is kept
# small both to honor "Please reduce the amount of data" guidance from
# the API and because Phase 3 needs only these.
_IMAGE_FIELDS = (
    "id",
    "captured_at",
    "compass_angle",
    "camera_parameters",
    "thumb_1024_url",
)


def _captured_at_to_date(captured_at_ms: int) -> date:
    """Mapillary returns ``captured_at`` as ms-since-epoch (UTC)."""
    return datetime.fromtimestamp(captured_at_ms / 1000.0, tz=UTC).date()


def _bbox_for(waypoint: Waypoint, half_extent_deg: float = _DEFAULT_BBOX_DEG) -> str:
    """Mapillary bbox is ``minLon,minLat,maxLon,maxLat``."""
    return (
        f"{waypoint.lon - half_extent_deg:.6f},"
        f"{waypoint.lat - half_extent_deg:.6f},"
        f"{waypoint.lon + half_extent_deg:.6f},"
        f"{waypoint.lat + half_extent_deg:.6f}"
    )


class _TokenBucket:
    """Token-bucket rate limiter sized to the developer-key ceiling.

    The Mapillary developer-key ceiling is 60 000 requests / minute (per
    ADR 0005). One token per request; tokens replenish at the same
    cadence. Thread-safe so a single provider instance can be shared
    across the ingestion job's batched calls.
    """

    def __init__(self, capacity_per_minute: int) -> None:
        self._capacity = capacity_per_minute
        self._tokens: float = float(capacity_per_minute)
        self._refill_rate = capacity_per_minute / 60.0  # tokens / second
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(
                float(self._capacity),
                self._tokens + (now - self._last_refill) * self._refill_rate,
            )
            self._last_refill = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            wait_s = (1.0 - self._tokens) / self._refill_rate
        time.sleep(wait_s)
        self.acquire()


class MapillaryProvider:
    """Mapillary v4 implementation of :class:`ImageryProvider`.

    The ``access_token`` is read from ``MAPILLARY_ACCESS_TOKEN`` if not
    passed explicitly. CI cassettes are recorded under
    ``ingestion/imagery/cassettes/`` with the token scrubbed; the
    placeholder ``"MLY|TEST|TEST"`` shows up in cassette URLs.
    """

    name: str = "mapillary"

    def __init__(
        self,
        *,
        access_token: str | None = None,
        client: httpx.Client | None = None,
        rate_limit_per_minute: int = 60_000,
    ) -> None:
        token = access_token or os.environ.get("MAPILLARY_ACCESS_TOKEN")
        if not token:
            raise RuntimeError(
                "MAPILLARY_ACCESS_TOKEN not set and no access_token passed. "
                "See docs/adr/0005-imagery-provider.md for token setup."
            )
        self._token = token
        # ``http2=True`` shaves connection setup over the many bbox
        # queries an ingestion run makes.
        self._client = client or httpx.Client(http2=True, timeout=30.0)
        self._owns_client = client is None
        self._rate_limiter = _TokenBucket(rate_limit_per_minute)

    def __enter__(self) -> MapillaryProvider:
        return self

    def __exit__(self, *_: object) -> None:
        if self._owns_client:
            self._client.close()

    def fetch_for_waypoints(
        self,
        waypoints: list[Waypoint],
        *,
        within: tuple[date, date] | None = None,
    ) -> Iterator[ImageryReference]:
        for waypoint in waypoints:
            yield from self._fetch_one_waypoint(waypoint, within=within)

    def _fetch_one_waypoint(
        self,
        waypoint: Waypoint,
        *,
        within: tuple[date, date] | None,
    ) -> Iterator[ImageryReference]:
        self._rate_limiter.acquire()
        response = self._client.get(
            f"{_GRAPH_API_BASE}/images",
            params={
                "access_token": self._token,
                "bbox": _bbox_for(waypoint),
                "fields": ",".join(_IMAGE_FIELDS),
                "limit": 10,
            },
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        for raw in payload.get("data", []):
            captured_at = raw.get("captured_at")
            if captured_at is None:
                # Mapillary occasionally returns images without a
                # capture timestamp on very old records. Skip — the
                # perception scorer needs the date for `imagery_capture_window`.
                continue
            capture_date = _captured_at_to_date(int(captured_at))
            if within is not None and (capture_date < within[0] or capture_date > within[1]):
                continue
            yield ImageryReference(
                provider=self.name,
                provider_image_id=str(raw["id"]),
                segment_id=waypoint.segment_id,
                sample_index=waypoint.sample_index,
                capture_date=capture_date,
                # Mapillary's `compass_angle` is documented as [0, 360)
                # but occasionally returns values like -0.30392932891846
                # (float precision near 0°) or, defensively, multiples
                # ≥ 360. Normalize to the unit circle so the DB's
                # segment_imagery_heading_range CHECK constraint holds.
                heading_deg=float(raw.get("compass_angle") or 0.0) % 360.0,
                camera_params={
                    k: raw[k]
                    for k in ("camera_parameters", "thumb_1024_url")
                    if k in raw and raw[k] is not None
                },
            )

    def download_bytes(self, reference: ImageryReference) -> bytes:
        thumb_url = reference.camera_params.get("thumb_1024_url")
        if not isinstance(thumb_url, str):
            # Re-fetch with a fresh thumb URL — Mapillary signs them
            # with a short TTL.
            self._rate_limiter.acquire()
            response = self._client.get(
                f"{_GRAPH_API_BASE}/{reference.provider_image_id}",
                params={
                    "access_token": self._token,
                    "fields": "thumb_1024_url",
                },
            )
            response.raise_for_status()
            payload = response.json()
            thumb_url = payload.get("thumb_1024_url")
            if not isinstance(thumb_url, str):
                raise RuntimeError(
                    f"Mapillary image {reference.provider_image_id} returned no thumb URL"
                )
        self._rate_limiter.acquire()
        image_response = self._client.get(thumb_url, follow_redirects=True)
        image_response.raise_for_status()
        return image_response.content
