/**
 * Redux Toolkit store wiring. UI state only (server state lives in
 * TanStack Query — never put it here).
 */

import { configureStore } from "@reduxjs/toolkit";

import viewport from "./viewport";

export const store = configureStore({
  reducer: { viewport },
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
