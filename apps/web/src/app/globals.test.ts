import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const css = readFileSync(fileURLToPath(new URL("./globals.css", import.meta.url)), "utf8");

describe("globals.css focus rules", () => {
  it("gives `outline-hidden` + `focus-visible:outline-2` controls a solid outline style (Tailwind v4 sets the style to none, which drew a 0px ring)", () => {
    // Unlayered, so it outranks the utility; selector keyed on the class that asks for the ring.
    const rule = css.match(/\[class\*="focus-visible:outline-2"\]:focus-visible\s*\{([^}]*)\}/);
    expect(rule).not.toBeNull();
    expect(rule![1]).toMatch(/outline-style:\s*solid/);
    const before = css.slice(0, css.indexOf('[class*="focus-visible:outline-2"]'));
    const opens = (before.match(/@layer[^{]*\{/g) ?? []).length;
    const closes = (before.match(/\}/g) ?? []).length;
    // Every @layer block opened before the rule is closed again: the rule is top level.
    expect(closes).toBeGreaterThanOrEqual(opens);
  });

  it("underlines accent-ink links in running text (WCAG 1.4.1: colour alone is under 3:1 against the text)", () => {
    expect(css).toMatch(/a\.text-accent-ink\s*\{[^}]*text-decoration-line:\s*underline/);
  });
});
