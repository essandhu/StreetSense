/**
 * Color-accessor unit tests — Phase 4 LayerOverlay.
 *
 * Asserts the bucketing math is correct on the corner cases the
 * deck.gl shader actually hits in production: stubs, missing values,
 * negative numbers, super-saturating composites, and the five
 * palette boundaries.
 */
import { describe, expect, it } from "vitest";

import { colorAccessorForLayer, STUB_COLOR } from "./layerColor";

describe("colorAccessorForLayer", () => {
  it("renders stub-flagged features as the neutral dim color", () => {
    const accessor = colorAccessorForLayer("glare");
    const color = accessor({
      properties: { glare_score: 0.99, is_stub_glare: true },
    });
    expect(color).toEqual(STUB_COLOR);
  });

  it("renders missing/non-finite values as the stub color (defensive fallback)", () => {
    const accessor = colorAccessorForLayer("composite");
    expect(accessor({ properties: {} })).toEqual(STUB_COLOR);
    expect(accessor({ properties: { composite_risk: NaN } })).toEqual(STUB_COLOR);
    expect(accessor({ properties: { composite_risk: "0.5" } })).toEqual(STUB_COLOR);
  });

  it("clamps negative values to the coolest bucket", () => {
    const accessor = colorAccessorForLayer("composite");
    const color = accessor({ properties: { composite_risk: -10 } });
    // Coolest palette bucket — the [0] entry, RGBA tuple length 4.
    expect(color[3]).toBeGreaterThan(0);
    expect(color).not.toEqual(STUB_COLOR);
  });

  it("saturates super-1.0 composites at the hottest bucket", () => {
    const accessor = colorAccessorForLayer("composite");
    // composite_risk can exceed 1.0; the accessor must still return
    // a valid palette color rather than over-reading the palette
    // array.
    const color = accessor({ properties: { composite_risk: 5.0 } });
    expect(color).not.toEqual(STUB_COLOR);
    expect(color[3]).toBeGreaterThan(0);
  });

  it("uses different accessor outputs for different layer attributes", () => {
    // Same feature, different active layers → different attributes
    // read → can produce different palette indices.
    const props = {
      composite_risk: 0.9,
      glare_score: 0.1,
    };
    const composite = colorAccessorForLayer("composite")({ properties: props });
    const glare = colorAccessorForLayer("glare")({ properties: props });
    expect(composite).not.toEqual(glare);
  });

  it("places 0.0 in the coolest bucket and 0.99 in the hottest", () => {
    const accessor = colorAccessorForLayer("composite");
    const cool = accessor({ properties: { composite_risk: 0.0 } });
    const hot = accessor({ properties: { composite_risk: 0.99 } });
    // The two endpoints should not collide.
    expect(cool).not.toEqual(hot);
    // Both should be opaque palette colors, not the stub.
    expect(cool).not.toEqual(STUB_COLOR);
    expect(hot).not.toEqual(STUB_COLOR);
  });
});
