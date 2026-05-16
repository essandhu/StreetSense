/**
 * Tests for useDeltaTileSource (Task 3.7).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { RunId } from "../domain";
import { deltaTileUrl, useDeltaTileSource } from "./useDeltaTileSource";

const RUN_A = RunId("11111111-1111-1111-1111-111111111111");
const RUN_B = RunId("22222222-2222-2222-2222-222222222222");

const _wrap = (qc: QueryClient) => {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
};

describe("deltaTileUrl — pure URL builder", () => {
  it("includes the delta tile layer name and the {z}/{x}/{y} template", () => {
    const url = deltaTileUrl(RUN_A, RUN_B);
    expect(url).toContain("public.road_segments_tile_delta");
    expect(url).toContain("/{z}/{x}/{y}.pbf");
  });

  it("URL-encodes both run UUIDs into query params", () => {
    const url = deltaTileUrl(RUN_A, RUN_B);
    expect(url).toContain(`run_a=${RUN_A}`);
    expect(url).toContain(`run_b=${RUN_B}`);
  });
});

describe("useDeltaTileSource", () => {
  it("returns the URL bundle when both runs are set and distinct", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => useDeltaTileSource(RUN_A, RUN_B), {
      wrapper: _wrap(qc),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.url).toContain("public.road_segments_tile_delta");
    expect(result.current.data?.runA).toBe(RUN_A);
    expect(result.current.data?.runB).toBe(RUN_B);
  });

  it("is disabled when either run is null", () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => useDeltaTileSource(RUN_A, null), {
      wrapper: _wrap(qc),
    });
    expect(result.current.fetchStatus).toBe("idle");
  });

  it("is disabled when both runs are equal", () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => useDeltaTileSource(RUN_A, RUN_A), {
      wrapper: _wrap(qc),
    });
    expect(result.current.fetchStatus).toBe("idle");
  });

  it("re-keys on swap (RUN_A, RUN_B) vs (RUN_B, RUN_A)", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = _wrap(qc);
    const a = renderHook(() => useDeltaTileSource(RUN_A, RUN_B), { wrapper });
    await waitFor(() => expect(a.result.current.isSuccess).toBe(true));
    const urlA = a.result.current.data?.url ?? "";

    const b = renderHook(() => useDeltaTileSource(RUN_B, RUN_A), { wrapper });
    await waitFor(() => expect(b.result.current.isSuccess).toBe(true));
    const urlB = b.result.current.data?.url ?? "";

    expect(urlA).not.toBe(urlB);
  });
});
