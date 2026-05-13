# state/

Redux Toolkit slices. **UI state only.** Server state is owned by TanStack
Query (`data/`); putting it in Redux is rejected at review.

Phase 1 has exactly one slice (`viewport`) — small on purpose. Phase 2 adds
`scrubber`; Phase 3 adds `selection`; Phase 5 adds `delta-comparison-mode`.

What lives here:

- Cross-cutting UI state: viewport, active layers, scrubber position,
  segment selection, delta mode.

What does **not** live here:

- Server data (segment details, freshness, tiles) → `data/`
- Pure types/transforms → `domain/`
- Component-local state (use `useState`) → component files
