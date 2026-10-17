import { normaliseSeparators } from "@/lib/number-input";
import type { ClaimTarget, VerificationConfig } from "@/lib/api/client";
import type { FieldErrors } from "@/lib/api/validation-errors";

import { fractionToPercent, percentToFraction } from "./strategy-form";

/*
 * Form state for the verification config (`VerificationConfig`, generated from the
 * backend's Pydantic model, apps/backend/src/steward/config/verification.py): the sanity
 * bounds and tolerances of the numeric validator, the corroboration rules, source trust
 * from feedback, and the claim verifier. Fractions (the sanity bands) are typed as
 * percentages; everything else in its own unit. Fields this form does not know are kept, so
 * a field the backend adds later survives a save.
 *
 * `validateVerificationForm` mirrors the backend's bounds, so a mistake is shown next to its
 * field before anything is sent; the API's own 422 answer is shown the same way.
 */

export const CLAIM_TARGETS = ["daily_digest", "weekly_digest", "monthly_digest", "quarterly_digest", "escalation"] as const satisfies readonly ClaimTarget[];

export const CLAIM_TARGET_LABELS: Record<ClaimTarget, string> = {
  daily_digest: "Daily digest",
  weekly_digest: "Weekly digest",
  monthly_digest: "Monthly digest",
  quarterly_digest: "Quarterly digest",
  escalation: "Impact notes",
};

export const CLAIM_MODES = [
  { value: "strip", label: "Strip: remove an unsupported sentence" },
  { value: "mark", label: "Mark: keep it, marked unsupported" },
] as const;

/** The backend's defaults, for when nothing is saved yet (a 404). */
export const DEFAULT_VERIFICATION: VerificationConfig = {
  schema_version: 1,
  sanity_max_change_qoq: 0.5,
  sanity_overrides: {},
  stated_ratio_tolerance_pp: 0.15,
  period_end_tolerance_days: 10,
  max_report_lag_days: 150,
  corroboration: { tier1_confirms_alone: true, independent_sources_to_confirm: 2 },
  trust: { wrong_penalty: 0.2, useful_reward: 0.05, demote_below: 0.6 },
  claim_verifier: {
    enabled: true,
    model: "gpt-6-luna",
    effort: null,
    mode: "strip",
    numeric_check: true,
    date_check: true,
    quote_check: true,
    applies_to: [...CLAIM_TARGETS],
  },
};

export type OverrideRow = {
  key: string;
  metric: string;
  /** No bound for this metric (null in the body). */
  disabled: boolean;
  /** The band as a percentage ("80" = ±80 %). */
  band: string;
};

type Rest = Record<string, unknown>;

export type VerificationForm = {
  sanityEnabled: boolean;
  /** Percent: "50" = ±50 % quarter on quarter. */
  sanityMaxChange: string;
  overrides: OverrideRow[];
  statedRatioTolerancePp: string;
  periodEndToleranceDays: string;
  maxReportLagDays: string;
  tier1ConfirmsAlone: boolean;
  independentSources: string;
  wrongPenalty: string;
  usefulReward: string;
  demoteBelow: string;
  claimEnabled: boolean;
  claimModel: string;
  /** "" = not sent (the model's default). */
  claimEffort: string;
  claimMode: "strip" | "mark";
  numericCheck: boolean;
  appliesTo: ClaimTarget[];
  /** Fields the form does not edit, kept as loaded. */
  rest: { top: Rest; corroboration: Rest; trust: Rest; claimVerifier: Rest };
};

let rowCounter = 0;
export function newOverride(metric = "", band = ""): OverrideRow {
  rowCounter += 1;
  return { key: `override-${rowCounter}`, metric, disabled: false, band };
}

function text(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value) ? "" : String(value);
}

function numberOrText(value: string): number | string {
  const trimmed = normaliseSeparators(value);
  const n = Number(trimmed);
  return trimmed !== "" && Number.isFinite(n) ? n : value.trim();
}

export function verificationFromBody(body: VerificationConfig | null): VerificationForm {
  const b = { ...DEFAULT_VERIFICATION, ...(body ?? {}) };
  const {
    schema_version: _schema,
    sanity_max_change_qoq,
    sanity_overrides,
    stated_ratio_tolerance_pp,
    period_end_tolerance_days,
    max_report_lag_days,
    corroboration,
    trust,
    claim_verifier,
    ...top
  } = b;
  void _schema;
  const { tier1_confirms_alone, independent_sources_to_confirm, ...corroborationRest } = {
    ...DEFAULT_VERIFICATION.corroboration!,
    ...(corroboration ?? {}),
  };
  const { wrong_penalty, useful_reward, demote_below, ...trustRest } = { ...DEFAULT_VERIFICATION.trust!, ...(trust ?? {}) };
  const { enabled, model, effort, mode, numeric_check, applies_to, ...claimRest } = {
    ...DEFAULT_VERIFICATION.claim_verifier!,
    ...(claim_verifier ?? {}),
  };
  return {
    sanityEnabled: sanity_max_change_qoq !== null,
    sanityMaxChange: sanity_max_change_qoq === null ? fractionToPercent(0.5) : fractionToPercent(sanity_max_change_qoq),
    overrides: Object.entries(sanity_overrides ?? {}).map(([metric, band]) => ({
      ...newOverride(metric, band === null ? "" : fractionToPercent(band)),
      disabled: band === null,
    })),
    statedRatioTolerancePp: text(stated_ratio_tolerance_pp),
    periodEndToleranceDays: text(period_end_tolerance_days),
    maxReportLagDays: text(max_report_lag_days),
    tier1ConfirmsAlone: tier1_confirms_alone,
    independentSources: text(independent_sources_to_confirm),
    wrongPenalty: text(wrong_penalty),
    usefulReward: text(useful_reward),
    demoteBelow: text(demote_below),
    claimEnabled: enabled,
    claimModel: model,
    claimEffort: effort ?? "",
    claimMode: mode,
    numericCheck: numeric_check,
    appliesTo: CLAIM_TARGETS.filter((t) => (applies_to ?? []).includes(t)),
    rest: { top, corroboration: corroborationRest, trust: trustRest, claimVerifier: claimRest },
  };
}

