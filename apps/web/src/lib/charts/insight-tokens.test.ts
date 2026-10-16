import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/* The insight roles (plan I0.6) exist in both themes and as Tailwind colours. */
const css = readFileSync(join(__dirname, "..", "..", "app", "tokens.css"), "utf8");

const ROLES = [
  "chart-forecast",
  "chart-fair-value",
  "tier-low",
  "tier-low-subtle",
  "tier-medium",
  "tier-medium-subtle",
  "tier-high",
  "tier-high-subtle",
  "score-mark-strong",
  "score-mark",
  "score-mark-soft",
  "score-mark-track",
  "score-mark-gap",
];

function block(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  return css.slice(start, css.indexOf("\n}", start));
}

describe("insight tokens", () => {
  it.each(ROLES)("%s is defined in light and dark and exposed to Tailwind", (role) => {
    expect(block(":root")).toMatch(new RegExp(`--${role}:`));
    expect(block(".dark")).toMatch(new RegExp(`--${role}:`));
    expect(css).toContain(`--color-${role}: var(--${role});`);
  });

  it("tiers reuse the state roles rather than new hues", () => {
    for (const theme of [block(":root"), block(".dark")]) {
      expect(theme).toContain("--tier-low: var(--loss);");
      expect(theme).toContain("--tier-medium: var(--warning-signal);");
      expect(theme).toContain("--tier-high: var(--gain);");
    }
  });

  it("forecast and fair value differ from the history line in both themes", () => {
    for (const theme of [block(":root"), block(".dark")]) {
      const forecast = /--chart-forecast: (#[0-9a-f]{6})/.exec(theme)?.[1];
      const fair = /--chart-fair-value: (#[0-9a-f]{6})/.exec(theme)?.[1];
      const accent = /--accent: (#[0-9a-f]{6})/.exec(theme)?.[1];
      expect(new Set([forecast, fair, accent]).size).toBe(3);
    }
  });
});
