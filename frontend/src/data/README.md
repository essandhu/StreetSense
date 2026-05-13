# data/

TanStack Query hooks. **Owns server state.** Server state never lives in
Redux (per CLAUDE.md / tech-stack).

What lives here:

- `useSegmentDetail(id)` — fetches `/segments/{id}`.
- `useFreshness()` — fetches `/admin/freshness`.
- `useTileSourceConfig()` — returns the active city's tile URL pattern.

What does **not** live here:

- Component rendering → `components/`
- UI state (zoom, scrubber, selection) → `state/`
- Pure transforms → `domain/`

Hooks return discriminated `{status, data, error}` shapes from TanStack
Query. Components consume them and render.
