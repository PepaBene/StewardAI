import type { PromptKey, PromptsConfig } from "@/lib/api/client";
import type { FieldErrors } from "@/lib/api/validation-errors";

/*
 * Form state for the prompts config (`PromptsConfig`, generated from the backend's Pydantic
 * model, apps/backend/src/steward/config/prompts.py): the owner's override of each AI step's
 * system prompt, by prompt key. A key without an override uses the built-in default, so
 * "Reset to default" removes the key rather than storing the default's text.
 *
 * `validatePromptsForm` mirrors the backend's length bounds, so a too-short prompt is shown
 * next to its text area before anything is sent.
 */

export const PROMPT_BOUNDS = { min: 20, max: 50_000 } as const;

export type PromptOverrides = Partial<Record<PromptKey, string>>;

export function overridesFromBody(body: PromptsConfig | null): PromptOverrides {
  return { ...(body?.overrides ?? {}) };
}

/** The body to save; keys sorted so the dirty check does not depend on edit order. */
export function overridesToBody(overrides: PromptOverrides): PromptsConfig {
  const sorted: PromptOverrides = {};
  for (const key of (Object.keys(overrides) as PromptKey[]).sort()) {
    const text = overrides[key];
    if (text !== undefined) sorted[key] = text;
  }
  return { schema_version: 1, overrides: sorted };
}

/** Start an override from the built-in text. An existing override is kept. */
export function customize(overrides: PromptOverrides, key: PromptKey, defaultText: string): PromptOverrides {
  return key in overrides ? overrides : { ...overrides, [key]: defaultText };
}

/** Back to the built-in prompt: the override is removed. */
export function resetToDefault(overrides: PromptOverrides, key: PromptKey): PromptOverrides {
  if (!(key in overrides)) return overrides;
  const next = { ...overrides };
  delete next[key];
  return next;
}

export function setOverride(overrides: PromptOverrides, key: PromptKey, text: string): PromptOverrides {
  return { ...overrides, [key]: text };
}

export function isOverridden(overrides: PromptOverrides, key: PromptKey): boolean {
  return overrides[key] !== undefined;
}

/** "1,234 characters" (the backend counts characters, as `str` length). */
export function characterCount(text: string): string {
  const n = [...text].length;
  return `${n.toLocaleString("en-US")} character${n === 1 ? "" : "s"}`;
}

export function validatePromptsForm(overrides: PromptOverrides): FieldErrors {
  const errors: FieldErrors = {};
  for (const [key, text] of Object.entries(overrides)) {
    if (text === undefined) continue;
    const n = [...text].length;
    if (n < PROMPT_BOUNDS.min) {
      errors[`overrides.${key}`] = [`At least ${PROMPT_BOUNDS.min} characters; use Reset to default to go back to the built-in prompt.`];
    } else if (n > PROMPT_BOUNDS.max) {
      errors[`overrides.${key}`] = [`At most ${PROMPT_BOUNDS.max.toLocaleString("en-US")} characters.`];
    }
  }
  return errors;
}
