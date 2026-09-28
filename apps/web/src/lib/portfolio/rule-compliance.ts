import type { DriftReport, StrategyConfig, StrategyRule as Rule } from "@/lib/api/client";

import { shortTicker } from "./ticker";

/*
 * Strategy rule compliance for the Overview, evaluated client-side against the drift report
 * the page already has — the same checks as the backend (steward/portfolio/breaches.py
 * `evaluate` and review/monthly.py `_not_checked`): a rule on a ticker or bucket the
 * portfolio does not hold is judged at weight 0; a rule with nothing to check is unknown.
 * Only rules that can be evaluated honestly from the drift report are shown.
 */

type Operator = Rule["operator"];

export type ComplianceStatus = "pass" | "breach" | "unknown";

export type ComplianceRow = {
  id: string;
  label: string;
  /** One line of evidence, e.g. "4.2% · band 2.0–8.0%". */
  detail: string;
  status: ComplianceStatus;
  /** Share of checks passed (0..1), for the ring; null when unknown. */
  fraction: number | null;
};

const EPS = 1e-9;

export function holds(value: number, operator: Operator, limit: number): boolean {
  switch (operator) {
    case "<":
      return value < limit;
    case "<=":
      return value <= limit + EPS;
    case ">":
      return value > limit;
    case ">=":
      return value >= limit - EPS;
    case "==":
      return Math.abs(value - limit) <= EPS;
  }
}

const OP_TEXT: Record<Operator, string> = { "<": "<", "<=": "≤", ">": ">", ">=": "≥", "==": "=" };

function pct(x: number | null | undefined, digits = 1): string {
  return x === null || x === undefined || !Number.isFinite(x) ? "—" : `${(x * 100).toFixed(digits)}%`;
}

const short = shortTicker;

/** Position band row: targeted positions inside their band. Null when nothing has a target. */
export function positionBandsRow(drift: DriftReport): ComplianceRow | null {
  const targeted = drift.positions.filter((p) => p.status !== "no_target");
  if (targeted.length === 0) return null;
  const inside = targeted.filter((p) => p.status === "in_band").length;
  return {
    id: "position-bands",
    label: "Position bands",
    detail: `${inside} of ${targeted.length} inside`,
    status: inside === targeted.length ? "pass" : "breach",
    fraction: inside / targeted.length,
  };
}

/** Cash band row. Null when the strategy sets no cash target. */
export function cashBandRow(drift: DriftReport): ComplianceRow | null {
  const c = drift.cash;
  if (c.status === "no_target" || c.lower === null || c.upper === null) return null;
  if (c.weight === null) {
    return { id: "cash-band", label: `Cash ${pct(c.lower, 0)}–${pct(c.upper, 0)}`, detail: "Cash weight unknown", status: "unknown", fraction: null };
  }
  const ok = c.status === "in_band";
  return {
    id: "cash-band",
    label: `Cash ${pct(c.lower, 0)}–${pct(c.upper, 0)}`,
    detail: `${pct(c.weight)} · ${ok ? "inside" : "outside"}`,
    status: ok ? "pass" : "breach",
    fraction: ok ? 1 : 0,
  };
}

type Check = { what: string; value: number };

function describe(rule: Rule): string {
  if (rule.description) return rule.description;
  const limit = rule.type === "position_count" ? String(rule.value) : pct(rule.value, 0);
  switch (rule.type) {
    case "position_weight":
      return `${rule.ticker ? short(rule.ticker) : "Single name"} ${OP_TEXT[rule.operator]} ${limit}`;
    case "category_weight":
      return `${rule.bucket ?? rule.dimension.replaceAll("_", " ")} ${OP_TEXT[rule.operator]} ${limit}`;
    case "cash_weight":
      return `Cash ${OP_TEXT[rule.operator]} ${limit}`;
    case "position_count":
      return `Positions ${OP_TEXT[rule.operator]} ${limit}`;
  }
}

