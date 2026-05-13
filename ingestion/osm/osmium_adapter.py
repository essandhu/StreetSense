"""osmium-backed concrete `OSMSource` adapter.

Replaces the originally planned `pyrosm_adapter` because `pyrosm` 0.6.x
ships a broken `pyrobuf` build dependency that does not compile against
modern setuptools. `osmium` (the official osmium-tool Python bindings) is
actively maintained, has Windows + Linux wheels, and gives us the same
PBF-streaming primitives at lower abstraction.

This module:

- Streams a PBF (or .osm XML) file in one pass.
- Filters to ways tagged `highway=*`.
- Clips by bounding box at parse time (way is kept if **any** of its
  reconstructed coordinates falls inside the bbox).
- Yields `RoadSegment` instances with `LineString` geometry in WGS84.

Live downloads are kept lightweight: `httpx` with conditional GET (If-
Modified-Since) against the Geofabrik URL. For tests, the adapter accepts a
`prefetched` local path and skips network IO entirely.
"""

from __future__ import annotations

import email.utils as eut
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.message import Message
from hashlib import sha256
from pathlib import Path

import httpx
import osmium
import structlog
from shapely.geometry import LineString

from ingestion.osm.source import RoadSegment, SnapshotMetadata

log = structlog.get_logger(__name__)


def _coord_in_bbox(lon: float, lat: float, bbox: tuple[float, float, float, float]) -> bool:
    min_lon, min_lat, max_lon, max_lat = bbox
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat


@dataclass(frozen=True, slots=True)
class _ParsedWay:
    osm_way_id: int
    coords: list[tuple[float, float]]
    attrs: dict[str, str]


class _HighwayHandler(osmium.SimpleHandler):
    """osmium pass that collects every way tagged `highway=*` with its full
    coordinate list. Filtering and clipping happen in the caller — this
    handler is intentionally permissive so non-highway filters can be
    added later (cycleway-only, bus-only) without recomputing geometry.
    """

    def __init__(self) -> None:
        super().__init__()
        self.ways: list[_ParsedWay] = []

    def way(self, w: osmium.osm.Way) -> None:
        if "highway" not in w.tags:
            return
        coords: list[tuple[float, float]] = []
        try:
            for node in w.nodes:
                if not node.location.valid():
                    return  # incomplete way — skip
                coords.append((node.location.lon, node.location.lat))
        except osmium.InvalidLocationError:
            return
        if len(coords) < 2:
            return
        attrs = {t.k: t.v for t in w.tags}
        self.ways.append(_ParsedWay(osm_way_id=w.id, coords=coords, attrs=attrs))


def _http_last_modified_to_date(headers: httpx.Headers) -> date | None:
    raw = headers.get("Last-Modified")
    if not raw:
        return None
    parsed = eut.parsedate_to_datetime(raw)
    return parsed.astimezone(UTC).date()


def _content_disposition_filename(headers: httpx.Headers) -> str | None:
    raw = headers.get("Content-Disposition")
    if not raw:
        return None
    msg = Message()
    msg["Content-Disposition"] = raw
    return msg.get_filename()


