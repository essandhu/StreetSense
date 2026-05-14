"""MassDOT IMPACT adapter tests via ``vcrpy``.

Recorded cassettes live next to this file under ``cassettes/``. They
were captured against a tight Cambridge, MA bounding box restricted to
a single calendar-year layer so the upstream payload is stable and
small. MassDOT's CrashClosedYear/* services publish per-year frozen
cohorts (2002-2019 at time of writing) — once a year is "closed", the
records below it do not change, which is exactly what makes the
cassette deterministic.

The adapter performs no authentication (MassDOT IMPACT is a public
FeatureServer) so token scrubbing is a no-op. Cassettes still apply
``decode_compressed_response`` so the recorded JSON is human-readable
on inspection.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import vcr

from ingestion.incidents.massdot_impact import MassDOTImpactProvider
from ingestion.incidents.provider import BoundingBox, IncidentSeverity

_CASSETTE_DIR = Path(__file__).parent / "cassettes"

# Cambridge bounding box (mirrors config/cities/cambridge.yaml).
_CAMBRIDGE_BBOX = BoundingBox(
    min_lat=42.355,
    min_lon=-71.165,
    max_lat=42.408,
    max_lon=-71.066,
)

# Single-year cohort for compact cassettes; MassDOT's 2019 layer is the
# most recent closed year as of recording time and exercises every
# severity bucket.
_RECORDING_YEAR = 2019


@pytest.fixture
def vcr_cassette() -> vcr.VCR:
    """Configure vcrpy.

    ``record_mode='once'`` is the right default: existing cassettes
    replay; missing cassettes record on first run; CI never records
    because cassettes are committed.
    """
    return vcr.VCR(
        cassette_library_dir=str(_CASSETTE_DIR),
        record_mode="once",
        match_on=["method", "scheme", "host", "path", "query"],
        decode_compressed_response=True,
    )


@pytest.fixture
def provider() -> Iterator[MassDOTImpactProvider]:
    with MassDOTImpactProvider(years=(_RECORDING_YEAR,)) as p:
        yield p


def test_fetch_for_bbox_returns_records(
    vcr_cassette: vcr.VCR, provider: MassDOTImpactProvider
) -> None:
    """The Cambridge bbox in 2019 contains many crashes; expect ≥ 1 record."""
    with vcr_cassette.use_cassette("fetch_for_bbox_returns_records.yaml"):
        records = list(provider.fetch_for_bbox(_CAMBRIDGE_BBOX))

    assert records, "Cambridge bbox returned no MassDOT records"
    for record in records:
        assert record.provider == "massdot-impact"
        assert record.provider_incident_id
        assert isinstance(record.incident_at, datetime)
        assert record.incident_at.tzinfo is not None, "incident_at must be tz-aware UTC"
        # Geometry should fall inside the bounding box (the FeatureServer
        # may include points marginally on the boundary; tolerate ±0.001
        # so a half-meter offset doesn't flap the test).
        assert _CAMBRIDGE_BBOX.min_lat - 1e-3 <= record.lat <= _CAMBRIDGE_BBOX.max_lat + 1e-3
        assert _CAMBRIDGE_BBOX.min_lon - 1e-3 <= record.lon <= _CAMBRIDGE_BBOX.max_lon + 1e-3
        assert isinstance(record.severity, IncidentSeverity)


def test_fetch_is_idempotent(vcr_cassette: vcr.VCR, provider: MassDOTImpactProvider) -> None:
    """Two consecutive fetches against the same bbox yield identical id sets."""
    with vcr_cassette.use_cassette("fetch_is_idempotent.yaml"):
        first = list(provider.fetch_for_bbox(_CAMBRIDGE_BBOX))
        second = list(provider.fetch_for_bbox(_CAMBRIDGE_BBOX))

    first_ids = {r.provider_incident_id for r in first}
    second_ids = {r.provider_incident_id for r in second}
    assert first_ids == second_ids, "Re-running fetch_for_bbox must be idempotent"


def test_fetch_respects_within_window(
    vcr_cassette: vcr.VCR, provider: MassDOTImpactProvider
) -> None:
    """A within-window filter narrows the result set (or keeps it equal)."""
    # First half of 2019 only; total set is the full year cassette.
    h1_2019 = (date(2019, 1, 1), date(2019, 6, 30))
    with vcr_cassette.use_cassette("fetch_respects_within_window.yaml"):
        full = list(provider.fetch_for_bbox(_CAMBRIDGE_BBOX))
        narrow = list(provider.fetch_for_bbox(_CAMBRIDGE_BBOX, within=h1_2019))

    assert len(narrow) <= len(full)
    for r in narrow:
        d = r.incident_at.astimezone(UTC).date()
        assert h1_2019[0] <= d <= h1_2019[1]


def test_severity_mapping_covers_taxonomy(
    vcr_cassette: vcr.VCR, provider: MassDOTImpactProvider
) -> None:
    """The Cambridge cohort should exercise multiple severity buckets.

    Not every cassette will see all four severities — Cambridge fatal
    crashes are rare. We assert at least two distinct severities show
    up, which guarantees ``_SEVERITY_MAP`` is being exercised for
    something beyond the default ``UNKNOWN`` path.
    """
    with vcr_cassette.use_cassette("fetch_severity_mapping.yaml"):
        records = list(provider.fetch_for_bbox(_CAMBRIDGE_BBOX))

    severities = {r.severity for r in records}
    assert len(severities) >= 2, (
        f"Expected ≥ 2 distinct severities; got {sorted(s.value for s in severities)}"
    )
