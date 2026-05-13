# benchmarks/

Performance budgets are **hard invariants**. Regressions fail the track —
they are not a "fix later" concern.

## Budgets

| Component | Target | Phase |
|---|---|---|
| Frontend pan/zoom | <100 ms at city scale | 1 |
| API tile endpoint p99 (warm) | <200 ms | 1 |
| API tile endpoint p99 (cold) | <800 ms | 1 |
| Network propagation (~500k edges) | <5 s | 4 |
| End-to-end scoring run | one overnight window per city | 4 |

## Layout

```
benchmarks/
├── api/
│   ├── tile_latency.py
│   └── results/                # JSON results, committed
├── frontend/
│   ├── pan_zoom.spec.ts        # Playwright
│   └── results/
└── propagator/
    ├── (Google Benchmark — Phase 4)
    └── results/
```

Result files are committed so regressions are auditable across commits.
