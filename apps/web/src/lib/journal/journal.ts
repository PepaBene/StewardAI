import { normaliseSeparators } from "@/lib/number-input";
import type { components } from "@/lib/api/schema";

type S = components["schemas"];
export type JournalEntry = S["JournalEntry"];
export type JournalEntryIn = S["JournalEntryIn"];
export type Scorecard = S["Scorecard"];
export type ScoreSection = S["Section"];
export type ScoreRow = S["Row"];
export type Cockpit = S["Cockpit"];
export type QualityIssue = S["QualityIssue"];
export type Decision = JournalEntryIn["decision"];
export type MainReason = NonNullable<JournalEntryIn["main_reason"]>;

export const DECISIONS: readonly { value: Decision; label: string; help: string }[] = [
  { value: "buy", label: "Buy", help: "Open a position." },
  { value: "add", label: "Add", help: "Increase one you hold." },
  { value: "hold", label: "Hold", help: "Looked again and kept it as it is." },
  { value: "trim", label: "Trim", help: "Take some off, for example at a profit-taking level." },
  { value: "sell", label: "Sell", help: "Close the position." },
  { value: "pass", label: "Pass", help: "Researched and decided not to buy." },
  { value: "watch", label: "Watch", help: "Not now; waiting for a price or an event." },
  { value: "review", label: "Review", help: "Revisit an earlier entry; the original stays as written." },
];

export const REASONS: readonly { value: MainReason; label: string }[] = [
  { value: "quality", label: "Quality" },
  { value: "value", label: "Value" },
  { value: "catalyst", label: "Catalyst" },
  { value: "momentum", label: "Momentum" },
  { value: "other", label: "Other" },
];

export function decisionLabel(value: string): string {
  return DECISIONS.find((d) => d.value === value)?.label ?? value;
}

export function reasonLabel(value: string | null | undefined): string {
  if (!value) return "not recorded";
  return REASONS.find((r) => r.value === value)?.label ?? value;
}

/** One item per non-blank line. */
export function lines(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

export type EntryForm = {
  scope: "security" | "portfolio";
  ticker: string;
  decision: Decision;
  parent_id: string;
  hypothesis: string;
  valuation_assumptions: string;
  catalysts: string;
  disconfirming: string;
  review_date: string;
  conviction: string;
  main_reason: string;
  price: string;
};

export const EMPTY_FORM: EntryForm = {
  scope: "security",
  ticker: "",
  decision: "buy",
  parent_id: "",
  hypothesis: "",
  valuation_assumptions: "",
  catalysts: "",
  disconfirming: "",
  review_date: "",
  conviction: "",
  main_reason: "",
  price: "",
};

export type BuiltEntry = { ok: true; body: JournalEntryIn } | { ok: false; error: string };

/** The request for the journal API from the form, or what is missing, in the owner's words. */
export function buildEntry(form: EntryForm): BuiltEntry {
  const ticker = form.ticker.trim();
  if (form.scope === "security" && ticker === "") return { ok: false, error: "Name the security this decision is about." };
  if (form.hypothesis.trim() === "") {
    return { ok: false, error: "Write the reasoning first: what you believe and why." };
  }
  let price: number | null = null;
  if (form.price.trim() !== "") {
    price = Number(normaliseSeparators(form.price));
    if (!Number.isFinite(price) || price <= 0) return { ok: false, error: "The price must be a positive number, or blank." };
  }
  const parent = form.parent_id.trim() === "" ? null : Number(form.parent_id);
  if (parent !== null && !Number.isInteger(parent)) return { ok: false, error: "The entry you review must be a number." };
  return {
    ok: true,
    body: {
      scope: form.scope,
      ticker: form.scope === "security" ? ticker : null,
      decision: form.decision,
      parent_id: parent,
      hypothesis: form.hypothesis.trim(),
      valuation_assumptions: form.valuation_assumptions.trim() || null,
      catalysts: lines(form.catalysts),
      disconfirming: lines(form.disconfirming),
      review_date: form.review_date || null,
      conviction: form.conviction === "" ? null : Number(form.conviction),
      main_reason: (form.main_reason || null) as MainReason | null,
      price_at_decision: price,
    },
  };
}

/** Days from `today` to a review date: negative when it has passed; null without a date. */
export function daysUntil(date: string | null | undefined, today: Date): number | null {
  if (!date) return null;
  const target = Date.parse(`${date}T00:00:00Z`);
  const now = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  return Number.isNaN(target) ? null : Math.round((target - now) / 86_400_000);
}

export function reviewLabel(days: number | null): string | null {
  if (days === null) return null;
  if (days < 0) return `review ${-days} day${days === -1 ? "" : "s"} overdue`;
  if (days === 0) return "review due today";
  return `review in ${days} day${days === 1 ? "" : "s"}`;
}

/**
 * The evidence meter of a scorecard section: `filled` of `needed` squares. A section below the
 * minimum shows its few squares and is marked thin, so a small sample never looks like a result.
 */
export function evidenceMeter(n: number, needed: number): { filled: number; needed: number; thin: boolean } {
  return { filled: Math.min(Math.max(n, 0), needed), needed, thin: n < needed };
}

/** Width (0-100) of a bar for a share of the portfolio, scaled to the largest in the list. */
export function weightBar(weight: number | null | undefined, max: number): number {
  if (weight === null || weight === undefined || !(max > 0)) return 0;
  return Math.min(100, Math.max(0, (weight / max) * 100));
}

type Loose = Record<string, unknown>;
const asObject = (v: unknown): Loose | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Loose) : null);