@dataclass
class OsmiumOSMSource:
    """Concrete `OSMSource` backed by `osmium`.

    Args:
        prefetched: If set, `fetch()` is a no-op aside from recording
            metadata. Tests use this to point at committed fixtures.
        snapshot_date_for_prefetched: Override for the snapshot date when
            using `prefetched`. Defaults to the file's mtime as a date.
    """

    prefetched: Path | None = None
    snapshot_date_for_prefetched: date | None = None

    def fetch(
        self,
        bbox: tuple[float, float, float, float],
        into_path: Path,
    ) -> SnapshotMetadata:
        del bbox  # Geofabrik publishes country-level extracts; bbox applies at parse time.

        if self.prefetched is not None:
            local = self.prefetched
            snapshot_date = self.snapshot_date_for_prefetched or _file_mtime_date(local)
            return SnapshotMetadata(
                osm_snapshot_date=snapshot_date,
                source_url=f"file://{local.resolve().as_posix()}",
                local_path=local,
                size_bytes=local.stat().st_size,
                sha256=_sha256_of(local),
            )

        # Live fetch: stream Geofabrik to into_path. Caller supplies the URL
        # via the OSMSource subclass that wraps this adapter; bare
        # OsmiumOSMSource expects `prefetched`. The CLI in 1.4.6 wires this
        # up against the city config.
        raise RuntimeError(
            "OsmiumOSMSource.fetch() requires `prefetched` or a subclass that overrides "
            "fetch() with a real URL. The CLI wires this up."
        )

    def parse(
        self,
        path: Path,
        bbox: tuple[float, float, float, float],
    ) -> Iterable[RoadSegment]:
        log.info(
            "osm.parse.start",
            path=str(path),
            bbox=bbox,
        )
        handler = _HighwayHandler()
        handler.apply_file(str(path), locations=True)

        kept = 0
        skipped = 0
        for parsed in handler.ways:
            # Bounding-box clip: keep the way if any vertex is inside the
            # bbox. Edge segments crossing the boundary are kept whole;
            # downstream consumers may further intersect with the bbox
            # polygon if precise clipping is needed.
            if not any(_coord_in_bbox(lon, lat, bbox) for lon, lat in parsed.coords):
                skipped += 1
                continue
            kept += 1
            yield RoadSegment(
                osm_way_id=parsed.osm_way_id,
                geometry=LineString(parsed.coords),
                attrs=dict(parsed.attrs),
            )

        log.info("osm.parse.done", kept=kept, skipped=skipped)


# --- file helpers ---------------------------------------------------------
def _file_mtime_date(path: Path) -> date:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).date()


def _sha256_of(path: Path) -> str:
    h = sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# --- HTTP-fetching subclass used by the CLI -------------------------------
@dataclass
class GeofabrikOSMSource(OsmiumOSMSource):
    """Concrete adapter that downloads from a Geofabrik URL on `fetch()`.

    Used by `make seed` (Task 1.4.6) via `ingestion/cli.py`.
    """

    url: str = ""

    def fetch(
        self,
        bbox: tuple[float, float, float, float],
        into_path: Path,
    ) -> SnapshotMetadata:
        del bbox
        if not self.url:
            raise ValueError("GeofabrikOSMSource requires a `url`.")
        into_path.parent.mkdir(parents=True, exist_ok=True)

        # Conditional download: re-use existing file if upstream is unchanged.
        head_resp: httpx.Response | None = None
        try:
            head_resp = httpx.head(self.url, follow_redirects=True, timeout=30.0)
        except httpx.HTTPError as e:
            log.warning("osm.fetch.head_failed", url=self.url, error=str(e))

        if (
            into_path.exists()
            and head_resp is not None
            and head_resp.status_code == 200
            and "Content-Length" in head_resp.headers
            and into_path.stat().st_size == int(head_resp.headers["Content-Length"])
        ):
            log.info("osm.fetch.cache_hit", path=str(into_path))
            snapshot_date = _http_last_modified_to_date(head_resp.headers) or _file_mtime_date(
                into_path
            )
            return SnapshotMetadata(
                osm_snapshot_date=snapshot_date,
                source_url=self.url,
                local_path=into_path,
                size_bytes=into_path.stat().st_size,
                sha256=_sha256_of(into_path),
            )

        log.info("osm.fetch.start", url=self.url, into=str(into_path))
        with (
            httpx.stream(
                "GET", self.url, follow_redirects=True, timeout=httpx.Timeout(300.0, connect=30.0)
            ) as resp,
            into_path.open("wb") as out,
        ):
            resp.raise_for_status()
            for chunk in resp.iter_bytes(chunk_size=1 << 16):
                out.write(chunk)
            headers = resp.headers

        snapshot_date = _http_last_modified_to_date(headers) or _file_mtime_date(into_path)
        log.info("osm.fetch.done", path=str(into_path), size=into_path.stat().st_size)
        return SnapshotMetadata(
            osm_snapshot_date=snapshot_date,
            source_url=self.url,
            local_path=into_path,
            size_bytes=into_path.stat().st_size,
            sha256=_sha256_of(into_path),
        )


def _iter_unused() -> Iterator[None]:  # pragma: no cover (kept for completeness)
    return iter(())
