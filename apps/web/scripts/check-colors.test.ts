import { describe, expect, it } from "vitest";

import { findColorViolations, scanSource } from "./check-colors.mjs";

describe("colour lint", () => {
  it("src/ has no colour literals or palette classes outside tokens.css", () => {
    expect(scanSource()).toEqual([]);
  });

  it.each([
    ["color: #fff;"],
    ['className="bg-[#1a2b3c]"'],
    ["background: rgb(0 0 0);"],
    ["fill: hsl(10 20% 30%);"],
    ["--x: oklch(0.5 0.1 200);"],
    ['className="bg-slate-100"'],
    ['className="dark:text-red-500"'],
    ['className="border-t-gray-200"'],
    ['className="hover:bg-white"'],
  ])("flags %s", (sample) => {
    expect(findColorViolations(sample).length).toBeGreaterThan(0);
  });

  it.each([
    ['className="bg-surface text-text border-border"'],
    ['className="text-gain bg-loss-subtle text-neutral-signal"'],
    ['className="bg-accent/90 ring-focus/30"'],
    ['<a href="#main">skip</a>'],
    ["&#123;"],
  ])("allows %s", (sample) => {
    expect(findColorViolations(sample)).toEqual([]);
  });
});
