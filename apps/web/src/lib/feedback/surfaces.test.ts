import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { FEEDBACK_SURFACES } from "./surfaces";

const ROOT = join(__dirname, "..", "..", "..");

describe("feedback surfaces checklist", () => {
  it("covers every kind of item the API takes feedback on", () => {
    const kinds = new Set(FEEDBACK_SURFACES.flatMap((s) => s.kinds));
    expect([...kinds].sort()).toEqual(["alert", "fact", "news_item", "report_sentence", "story", "verdict"]);
  });

  for (const surface of FEEDBACK_SURFACES) {
    it(`${surface.surface} renders the feedback control for ${surface.kinds.join(", ")}`, () => {
      const path = join(ROOT, surface.file);
      expect(existsSync(path), `${surface.file} exists`).toBe(true);
      const source = readFileSync(path, "utf8");
      expect(source).toContain("<FeedbackControl");
      for (const kind of surface.kinds) {
        expect(source, `${surface.file} marks ${kind}`).toMatch(new RegExp(`kind: "${kind}"`));
      }
    });
  }

  it("is listed in the README", () => {
    const readme = readFileSync(join(ROOT, "README.md"), "utf8");
    for (const surface of FEEDBACK_SURFACES) expect(readme).toContain(surface.surface);
  });
});
