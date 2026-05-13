/**
 * Typed wrappers around react-redux's hooks so call sites get proper
 * inference for our `RootState` and `AppDispatch`.
 */

import { useDispatch, useSelector, type TypedUseSelectorHook } from "react-redux";

import type { AppDispatch, RootState } from "./store";

export const useAppDispatch = () => useDispatch<AppDispatch>();
export const useAppSelector: TypedUseSelectorHook<RootState> = useSelector;
