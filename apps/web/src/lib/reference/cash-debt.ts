/**
 * Cash conversion and debt maturities from the stored as-filed facts
 * (`/holdings/{ticker}/financial-history`, untyped in OpenAPI). Every figure is a filed value on
 * one period, basis and currency; a step that is not filed is left out and said so, and the one
 * step that is worked out (the balancing item) is labelled as such.
 */
import type { WaterfallStep } from "@/components/charts";

import type { Reconciliation } from "./financial-history";
import { toNumber } from "./peers";

type Fact = {
  item: string;
  value: number;
  periodEnd: string;
  periodStart: string | null;
  basis: string;
  currency: string | null;
  known: string;
  reconciliation: Reconciliation;
};

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const DAY = 86_400_000;
const ORDER: Reconciliation[] = ["failed", "unchecked", "reconciled"];

function annualOrInstant(start: string | null, end: string): boolean {
  if (!start) return true;
  const days = (Date.parse(end) - Date.parse(start)) / DAY;
  return days >= 330 && days <= 380;
}

function consolidatedFacts(payload: unknown, items: ReadonlySet<string>): Fact[] {
  if (!Array.isArray(payload)) return [];
  const best = new Map<string, Fact>();
  for (const raw of payload) {
    if (!isRecord(raw)) continue;
    const item = str(raw.line_item);
    const end = str(raw.period_end);
    const value = toNumber(raw.value);
    if (!item || !items.has(item) || !end || value === null) continue;
    if (isRecord(raw.dimensions) && Object.keys(raw.dimensions).length > 0) continue;
    const start = str(raw.period_start);
    if (!annualOrInstant(start, end)) continue;
    const basis = str(raw.basis) ?? "";
    const currency = str(raw.currency) ?? str(raw.unit);
    const fact: Fact = {
      item,
      value,
      periodEnd: end,
      periodStart: start,
      basis,
      currency,
      known: str(raw.known_at) ?? "",
      reconciliation: raw.reconciliation === "reconciled" || raw.reconciliation === "failed" ? raw.reconciliation : "unchecked",
    };
    const key = `${item}|${end}|${basis}|${currency}`;
    const prior = best.get(key);
    if (!prior || fact.known > prior.known) best.set(key, fact);
  }
  return [...best.values()];
}

function worst(facts: readonly Fact[]): Reconciliation {
  return facts.reduce<Reconciliation>((w, f) => (ORDER.indexOf(f.reconciliation) < ORDER.indexOf(w) ? f.reconciliation : w), "reconciled");
}

// --- Cash conversion --------------------------------------------------------------------

export type CashConversion = {
  periodEnd: string;
  basis: string;
  currency: string | null;
  steps: WaterfallStep[];
  netIncome: number;
  freeCashFlow: number | null;
  /** Free cash flow over net income, null unless net income is positive and free cash flow exists. */
  conversion: number | null;
  /** Lines that are not filed for this period, in words. */
  missing: string[];
  reconciliation: Reconciliation;
};

const CASH_ITEMS = new Set(["net_income", "depreciation_amortization", "share_based_compensation", "cash_from_operations", "capex"]);

