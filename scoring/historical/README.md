# scoring/historical/

Historical-correlation sub-score — Phase 4's fourth real risk factor.

## What this measures

Per-segment correlation in `[0, 1]` between local incident density and
segment proximity. Higher scores mean a segment is near a cluster of
historically-reported incidents, weighted by recency.

The score uses a Gaussian kernel density estimate over the
``incidents`` table (migration 0013) with:

- **Configurable radius** (default 50 m) — controls the kernel
  bandwidth.
- **Exponential time decay** (default 365-day half-life) — more recent
  incidents weight more heavily than older ones.

## Why it's time-invariant at the per-image scale

Incident history is a static input at scoring-run time — the
ingestion job (`make ingest-incidents`) runs out-of-band on a separate
cadence (default: once per week). Recency-weighting is computed once
per scoring-run against the run's reference timestamp; the scorer's
`score_for_samples(segment, ats)` API returns the same value for all
24 hours of day.

## Phase 4 status

- **Phase 4.1:** empty scaffold (this README + `__init__.py` +
  `scorer.py`).
- **Phase 4.5.11–4.5.13:** TDD red-phase tests + property tests +
  implementation. Wires into `_SUB_SCORE_REGISTRY` in `scoring/run.py`
  (Phase 4.6.4); flipping `is_stub_historical` from `true` to `false`
  is the ``is_stub_*`` retirement that Phase 4 closes for this
  sub-score.
- **Phase 4.5.1–4.5.7:** the upstream incident-data ingestion landing
  before this scorer can run. See
  [`ingestion/incidents/`](../../ingestion/incidents/README.md).
- **Phase 4.7:** the historical-correlation layer appears as a
  toggleable secondary layer behind the composite-risk layer in the
  frontend.

## Related

- [ADR 0007 — Historical Incident Dataset](../../docs/adr/0007-incident-dataset.md)
- [`scoring/interface.py`](../interface.py) — the `SubScorer` Protocol
- [`scoring/run.py`](../run.py) — the `_SUB_SCORE_REGISTRY` seam
- [`ingestion/incidents/`](../../ingestion/incidents/README.md)
- [`conductor/tracks/phase-4-propagator/`](../../conductor/tracks/phase-4-propagator/index.md)
