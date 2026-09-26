import { describe, expect, it } from "vitest";

import { placePopover } from "./popover";

const viewport = { width: 1000, height: 800 };
const anchor = (top: number, left: number) => ({ top, bottom: top + 20, left, right: left + 30 });

describe("popover placement", () => {
  it("opens below the anchor, aligned with its left edge", () => {
    expect(placePopover(anchor(100, 200), { width: 300, height: 150 }, viewport)).toEqual({ top: 124, left: 200, side: "below" });
  });

  it("opens above when there is no room below", () => {
    expect(placePopover(anchor(700, 200), { width: 300, height: 150 }, viewport)).toEqual({ top: 546, left: 200, side: "above" });
  });

  it("stays inside the viewport horizontally and vertically", () => {
    expect(placePopover(anchor(100, 900), { width: 300, height: 150 }, viewport).left).toBe(692);
    expect(placePopover(anchor(100, -50), { width: 300, height: 150 }, viewport).left).toBe(8);
    const tall = placePopover(anchor(400, 10), { width: 300, height: 900 }, viewport);
    expect(tall.top).toBe(8);
  });
});
