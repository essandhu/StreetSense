# domain/

Pure types and pure functions. **No React, no fetching, no Redux, no
side-effects.**

What lives here:

- Branded ID types (`SegmentId`, `RunId`).
- Discriminated unions for layer/run state.
- Pure helpers that operate on those types (e.g., score → palette bucket).

What does **not** live here:

- React components → `components/`
- TanStack Query hooks → `data/`
- Redux slices → `state/`
- Anything that calls `fetch()` or touches `window`.

Types generated from the API's OpenAPI schema land in `domain/generated/`
and re-export from `domain/index.ts`.
