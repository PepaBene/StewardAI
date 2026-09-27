import { describe, expect, it } from "vitest";

import { noteDraftFor } from "./save-note";

describe("noteDraftFor", () => {
  it("keeps the note while the save has not gone through (failed save, 409, preview cancelled)", () => {
    const draft = { note: "raise the cap", forVersion: 4 };
    expect(noteDraftFor(draft, 4)).toBe(draft);
  });

  it("clears the note once a new version is active", () => {
    expect(noteDraftFor({ note: "raise the cap", forVersion: 4 }, 5)).toEqual({ note: "", forVersion: 5 });
  });

  it("clears it after the very first save too", () => {
    expect(noteDraftFor({ note: "first", forVersion: null }, 1)).toEqual({ note: "", forVersion: 1 });
  });
});
