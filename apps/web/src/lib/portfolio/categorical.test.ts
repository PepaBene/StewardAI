import { describe, expect, it } from "vitest";

import { categoricalClasses, categoricalFills } from "./categorical";

describe("categorical colour assignment", () => {
  it("assigns the fixed hue order in first-seen order, muted items neutral", () => {
    const items = [{ muted: false }, { muted: true }, { muted: false }, { muted: false }];
    expect(categoricalClasses(items)).toEqual(["bg-categorical-1", "bg-neutral-signal", "bg-categorical-2", "bg-categorical-3"]);
  });

  it("cycles the 8-slot order for a longer list", () => {
    const items = Array.from({ length: 9 }, () => ({}));
    expect(categoricalClasses(items).at(-1)).toBe("bg-categorical-1");
  });

  it("gives a chart's Cell fill the exact same hue, item for item, as the class-based charts", () => {
    const items = [{ muted: false }, { muted: true }, { muted: false }];
    const classes = categoricalClasses(items);
    const fills = categoricalFills(items);
    // Same slot assignment, just expressed as a CSS var() instead of a Tailwind class.
    expect(fills).toEqual(["var(--categorical-1)", "var(--neutral-signal)", "var(--categorical-2)"]);
    expect(classes.map((c) => c.replace("bg-", ""))).toEqual(fills.map((f) => f.replace("var(--", "").replace(")", "")));
  });
});
