import { describe, expect, it } from "vitest";

import { CONFIG_DOMAINS, domainFromName, historyHref } from "./domains";
import {
  characterCount,
  customize,
  isOverridden,
  overridesFromBody,
  overridesToBody,
  PROMPT_BOUNDS,
  resetToDefault,
  setOverride,
  validatePromptsForm,
} from "./prompts-form";

const DEFAULT = "You are a careful analyst. Answer briefly.";

describe("prompts form", () => {
  it("starts empty when nothing is saved and keeps saved overrides", () => {
    expect(overridesFromBody(null)).toEqual({});
    expect(overridesFromBody({ schema_version: 1, overrides: { news_digest: DEFAULT } })).toEqual({ news_digest: DEFAULT });
  });

  it("customize copies the default; reset removes the override", () => {
    const started = customize({}, "chat_assistant", DEFAULT);
    expect(started).toEqual({ chat_assistant: DEFAULT });
    expect(isOverridden(started, "chat_assistant")).toBe(true);
    expect(isOverridden(started, "news_digest")).toBe(false);

    const edited = setOverride(started, "chat_assistant", `${DEFAULT} Be terse.`);
    // Customize again keeps the edit.
    expect(customize(edited, "chat_assistant", DEFAULT)).toBe(edited);

    const reset = resetToDefault(edited, "chat_assistant");
    expect(reset).toEqual({});
    expect(resetToDefault(reset, "chat_assistant")).toBe(reset);
  });

  it("builds a body with sorted keys so edit order does not make it dirty", () => {
    const a = overridesToBody({ news_digest: DEFAULT, chat_assistant: DEFAULT });
    const b = overridesToBody({ chat_assistant: DEFAULT, news_digest: DEFAULT });
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
    expect(a).toEqual({ schema_version: 1, overrides: { chat_assistant: DEFAULT, news_digest: DEFAULT } });
    expect(overridesToBody({})).toEqual({ schema_version: 1, overrides: {} });
  });

  it("mirrors the backend's length bounds", () => {
    expect(validatePromptsForm({ chat_assistant: DEFAULT })).toEqual({});
    expect(validatePromptsForm({ chat_assistant: "short" })["overrides.chat_assistant"]).toHaveLength(1);
    expect(validatePromptsForm({ chat_assistant: "x".repeat(PROMPT_BOUNDS.min) })).toEqual({});
    expect(validatePromptsForm({ chat_assistant: "x".repeat(PROMPT_BOUNDS.max + 1) })["overrides.chat_assistant"]).toEqual([
      "At most 50,000 characters.",
    ]);
  });

  it("counts characters, not UTF-16 units", () => {
    expect(characterCount("")).toBe("0 characters");
    expect(characterCount("a")).toBe("1 character");
    expect(characterCount("😀😀")).toBe("2 characters");
    expect(characterCount("x".repeat(1234))).toBe("1,234 characters");
  });

  it("is a config domain with an editor and a history page", () => {
    expect(CONFIG_DOMAINS.prompts.editor).toBe("/config/prompts");
    expect(domainFromName("prompts")).toBe("prompts");
    expect(historyHref("prompts")).toBe("/config/history/prompts");
  });
});
