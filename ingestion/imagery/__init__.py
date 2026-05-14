"""Street-level imagery ingestion — extension point 3.

The :class:`ImageryProvider` protocol is the seam behind which provider
implementations live. Phase 3 ships one concrete provider (Mapillary);
adding a second provider in a later track requires only a new module
implementing the protocol — callers (the ingestion job, the perception
scorer, the API) are unaware of the concrete provider.

See ``docs/adr/0005-imagery-provider.md`` for the Mapillary selection
and license/rate-limit posture.
"""

from __future__ import annotations

from ingestion.imagery.provider import ImageryProvider, ImageryReference, Waypoint

__all__ = ["ImageryProvider", "ImageryReference", "Waypoint"]
