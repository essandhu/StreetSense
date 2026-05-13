# scoring/environmental/

Pure-functional environmental scorers (glare, weather). **Phase 2+ — not
populated in Phase 1.**

## Phase 2 plan

- Solar geometry → per-segment glare exposure as a function of time of day
  and day of year.
- Property-tested with `hypothesis` (e.g., glare is symmetric around solar
  noon for east-west roads).
- Deterministic. Same inputs → same outputs.
