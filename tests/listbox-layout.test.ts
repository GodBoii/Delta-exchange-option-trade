import { describe, expect, it } from "vitest";
import { listboxLayout } from "@/lib/listbox-layout";

describe("listbox viewport placement", () => {
  const viewport = { width: 390, height: 844 };
  it("opens down when the measured options fit", () => {
    expect(listboxLayout({ top: 200, bottom: 244, left: 16, width: 358 }, viewport, 140))
      .toEqual({ top: 250, left: 16, width: 358, maxHeight: 140, origin: "top" });
  });
  it("anchors a short menu immediately above its trigger near the bottom", () => {
    expect(listboxLayout({ top: 760, bottom: 804, left: 16, width: 358 }, viewport, 116))
      .toMatchObject({ top: 638, maxHeight: 116, origin: "bottom" });
  });
  it("shrinks to the available space on short landscape screens", () => {
    const result = listboxLayout({ top: 140, bottom: 184, left: 10, width: 700 }, { width: 720, height: 320 }, 500);
    expect(result).toMatchObject({ top: 12, maxHeight: 122, origin: "bottom", left: 12, width: 696 });
    expect(result.top + result.maxHeight).toBeLessThanOrEqual(308);
  });
  it("clamps a trigger that reaches the right edge", () => {
    expect(listboxLayout({ top: 50, bottom: 94, left: 300, width: 250 }, viewport, 90))
      .toMatchObject({ left: 128, width: 250 });
  });
  it("caps a large grouped list without adding a gap above the trigger", () => {
    expect(listboxLayout({ top: 600, bottom: 644, left: 20, width: 300 }, viewport, 2000))
      .toMatchObject({ origin: "bottom", maxHeight: 288, top: 306 });
  });
});
