# scoring/junction/

Junction-complexity sub-score — Phase 4's third real risk factor.

## What this measures

Per-segment OSM-topology complexity in `[0, 1]`. Higher scores mean a
more challenging junction for ADAS perception (sharper merges, more
legs, lane-count changes, road-class transitions).

The score combines:

- **Intersection degree** at the segment's endpoints (number of legs
  meeting).
- **Merge-angle sharpness** (minimum angle between this segment and any
  other edge at the same junction).
- **Lane-count changes** between this segment and its endpoint
  neighbors.
- **Road-class transitions** (e.g., motorway → trunk → primary at an
  off-ramp).

## Why it's time-invariant at the per-image scale

OSM topology does not change hourly. The scorer's
`score_for_samples(segment, ats)` API returns the same value for all
24 hours of day. The batched form exists for shape-consistency with the
registry; the underlying compute is single-call per segment.

## Phase 4 status

- **Phase 4.1:** empty scaffold (this README + `__init__.py` +
  `scorer.py`).
- **Phase 4.5.8–4.5.10:** TDD red-phase tests + property tests +
  implementation. The scorer wires into `_SUB_SCORE_REGISTRY` in
  `scoring/run.py` (Phase 4.6.4); flipping
  `is_stub_junction_complexity` from `true` to `false` is the
  ``is_stub_*`` retirement that Phase 4 closes for this sub-score.
- **Phase 4.7:** the junction-complexity layer appears as a toggleable
  secondary layer behind the composite-risk layer in the frontend.

## Related

- [`scoring/interface.py`](../interface.py) — the `SubScorer` Protocol
- [`scoring/run.py`](../run.py) — the `_SUB_SCORE_REGISTRY` seam (extension point 1)
- [`docs/StreetSense_Architecture.md`](../../docs/StreetSense_Architecture.md) §"Per-segment scorers"
- [`conductor/tracks/phase-4-propagator/`](../../conductor/tracks/phase-4-propagator/index.md)