export function parseCashConversion(payload: unknown): CashConversion | null {
  const facts = consolidatedFacts(payload, CASH_ITEMS);
  // Newest period (same basis and currency) that has both ends of the bridge.
  const groups = new Map<string, Fact[]>();
  for (const f of facts) {
    const key = `${f.periodEnd}|${f.basis}|${f.currency}`;
    groups.set(key, [...(groups.get(key) ?? []), f]);
  }
  const candidates = [...groups.entries()]
    .filter(([, g]) => g.some((f) => f.item === "net_income") && g.some((f) => f.item === "cash_from_operations"))
    .sort((a, b) => b[0].localeCompare(a[0]));
  if (candidates.length === 0) return null;
  const [, group] = candidates[0];
  const get = (item: string) => group.find((f) => f.item === item)?.value ?? null;
  const ni = get("net_income")!;
  const cfo = get("cash_from_operations")!;
  const da = get("depreciation_amortization");
  const sbc = get("share_based_compensation");
  const capexRaw = get("capex");
  const missing: string[] = [];
  const steps: WaterfallStep[] = [{ id: "ni", label: "Net income", value: ni, kind: "total" }];
  if (da === null) missing.push("depreciation and amortisation");
  else steps.push({ id: "da", label: "Depreciation", value: da, kind: "delta" });
  if (sbc === null) missing.push("share-based compensation");
  else steps.push({ id: "sbc", label: "Share pay", value: sbc, kind: "delta" });
  const other = cfo - ni - (da ?? 0) - (sbc ?? 0);
  steps.push({ id: "other", label: "Other and working capital", value: other, kind: "delta", derived: true });
  steps.push({ id: "cfo", label: "Operating cash", value: cfo, kind: "total" });
  let fcf: number | null = null;
  if (capexRaw === null) missing.push("capital expenditure");
  else {
    steps.push({ id: "capex", label: "Capex", value: -Math.abs(capexRaw), kind: "delta" });
    fcf = cfo - Math.abs(capexRaw);
    steps.push({ id: "fcf", label: "Free cash flow", value: fcf, kind: "total" });
  }
  return {
    periodEnd: group[0].periodEnd,
    basis: group[0].basis,
    currency: group[0].currency,
    steps,
    netIncome: ni,
    freeCashFlow: fcf,
    conversion: fcf !== null && ni > 0 ? fcf / ni : null,
    missing,
    reconciliation: worst(group),
  };
}

// --- Debt maturities --------------------------------------------------------------------

export type MaturityBucket = { id: string; label: string; value: number | null };

export type DebtMaturities = {
  periodEnd: string;
  basis: string;
  currency: string | null;
  buckets: MaturityBucket[];
  /** Sum of the buckets that are filed; excludes the ones that are not. */
  total: number;
  /** Buckets the issuer did not tag for this period. */
  missing: string[];
  reconciliation: Reconciliation;
};

const BUCKETS: readonly { item: string; label: string }[] = [
  { item: "debt_maturity_y1", label: "Year 1" },
  { item: "debt_maturity_y2", label: "Year 2" },
  { item: "debt_maturity_y3", label: "Year 3" },
  { item: "debt_maturity_y4", label: "Year 4" },
  { item: "debt_maturity_y5", label: "Year 5" },
  { item: "debt_maturity_after_y5", label: "After 5" },
];
const MATURITY_ITEMS = new Set([...BUCKETS.map((b) => b.item), "debt_maturity_remainder"]);

export function parseDebtMaturities(payload: unknown): DebtMaturities | null {
  const facts = consolidatedFacts(payload, MATURITY_ITEMS);
  if (facts.length === 0) return null;
  const latest = [...facts].sort((a, b) => b.periodEnd.localeCompare(a.periodEnd) || a.basis.localeCompare(b.basis))[0];
  const group = facts.filter((f) => f.periodEnd === latest.periodEnd && f.basis === latest.basis && f.currency === latest.currency);
  const val = (item: string) => group.find((f) => f.item === item)?.value ?? null;
  const buckets: MaturityBucket[] = BUCKETS.map((b) => ({
    id: b.item,
    label: b.label,
    // An issuer that reports only a single "remainder" tail has it shown under "After 5".
    value: b.item === "debt_maturity_after_y5" ? (val(b.item) ?? val("debt_maturity_remainder")) : val(b.item),
  }));
  return {
    periodEnd: latest.periodEnd,
    basis: latest.basis,
    currency: latest.currency,
    buckets,
    total: buckets.reduce((s, b) => s + (b.value ?? 0), 0),
    missing: buckets.filter((b) => b.value === null).map((b) => b.label),
    reconciliation: worst(group),
  };
}
