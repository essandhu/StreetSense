"""Mapillary v4 adapter tests via ``vcrpy``.

Recorded cassettes live next to this file under ``cassettes/``. The
recordings were captured against a tight Cambridge, MA bbox so the
upstream content is stable: Mapillary's coverage of central Cambridge
predates Phase 3 by years and is unlikely to change in a way that
would invalidate the assertions here.

Token hygiene
-------------
The ``MAPILLARY_ACCESS_TOKEN`` env var is required only for
*recording* (re-recording when cassettes go stale). CI sets it to
``"MLY|TEST|TEST"`` since the cassettes already contain the answers.
Recorded cassettes scrub the real token via vcrpy's
``filter_query_parameters`` and ``filter_headers`` hooks; the
placeholder shows up wherever the token would appear in the URL or
the Authorization header.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

import httpx
import pytest
import vcr

from ingestion.imagery.mapillary import MapillaryProvider
from ingestion.imagery.provider import Waypoint

# Cassette directory — committed under ingestion/imagery/cassettes/.
_CASSETTE_DIR = Path(__file__).parent / "cassettes"

# Placeholder used in cassettes so recorded responses can be replayed
# in CI without leaking the developer token.
_SCRUBBED_TOKEN_PLACEHOLDER = "MLY|TEST|TEST"

# A stable point near MIT in Cambridge, MA. Mapillary's coverage here
# is dense and predates Phase 3 by years, so the recorded response is
# expected to remain valid.
_CAMBRIDGE_LAT = 42.372
_CAMBRIDGE_LON = -71.105

# Stable UUID so failures point at a recognizable segment id.
_SEG_ID = UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture
def vcr_cassette() -> vcr.VCR:
    """Configure vcrpy with token + auth scrubbing.

    Per ADR 0005 and the dependency-hygiene posture in CLAUDE.md,
    cassettes must never contain the real access token. The filters
    below replace the token in URL query parameters and the
    Authorization header before the cassette is written.

    ``record_mode='once'`` is the right default: existing cassettes
    replay; missing cassettes record (on first run, with the env var
    set); CI never records because cassettes are committed.
    """
    return vcr.VCR(
        cassette_library_dir=str(_CASSETTE_DIR),
        record_mode="once",
        match_on=["method", "scheme", "host", "path", "query"],
        filter_query_parameters=[("access_token", _SCRUBBED_TOKEN_PLACEHOLDER)],
        filter_headers=[("authorization", _SCRUBBED_TOKEN_PLACEHOLDER)],
        decode_compressed_response=True,
    )


@pytest.fixture
def provider() -> Iterator[MapillaryProvider]:
    """Provider against the real token if set, else the scrubbed placeholder.

    CI uses the placeholder — every HTTP call is intercepted by vcrpy
    before it touches the network, so the placeholder is sufficient.
    """
    token = os.environ.get("MAPILLARY_ACCESS_TOKEN") or _SCRUBBED_TOKEN_PLACEHOLDER
    with MapillaryProvider(access_token=token) as provider:
        yield provider


def _waypoint(sample_index: int = 0) -> Waypoint:
    return Waypoint(
        lat=_CAMBRIDGE_LAT,
        lon=_CAMBRIDGE_LON,
        segment_id=_SEG_ID,
        sample_index=sample_index,
    )


def test_fetch_for_waypoints_returns_references(
    vcr_cassette: vcr.VCR, provider: MapillaryProvider
) -> None:
    """One waypoint, central Cambridge — expect ≥ 1 reference with
    ``provider == "mapillary"`` and a populated ``capture_date``."""
    with vcr_cassette.use_cassette("fetch_for_waypoints_returns_references.yaml"):
        references = list(provider.fetch_for_waypoints([_waypoint()]))

    assert references, "Cambridge waypoint returned no imagery"
    for reference in references:
        assert reference.provider == "mapillary"
        assert isinstance(reference.capture_date, date)
        assert reference.segment_id == _SEG_ID


def test_fetch_is_idempotent(vcr_cassette: vcr.VCR, provider: MapillaryProvider) -> None:
    """Two consecutive fetches yield the same provider_image_id set."""
    with vcr_cassette.use_cassette("fetch_is_idempotent.yaml"):
        first = list(provider.fetch_for_waypoints([_waypoint()]))
        second = list(provider.fetch_for_waypoints([_waypoint()]))

    first_ids = {ref.provider_image_id for ref in first}
    second_ids = {ref.provider_image_id for ref in second}
    assert first_ids == second_ids, "Re-running fetch_for_waypoints must be idempotent"


def test_fetch_respects_within_window(vcr_cassette: vcr.VCR, provider: MapillaryProvider) -> None:
    """A tight ``within`` window yields ≤ the count of a wide window."""
    wide = (date(2010, 1, 1), date(2030, 12, 31))
    # A 1-day window almost certainly misses every image — captured_at
    # is a continuous timestamp, so even a single day rarely overlaps.
    tight = (date(2025, 6, 14), date(2025, 6, 15))
    with vcr_cassette.use_cassette("fetch_respects_within_window.yaml"):
        wide_results = list(provider.fetch_for_waypoints([_waypoint()], within=wide))
        tight_results = list(provider.fetch_for_waypoints([_waypoint()], within=tight))

    assert len(tight_results) <= len(wide_results), (
        f"Tight window ({len(tight_results)}) must not exceed wide window ({len(wide_results)})"
    )


_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_download_bytes_returns_image_bytes(
    vcr_cassette: vcr.VCR, provider: MapillaryProvider
) -> None:
    """First image bytes from the cassette start with a JPEG/PNG magic."""
    with vcr_cassette.use_cassette("download_bytes_returns_image_bytes.yaml"):
        references = list(provider.fetch_for_waypoints([_waypoint()]))
        assert references, "Cassette returned no references to download"
        image_bytes = provider.download_bytes(references[0])

    assert image_bytes, "download_bytes returned empty"
    assert image_bytes.startswith(_JPEG_MAGIC) or image_bytes.startswith(_PNG_MAGIC), (
        f"Image bytes start with {image_bytes[:16]!r}, not JPEG/PNG magic"
    )


def _verify_cassette_token_scrubbed(cassette_path: Path) -> None:
    """Sanity check: the recorded cassette must not contain the real token."""
    text = cassette_path.read_text(encoding="utf-8", errors="replace")
    # Real tokens have format MLY|<digits>|<hex>; we accept the
    # scrubbed placeholder MLY|TEST|TEST but flag any other MLY|
    # followed by digits.
    for line in text.splitlines():
        if "MLY|" in line and _SCRUBBED_TOKEN_PLACEHOLDER not in line:
            # Allow only if it's a literal that matches a known
            # non-token form (defensive — should not occur).
            raise AssertionError(
                f"Cassette {cassette_path.name} contains an unscrubbed MLY| value:\n  {line}"
            )


@pytest.mark.parametrize(
    "cassette_name",
    [
        "fetch_for_waypoints_returns_references.yaml",
        "fetch_is_idempotent.yaml",
        "fetch_respects_within_window.yaml",
        "download_bytes_returns_image_bytes.yaml",
    ],
)
def test_cassette_has_no_unscrubbed_token(cassette_name: str) -> None:
    """Cassettes committed to the repo must not contain the real token."""
    cassette_path = _CASSETTE_DIR / cassette_name
    if not cassette_path.exists():
        pytest.skip(f"Cassette {cassette_name} not yet recorded")
    _verify_cassette_token_scrubbed(cassette_path)


# --- Regression: compass_angle normalization -------------------------------
# Mapillary's `compass_angle` field is documented as [0, 360) but real
# responses occasionally return values like -0.30392932891846 (likely
# floating-point precision near 0°). The DB schema's
# `segment_imagery_heading_range` CHECK constraint
# (db/migrations/versions/0008_segment_imagery.py) requires
# `heading_deg >= 0.0 AND heading_deg < 360.0`, so the adapter must
# normalize at the ingress boundary or the entire ingestion job rolls
# back at flush time.
def _mock_client_with_payload(payload: dict[str, object]) -> httpx.Client:
    fake_client = MagicMock(spec=httpx.Client)
    fake_client.get = MagicMock(return_value=_mock_response(200, payload))
    return fake_client


# --- Regression: 5xx retry --------------------------------------------------
# Mapillary occasionally returns 5xx on transient errors. Over a full
# Cambridge ingest (~180k API calls) at least one such error is
# statistically expected; without retry, that single error rolls back
# the entire ingestion. See ``_get_with_retry`` in mapillary.py.
def _mock_response(status_code: int, payload: dict[str, object] | None) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    if status_code >= 400:
        response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                f"{status_code}", request=MagicMock(), response=response
            )
        )
    else:
        response.raise_for_status = MagicMock(return_value=None)
    response.json = MagicMock(return_value=payload or {"data": []})
    return response


def _ok_payload() -> dict[str, object]:
    return {
        "data": [
            {
                "id": "img-1",
                "captured_at": 1_700_000_000_000,
                "compass_angle": 12.5,
                "camera_parameters": [],
                "thumb_1024_url": "https://example.invalid/x.jpg",
            }
        ]
    }


def _provider_with_responses(responses: list[MagicMock]) -> MapillaryProvider:
    """Provider whose mocked client yields ``responses`` in order on .get()."""
    fake_client = MagicMock(spec=httpx.Client)
    fake_client.get = MagicMock(side_effect=responses)
    return MapillaryProvider(
        access_token="MLY|TEST|TEST",
        client=fake_client,
        retry_initial_backoff_seconds=0.0,
        retry_max_attempts=3,
    )


def test_get_with_retry_succeeds_after_one_5xx() -> None:
    """A single 5xx is retried and the subsequent 200 returns normally."""
    with _provider_with_responses(
        [_mock_response(500, None), _mock_response(200, _ok_payload())]
    ) as provider:
        refs = list(provider.fetch_for_waypoints([_waypoint()]))
    assert len(refs) == 1
    assert refs[0].provider_image_id == "img-1"


def test_get_with_retry_raises_after_max_attempts() -> None:
    """Sustained 5xx exhausts retries and raises the final HTTPStatusError."""
    with (
        _provider_with_responses([_mock_response(500, None) for _ in range(3)]) as provider,
        pytest.raises(httpx.HTTPStatusError),
    ):
        list(provider.fetch_for_waypoints([_waypoint()]))


def test_get_with_retry_4xx_raises_immediately() -> None:
    """4xx is a client error — surface immediately, no retry."""
    only_call = _mock_response(404, None)
    with (
        _provider_with_responses([only_call]) as provider,
        pytest.raises(httpx.HTTPStatusError),
    ):
        list(provider.fetch_for_waypoints([_waypoint()]))
    # raise_for_status was called once on the single response; no retry happened.
    only_call.raise_for_status.assert_called_once()


@pytest.mark.parametrize(
    ("raw_compass_angle", "expected_heading_deg"),
    [
        (-0.30392932891846, 360.0 - 0.30392932891846),  # the live-pipeline failure
        (-0.0, 0.0),
        (0.0, 0.0),
        (180.0, 180.0),
        (359.999, 359.999),
        (360.0, 0.0),  # closed-open interval — 360 wraps to 0
        (720.5, 0.5),  # defensive: any multiple wraps
        (None, 0.0),  # Mapillary occasionally omits the field
    ],
)
def test_heading_deg_is_normalized_to_unit_circle(
    raw_compass_angle: float | None, expected_heading_deg: float
) -> None:
    """``heading_deg`` must satisfy the schema's [0, 360) constraint
    regardless of what Mapillary returns."""
    payload: dict[str, object] = {
        "data": [
            {
                "id": "test-image-1",
                "captured_at": 1_700_000_000_000,  # ms since epoch (2023-11-14)
                "compass_angle": raw_compass_angle,
                "camera_parameters": [1.0, 0.0, 0.0],
                "thumb_1024_url": "https://example.invalid/thumb.jpg",
            }
        ]
    }
    fake_client = _mock_client_with_payload(payload)
    with MapillaryProvider(access_token="MLY|TEST|TEST", client=fake_client) as provider:
        references = list(provider.fetch_for_waypoints([_waypoint()]))

    assert len(references) == 1
    heading = references[0].heading_deg
    assert 0.0 <= heading < 360.0, (
        f"heading_deg must be in [0, 360); got {heading} from compass_angle={raw_compass_angle}"
    )
    assert heading == pytest.approx(expected_heading_deg)