export function verificationToBody(form: VerificationForm): VerificationConfig {
  const overrides: Record<string, number | null> = {};
  for (const row of form.overrides) {
    const metric = row.metric.trim();
    if (!metric && !row.band.trim() && !row.disabled) continue; // an empty new row
    overrides[metric] = row.disabled ? null : (percentToFraction(row.band) as number);
  }
  return {
    ...form.rest.top,
    schema_version: 1,
    sanity_max_change_qoq: form.sanityEnabled ? (percentToFraction(form.sanityMaxChange) as number) : null,
    sanity_overrides: overrides,
    stated_ratio_tolerance_pp: numberOrText(form.statedRatioTolerancePp) as number,
    period_end_tolerance_days: numberOrText(form.periodEndToleranceDays) as number,
    max_report_lag_days: numberOrText(form.maxReportLagDays) as number,
    corroboration: {
      ...form.rest.corroboration,
      tier1_confirms_alone: form.tier1ConfirmsAlone,
      independent_sources_to_confirm: numberOrText(form.independentSources) as number,
    },
    trust: {
      ...form.rest.trust,
      wrong_penalty: numberOrText(form.wrongPenalty) as number,
      useful_reward: numberOrText(form.usefulReward) as number,
      demote_below: numberOrText(form.demoteBelow) as number,
    },
    claim_verifier: {
      ...form.rest.claimVerifier,
      enabled: form.claimEnabled,
      model: form.claimModel.trim(),
      effort: (form.claimEffort || null) as NonNullable<VerificationConfig["claim_verifier"]>["effort"],
      mode: form.claimMode,
      numeric_check: form.numericCheck,
      applies_to: CLAIM_TARGETS.filter((t) => form.appliesTo.includes(t)),
    },
  } as VerificationConfig;
}

const METRIC = /^[a-z][a-z0-9_]{0,63}$/;
const MODEL = /^(gpt|gemini)-[a-z0-9.-]{1,64}$/;

function num(value: string): number | null {
  const t = normaliseSeparators(value).replace(/\s*%$/, "");
  if (!/^[+-]?(\d+\.?\d*|\.\d+)$/.test(t)) return null;
  return Number(t);
}

function checkNumber(
  errors: FieldErrors,
  path: string,
  value: string,
  { min, max, integer = false, exclusiveMin = false }: { min?: number; max?: number; integer?: boolean; exclusiveMin?: boolean },
) {
  const n = num(value);
  if (!value.trim()) errors[path] = ["Required."];
  else if (n === null) errors[path] = ["Enter a number."];
  else if (integer && !Number.isInteger(n)) errors[path] = ["Enter a whole number."];
  else if (min !== undefined && (exclusiveMin ? n <= min : n < min)) errors[path] = [exclusiveMin ? `Must be more than ${min}.` : `At least ${min}.`];
  else if (max !== undefined && n > max) errors[path] = [`At most ${max}.`];
}

/**
 * Problems the backend would reject, keyed by the body's field paths
 * ("trust.wrong_penalty", "sanity_overrides.revenue"), so each shows next to its field.
 */
export function validateVerificationForm(form: VerificationForm): FieldErrors {
  const errors: FieldErrors = {};
  if (form.sanityEnabled) checkNumber(errors, "sanity_max_change_qoq", form.sanityMaxChange, { min: 0, exclusiveMin: true });
  const seen = new Set<string>();
  form.overrides.forEach((row) => {
    const metric = row.metric.trim();
    if (!metric && !row.band.trim() && !row.disabled) return;
    const path = `sanity_overrides.${metric || "?"}`;
    if (!METRIC.test(metric)) {
      errors[path] = ["A metric id: lower-case letters, digits and _, starting with a letter (e.g. revenue)."];
      return;
    }
    if (seen.has(metric)) {
      errors[path] = [`${metric} is listed twice.`];
      return;
    }
    seen.add(metric);
    if (!row.disabled) checkNumber(errors, path, row.band, { min: 0, exclusiveMin: true });
  });
  checkNumber(errors, "stated_ratio_tolerance_pp", form.statedRatioTolerancePp, { min: 0 });
  checkNumber(errors, "period_end_tolerance_days", form.periodEndToleranceDays, { min: 0, max: 31, integer: true });
  checkNumber(errors, "max_report_lag_days", form.maxReportLagDays, { min: 30, max: 400, integer: true });
  checkNumber(errors, "corroboration.independent_sources_to_confirm", form.independentSources, { min: 2, max: 5, integer: true });
  checkNumber(errors, "trust.wrong_penalty", form.wrongPenalty, { min: 0, max: 1 });
  checkNumber(errors, "trust.useful_reward", form.usefulReward, { min: 0, max: 1 });
  checkNumber(errors, "trust.demote_below", form.demoteBelow, { min: 0, max: 1 });
  if (!MODEL.test(form.claimModel.trim())) errors["claim_verifier.model"] = ["A gpt- or gemini- model id, e.g. gpt-6-luna."];
  return errors;
}

/** Server errors on "sanity_overrides.<metric>" belong to that row. */
export function overrideErrorPath(row: Pick<OverrideRow, "metric">): string {
  return `sanity_overrides.${row.metric.trim() || "?"}`;
}
