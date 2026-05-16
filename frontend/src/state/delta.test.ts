import { describe, expect, it } from "vitest";

import { RunId } from "../domain";
import reducer, {
  clearRuns,
  enterDeltaMode,
  exitDeltaMode,
  setRunA,
  setRunB,
  swapRuns,
  type DeltaState,
} from "./delta";

const RUN_A = RunId("11111111-1111-1111-1111-111111111111");
const RUN_B = RunId("22222222-2222-2222-2222-222222222222");

const initial = (): DeltaState => reducer(undefined, { type: "@@INIT" });

describe("delta slice — initial state", () => {
  it("starts in single mode with both runs null", () => {
    expect(initial()).toEqual({ mode: "single", runA: null, runB: null });
  });
});

describe("delta slice — mode transitions", () => {
  it("enterDeltaMode flips mode to 'delta' without touching runs", () => {
    const state: DeltaState = { mode: "single", runA: RUN_A, runB: null };
    expect(reducer(state, enterDeltaMode())).toEqual({
      mode: "delta",
      runA: RUN_A,
      runB: null,
    });
  });

  it("exitDeltaMode flips mode to 'single' and clears both runs", () => {
    const state: DeltaState = { mode: "delta", runA: RUN_A, runB: RUN_B };
    expect(reducer(state, exitDeltaMode())).toEqual({
      mode: "single",
      runA: null,
      runB: null,
    });
  });

  it("entering delta mode while already in delta mode is a no-op", () => {
    const state: DeltaState = { mode: "delta", runA: RUN_A, runB: RUN_B };
    expect(reducer(state, enterDeltaMode())).toEqual(state);
  });
});

describe("delta slice — run selection", () => {
  it("setRunA assigns runA without touching runB or mode", () => {
    const state: DeltaState = { mode: "delta", runA: null, runB: RUN_B };
    expect(reducer(state, setRunA(RUN_A))).toEqual({
      mode: "delta",
      runA: RUN_A,
      runB: RUN_B,
    });
  });

  it("setRunB assigns runB without touching runA or mode", () => {
    const state: DeltaState = { mode: "delta", runA: RUN_A, runB: null };
    expect(reducer(state, setRunB(RUN_B))).toEqual({
      mode: "delta",
      runA: RUN_A,
      runB: RUN_B,
    });
  });

  it("setRunA accepts null to clear just runA", () => {
    const state: DeltaState = { mode: "delta", runA: RUN_A, runB: RUN_B };
    expect(reducer(state, setRunA(null))).toEqual({
      mode: "delta",
      runA: null,
      runB: RUN_B,
    });
  });

  it("setRunB accepts null to clear just runB", () => {
    const state: DeltaState = { mode: "delta", runA: RUN_A, runB: RUN_B };
    expect(reducer(state, setRunB(null))).toEqual({
      mode: "delta",
      runA: RUN_A,
      runB: null,
    });
  });
});

describe("delta slice — swap", () => {
  it("swapRuns exchanges runA and runB when both are set", () => {
    const state: DeltaState = { mode: "delta", runA: RUN_A, runB: RUN_B };
    expect(reducer(state, swapRuns())).toEqual({
      mode: "delta",
      runA: RUN_B,
      runB: RUN_A,
    });
  });

  it("swapRuns is its own inverse (double-swap returns to original)", () => {
    const state: DeltaState = { mode: "delta", runA: RUN_A, runB: RUN_B };
    const once = reducer(state, swapRuns());
    expect(reducer(once, swapRuns())).toEqual(state);
  });

  it("swapRuns with one slot null moves the value to the other slot", () => {
    const state: DeltaState = { mode: "delta", runA: RUN_A, runB: null };
    expect(reducer(state, swapRuns())).toEqual({
      mode: "delta",
      runA: null,
      runB: RUN_A,
    });
  });

  it("swapRuns with both slots null is a no-op", () => {
    const state: DeltaState = { mode: "delta", runA: null, runB: null };
    expect(reducer(state, swapRuns())).toEqual(state);
  });

  it("swapRuns does not alter mode", () => {
    const state: DeltaState = { mode: "single", runA: RUN_A, runB: RUN_B };
    expect(reducer(state, swapRuns()).mode).toBe("single");
  });
});

describe("delta slice — clear", () => {
  it("clearRuns nulls both runs without touching mode", () => {
    const state: DeltaState = { mode: "delta", runA: RUN_A, runB: RUN_B };
    expect(reducer(state, clearRuns())).toEqual({
      mode: "delta",
      runA: null,
      runB: null,
    });
  });

  it("clearRuns on an already-empty state is a no-op", () => {
    const state: DeltaState = { mode: "delta", runA: null, runB: null };
    expect(reducer(state, clearRuns())).toEqual(state);
  });
});
