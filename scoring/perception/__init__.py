"""Perception sub-score — Phase 3's second real risk factor.

Per-segment ``lane_marking_quality`` derived from street-level imagery
(Mapillary) scored by a pretrained semantic-segmentation model served
through ONNX Runtime. Sits behind the :class:`SubScorer` protocol from
``scoring.interface`` — the same seam Phase 2's glare scorer attaches
to — so the scoring-run orchestration in ``scoring.run`` adds perception
by configuration (extension point 1).

See ``docs/adr/0004-perception-model.md`` for the model choice posture
and ``docs/adr/0005-imagery-provider.md`` for the imagery provider.
"""

from __future__ import annotations
