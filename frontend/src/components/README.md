# components/

Presentational React components, organized by feature (`Map/`, `Panel/`,
`Scrubber/`, `Detail/`), not by component type. Functional components with
hooks; no class components.

A component:

- Reads from `data/` (server state) and `state/` (UI state) hooks.
- Calls pure helpers from `domain/`.
- Renders DOM / canvases / WebGL.
- Owns local state via `useState` / `useReducer`.

A component does **not**:

- Fetch directly (use a `data/` hook).
- Mutate Redux from inside render (only in event handlers / effects).
- Reach into siblings via DOM queries.

Special case — the MapLibre instance: `Map/Map.tsx` mounts MapLibre once
via a `ref` and controls it imperatively. JSX children do **not**
represent map layers. See the file's docstring.