/** One enabled strategy rule, evaluated against the drift report. */
export function ruleRow(rule: Rule, drift: DriftReport): ComplianceRow {
  const label = describe(rule);
  const limit = rule.value;
  const isCount = rule.type === "position_count";
  const fmt = (v: number) => (isCount ? String(Math.round(v)) : pct(v));
  const held = drift.positions.filter((p) => p.held);
  const checks: Check[] = [];
  let unknown: string | null = null;

  if (rule.type === "position_weight") {
    if (rule.ticker) {
      const p = held.find((x) => x.ticker === rule.ticker);
      checks.push({ what: short(rule.ticker), value: p ? p.weight : 0 });
    } else if (held.length === 0) {
      unknown = "No positions held to check";
    } else {
      held.forEach((p) => checks.push({ what: short(p.ticker), value: p.weight }));
    }
  } else if (rule.type === "category_weight") {
    const dim = drift.categories.find((d) => d.dimension === rule.dimension);
    const buckets = dim?.buckets ?? [];
    if (rule.bucket) {
      const b = buckets.find((x) => x.bucket === rule.bucket);
      checks.push({ what: rule.bucket, value: b ? b.weight : 0 });
    } else if (buckets.length === 0) {
      unknown = `No ${rule.dimension.replaceAll("_", " ")} data to check`;
    } else {
      buckets.forEach((b) => checks.push({ what: b.bucket, value: b.weight }));
    }
  } else if (rule.type === "cash_weight") {
    if (drift.cash.weight === null) unknown = "Cash weight unknown";
    else checks.push({ what: "Cash", value: drift.cash.weight });
  } else {
    checks.push({ what: "Positions", value: held.length });
  }

  if (unknown !== null) return { id: rule.id, label, detail: unknown, status: "unknown", fraction: null };

  const failed = checks.filter((c) => !holds(c.value, rule.operator, limit));
  const lower = rule.operator === ">" || rule.operator === ">=";
  // The binding figure: the worst check (largest for a cap, smallest for a floor).
  const worst = [...checks].sort((a, b) => (lower ? a.value - b.value : b.value - a.value))[0];
  const multi = checks.length > 1;
  const detail = failed.length
    ? `${failed.length > 1 ? `${failed.length} breach · ` : ""}${worst.what} ${fmt(worst.value)}`
    : multi
      ? `${lower ? "Min" : "Max"} ${fmt(worst.value)} ${worst.what}`
      : `${fmt(worst.value)} · pass`;
  return {
    id: rule.id,
    label,
    detail,
    status: failed.length ? "breach" : "pass",
    fraction: (checks.length - failed.length) / checks.length,
  };
}

/** Rows for the compliance panel: position bands, cash band, then every enabled rule. */
export function complianceRows(drift: DriftReport, strategy: StrategyConfig | null): ComplianceRow[] {
  const rows: ComplianceRow[] = [];
  const bands = positionBandsRow(drift);
  if (bands) rows.push(bands);
  const cash = cashBandRow(drift);
  if (cash) rows.push(cash);
  for (const rule of strategy?.rules ?? []) {
    if (rule.enabled === false) continue;
    rows.push(ruleRow(rule, drift));
  }
  return rows;
}

/** Thesis KPI row from the thesis board: theses judged intact out of those judged. */
export function thesisRow(rows: readonly { state: "intact" | "watch" | "at_risk" | "unjudged" }[]): ComplianceRow | null {
  const judged = rows.filter((r) => r.state !== "unjudged");
  const unjudged = rows.length - judged.length;
  if (rows.length === 0) return null;
  if (judged.length === 0) {
    return { id: "thesis-kpis", label: "Thesis KPIs", detail: `${unjudged} not judged yet`, status: "unknown", fraction: null };
  }
  const intact = judged.filter((r) => r.state === "intact").length;
  return {
    id: "thesis-kpis",
    label: "Thesis KPIs",
    detail: `${intact} of ${judged.length} intact${unjudged ? ` · ${unjudged} unjudged` : ""}`,
    status: intact === judged.length ? "pass" : "breach",
    fraction: intact / judged.length,
  };
}
