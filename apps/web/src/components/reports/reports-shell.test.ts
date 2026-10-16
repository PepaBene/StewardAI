import { describe, expect, it } from "vitest";

import { foldQuiet, type ShellItem } from "./reports-shell";

const item = (id: number, quiet = false): ShellItem => ({
  id,
  href: `/reports/${id}`,
  kind: "Daily",
  dateLabel: `d${id}`,
  at: "2026-10-16T07:00:00Z",
  title: "t",
  quiet,
});

describe("foldQuiet", () => {
  it("folds runs of two or more quiet reports into one entry", () => {
    const entries = foldQuiet([item(1), item(2, true), item(3, true), item(4), item(5, true)], null);
    expect(entries.map((e) => (e.type === "item" ? e.item.id : e.items.map((i) => i.id)))).toEqual([1, [2, 3], 4, 5]);
  });

  it("keeps the selected report visible even when it is quiet", () => {
    const entries = foldQuiet([item(1, true), item(2, true), item(3, true)], 2);
    expect(entries.map((e) => e.type)).toEqual(["item", "item", "item"]);
  });
});
