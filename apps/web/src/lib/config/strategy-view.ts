import type { ConfigVersionMeta, StrategyConfig } from "@/lib/api/client";

import { ON_BREACH, RULE_TYPES, type RuleForm } from "./strategy-form";

/*
 * Derivations for the strategy page's read-outs: per-domain version numbers (v1, v2, ...),
 * the scale and state of the target-and-band meters, a one-line summary per rule, and the
 * list of unsaved changes between the active body and the draft.
 */

// ---------------------------------------------------------------------------
// Versions
// ---------------------------------------------------------------------------

/**
 * Per-domain version numbers: the oldest saved version is v1. Version ids are global across
 * domains, so "v3" (the third strategy save) reads better than "#57". Keyed by version id.
 */
export function versionNumbers(versions: readonly Pick<ConfigVersionMeta, "id" | "created_at">[]): Map<number, number> {
  const ordered = [...versions].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id - b.id);
  return new Map(ordered.map((v, i) => [v.id, i + 1]));
}

// ---------------------------------------------------------------------------
// Target meters
// ---------------------------------------------------------------------------

/** A finite number, else null (the form sends text the backend rejects as typed). */
export function numeric(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * The right end of the meters' shared 0..max scale: the largest target + band, hard limit or
 * current weight, rounded up to a 5 % step (at least 10 %, at most 100 %).
 */
export function targetScale(values: readonly (number | null | undefined)[]): number {
  const reach = Math.max(0, ...values.filter((v): v is number => typeof v === "number" && Number.isFinite(v)));
  const stepped = Math.ceil(reach / 0.05 - 1e-6) * 0.05;
  return Math.min(1, Math.max(0.1, Math.round(stepped * 100) / 100));
}

export type BandState = "no_data" | "no_target" | "below_band" | "in_band" | "above_band" | "above_limit";

/** Where a current weight sits against a (draft) target, band and hard limit. */
export function bandState(
  weight: number | null,
  target: number | null,
  band: number | null,
  hardLimit: number | null = null,
): BandState {
  if (weight === null) return "no_data";
  if (hardLimit !== null && weight > hardLimit + 1e-12) return "above_limit";
  if (target === null) return "no_target";
  const half = band ?? 0;
  if (weight < target - half - 1e-12) return "below_band";
  if (weight > target + half + 1e-12) return "above_band";
  return "in_band";
}

export const BAND_STATE_LABEL: Record<BandState, string> = {
  no_data: "not held",
  no_target: "no target",
  below_band: "below band",
  in_band: "in band",
  above_band: "above band",
  above_limit: "above hard limit",
};

/** Position + cash targets and the cash share, for the "100.0 % allocated incl. cash" read-out. */
export function allocation(body: StrategyConfig): { total: number; cash: number | null } {
  const positions = (body.target_positions ?? []).reduce((s, p) => s + (numeric(p.target_weight) ?? 0), 0);
  const cash = body.cash_target ? (numeric(body.cash_target.target_weight) ?? 0) : null;
  return { total: positions + (cash ?? 0), cash };
}

// ---------------------------------------------------------------------------
// Rules
// ---------------------------------------------------------------------------

const OPERATOR_TEXT: Record<RuleForm["operator"], string> = { "<": "<", "<=": "≤", ">": ">", ">=": "≥", "==": "=" };

/** "NVDA_US_EQ weight ≤ 9 %", "sector · Semiconductors ≤ 25 %", "Positions ≥ 10". */
export function ruleSummary(rule: RuleForm): string {
  const op = OPERATOR_TEXT[rule.operator] ?? rule.operator;
  const value = rule.value.trim() || "?";
  switch (rule.type) {
    case "position_weight":
      return `${rule.ticker.trim() ? `${rule.ticker.trim()} weight` : "Every position"} ${op} ${value} %`;
    case "category_weight": {
      const dimension = rule.dimension.trim() || "?";
      return `${rule.bucket.trim() ? `${dimension} · ${rule.bucket.trim()}` : `Every ${dimension} bucket`} ${op} ${value} %`;
    }
    case "cash_weight":
      return `Cash ${op} ${value} %`;
    case "position_count":
      return `Positions ${op} ${value}`;
  }
}

export function ruleTypeLabel(type: RuleForm["type"]): string {
  return RULE_TYPES.find((t) => t.value === type)?.label ?? type;
}

export function severityLabel(value: string | null | undefined): string {
  return ON_BREACH.find((o) => o.value === value)?.label ?? String(value ?? "");
}

// ---------------------------------------------------------------------------
// Unsaved changes
// ---------------------------------------------------------------------------

function pct(value: unknown): string {
  const n = numeric(value);
  if (n === null) return value === null || value === undefined || value === "" ? "none" : String(value);
  return `${Math.round(n * 10000) / 100} %`;
}

function pp(value: unknown): string {
  const n = numeric(value);
  if (n === null) return value === null || value === undefined || value === "" ? "none" : String(value);
  return `±${Math.round(n * 10000) / 100} pp`;
}

/** JSON with object keys sorted and absent/undefined fields treated as null. */
function stable(value: unknown): string {
  if (value === undefined || value === null) return "null";
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== undefined && v !== null)
      .sort(([a], [b]) => a.localeCompare(b));
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${stable(v)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

const same = (a: unknown, b: unknown) => stable(a) === stable(b);

const BASICS: { key: keyof StrategyConfig; label: string; show?: (v: unknown) => string }[] = [
  { key: "base_currency", label: "Base currency", show: (v) => String(v ?? "") },
  { key: "fx_source", label: "FX rates", show: (v) => String(v ?? "default") },
  { key: "weight_basis", label: "Weight basis", show: (v) => String(v ?? "") },
  { key: "horizon_years", label: "Horizon", show: (v) => (v == null ? "none" : `${String(v)} y`) },
  { key: "goals", label: "Goals" },
  { key: "notes", label: "Notes for the AI" },
  { key: "hard_limit_severity", label: "Hard-limit severity", show: (v) => severityLabel(v as string) },
  { key: "band_breach_severity", label: "Band severity", show: (v) => severityLabel(v as string) },
];

/**
 * The draft's changes against the active body, one short line each ("VWCEd_EQ target 22 % →
 * 20 %", "Rule nvda-cap off", "New rule semis-cap"). `before` null: nothing saved yet.
 */
export function strategyChanges(before: StrategyConfig | null, after: StrategyConfig): string[] {
  if (!before) return ["First version"];
  const out: string[] = [];

  for (const { key, label, show } of BASICS) {
    if (same(before[key], after[key])) continue;
    out.push(show ? `${label} ${show(before[key])} → ${show(after[key])}` : `${label} edited`);
  }
  if (!same(before.categories, after.categories)) out.push("Dimensions changed");

  const beforeClass = before.classifications ?? {};
  const afterClass = after.classifications ?? {};
  const classChanged = [...new Set([...Object.keys(beforeClass), ...Object.keys(afterClass)])].filter(
    (t) => !same(beforeClass[t], afterClass[t]),
  );
  if (classChanged.length === 1) out.push(`${classChanged[0] || "A holding"} classification changed`);
  else if (classChanged.length > 1) out.push(`${classChanged.length} classifications changed`);

  const bc = before.cash_target;
  const ac = after.cash_target;
  if (!bc && ac) out.push(`Cash target ${pct(ac.target_weight)} added`);
  else if (bc && !ac) out.push("Cash target removed");
  else if (bc && ac) {
    if (!same(bc.target_weight, ac.target_weight)) out.push(`Cash target ${pct(bc.target_weight)} → ${pct(ac.target_weight)}`);
    if (!same(bc.band, ac.band)) out.push(`Cash band ${pp(bc.band)} → ${pp(ac.band)}`);
  }

  const beforePos = new Map((before.target_positions ?? []).map((p) => [p.ticker, p]));
  const afterPos = new Map((after.target_positions ?? []).map((p) => [p.ticker, p]));
  for (const [ticker, p] of afterPos) {
    const old = beforePos.get(ticker);
    const name = ticker || "New position";
    if (!old) {
      out.push(`New target ${name} ${pct(p.target_weight)}`);
      continue;
    }
    if (!same(old.target_weight, p.target_weight)) out.push(`${name} target ${pct(old.target_weight)} → ${pct(p.target_weight)}`);
    if (!same(old.band, p.band)) out.push(`${name} band ${pp(old.band)} → ${pp(p.band)}`);
    if (!same(old.hard_limit, p.hard_limit)) out.push(`${name} hard limit ${pct(old.hard_limit)} → ${pct(p.hard_limit)}`);
  }
  for (const ticker of beforePos.keys()) if (!afterPos.has(ticker)) out.push(`Target ${ticker} removed`);

  const catKey = (c: { dimension: string; bucket: string }) => `${c.dimension} · ${c.bucket}`;
  const beforeCat = new Map((before.target_categories ?? []).map((c) => [catKey(c), c]));
  const afterCat = new Map((after.target_categories ?? []).map((c) => [catKey(c), c]));
  for (const [key, c] of afterCat) {
    const old = beforeCat.get(key);
    if (!old) out.push(`New category target ${key} ${pct(c.target_weight)}`);
    else {
      if (!same(old.target_weight, c.target_weight)) out.push(`${key} target ${pct(old.target_weight)} → ${pct(c.target_weight)}`);
      if (!same(old.band, c.band)) out.push(`${key} band ${pp(old.band)} → ${pp(c.band)}`);
    }
  }
  for (const key of beforeCat.keys()) if (!afterCat.has(key)) out.push(`Category target ${key} removed`);

  const beforeRules = new Map((before.rules ?? []).map((r) => [r.id, r]));
  const afterRules = new Map((after.rules ?? []).map((r) => [r.id, r]));
  for (const [id, r] of afterRules) {
    const old = beforeRules.get(id);
    const name = id || "(no id)";
    if (!old) {
      out.push(`New rule ${name}`);
      continue;
    }
    const enabledBefore = old.enabled ?? true;
    const enabledAfter = r.enabled ?? true;
    if (enabledBefore !== enabledAfter) out.push(`Rule ${name} ${enabledAfter ? "on" : "off"}`);
    if (!same({ ...old, enabled: null }, { ...r, enabled: null })) out.push(`Rule ${name} edited`);
  }
  for (const id of beforeRules.keys()) if (!afterRules.has(id)) out.push(`Rule ${id} removed`);

  return out;
}

/** "3 unsaved changes" + the first few, "+2 more" for the rest. */
export function changesSummary(changes: readonly string[], shown = 3): { count: string; detail: string } {
  const n = changes.length;
  const count = n === 0 ? "Unsaved changes" : `${n} unsaved change${n === 1 ? "" : "s"}`;
  const head = changes.slice(0, shown).join(", ");
  return { count, detail: n > shown ? `${head}, +${n - shown} more` : head };
}