/** The evidence stored with an entry, as plain sentences; what was not recorded is said, not skipped. */
export function evidenceLines(snapshot: Record<string, unknown>, portfolioScope = false): string[] {
  const out: string[] = [];
  if (portfolioScope) {
    const value = snapshot.portfolio_value_base;
    out.push(typeof value === "number" ? `Portfolio value then: ${Math.round(value).toLocaleString("en-GB")} in base currency.` : "Portfolio value was not recorded.");
  } else {
    const holding = asObject(snapshot.holding);
    const weight = holding && typeof holding.weight === "number" ? holding.weight : null;
    out.push(holding ? (weight === null ? "Held, weight not recorded." : `Held at ${(weight * 100).toFixed(1)}% of the portfolio.`) : "Not held at the time.");
    const market = asObject(snapshot.market);
    if (market && typeof market.last_close === "number") {
      const vol = typeof market.volatility_1y === "number" ? `, one-year volatility ${(market.volatility_1y * 100).toFixed(0)}%` : "";
      out.push(`Last close ${market.last_close} on ${String(market.last_close_date ?? "an unknown date")}${vol}.`);
    }
    const gaps = snapshot.thesis_gaps;
    if (snapshot.thesis === null || snapshot.thesis === undefined) out.push("No thesis was written for it.");
    else if (Array.isArray(gaps) && gaps.length > 0) out.push(`The thesis still lacked: ${gaps.join("; ")}.`);
    else out.push("The thesis met every requirement.");
  }
  const versions = asObject(snapshot.config_versions);
  if (versions) {
    const named = Object.entries(versions)
      .filter(([, id]) => id !== null && id !== undefined)
      .map(([name, id]) => `${name.replace("_", " ")} #${String(id)}`);
    if (named.length > 0) out.push(`Config in force: ${named.join(", ")}.`);
  }
  const methods = asObject(snapshot.method_versions);
  if (methods) out.push(`${Object.keys(methods).length} calculation methods, versions recorded.`);
  return out;
}

export const SNOOZE_CHOICES: readonly { days: number; label: string }[] = [
  { days: 7, label: "1 week" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
];

/** The date an issue set aside today for `days` days comes back, as YYYY-MM-DD in local time. */
export function snoozeUntil(days: number, today: Date = new Date()): string {
  const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() + days);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export const ISSUE_LABEL:Record<QualityIssue["kind"], string> = {
  stale_price: "Price",
  identity_unresolved: "Identity",
  fund_weights: "Fund weights",
  claim_review: "Claims",
  source_conflict: "Sources",
  provider_access: "Provider access",
  provider_quota: "Provider quota",
  thesis_gap: "Thesis",
};
