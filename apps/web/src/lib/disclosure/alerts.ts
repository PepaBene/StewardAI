import type { components } from "@/lib/api/schema";

/*
 * Disclosure alerts (plan P4.6 / P5): an alert says what was filed and when it became known. It never
 * says why anyone acted, and acknowledging one only hides it from the open list.
 */

type S = components["schemas"];
export type DisclosureAlert = S["DisclosureAlertOut"];
export type AlertKind = DisclosureAlert["kind"];

export const ALERT_KIND_LABEL: Record<AlertKind, string> = {
  insider_trade: "Insider trade",
  major_holder: "Large holder filing",
  relationship_change: "Customer or supplier change",
  deal_state: "Deal moved to a new stage",
  controlling_stake: "Controlling stake reported",
  insider_buy_cluster: "Cluster of insider purchases",
  estimate_revision: "Consensus estimate revised",
  debt_maturity_change: "Debt maturity profile changed",
};

/** Where to read more about an alert, by kind. */
export const ALERT_HREF: Record<AlertKind, string> = {
  insider_trade: "/disclosures/insiders",
  major_holder: "/disclosures/ownership",
  relationship_change: "/disclosures/network",
  deal_state: "/disclosures/deals",
  controlling_stake: "/disclosures/ownership",
  insider_buy_cluster: "/disclosures/insiders",
  estimate_revision: "/disclosures/estimates",
  debt_maturity_change: "/disclosures",
};

export type AlertWhy = S["AlertWhyOut"];

/** The rule behind an alert as readable lines: rule, threshold, then each input it saw. An alert
 * stored before rules were recorded has none, and says so rather than inventing one. */
export function whyLines(why: AlertWhy | null | undefined): string[] {
  if (why == null) return ["No rule was recorded for this alert."];
  const inputs = Object.entries(why.inputs ?? {}).map(([k, v]) => `${k.replaceAll("_", " ")}: ${v == null ? "not stated" : String(v)}`);
  return [`Rule: ${why.rule.replaceAll("_", " ")}`, `Threshold: ${why.threshold}`, ...inputs];
}

export type AlertScan = S["AlertScanOut"];

/** New alerts raised by one scan: the sum of every alert kind. Review tasks and failed checks are
 * not alerts and are reported separately. */
export const scanTotal = (d: AlertScan): number =>
  d.insider_trade + d.major_holder + d.relationship_change + d.deal_state + d.controlling_stake + d.insider_buy_cluster + d.estimate_revision + d.debt_maturity_change;

/** Every known kind with its count, zeros included, so the total never hides a kind. */
export function countByKind(alerts: readonly DisclosureAlert[]): { kind: AlertKind; count: number }[] {
  return (Object.keys(ALERT_KIND_LABEL) as AlertKind[]).map((kind) => ({ kind, count: alerts.filter((a) => a.kind === kind).length }));
}

export function splitByState(alerts: readonly DisclosureAlert[]): { open: DisclosureAlert[]; done: DisclosureAlert[] } {
  const open = alerts.filter((a) => a.acknowledged_at == null);
  const done = alerts.filter((a) => a.acknowledged_at != null);
  return { open, done };
}

/** Newest known first: what the owner learned most recently is read first. */
export const byKnown = (alerts: readonly DisclosureAlert[]): DisclosureAlert[] => [...alerts].sort((a, b) => Date.parse(b.known_at) - Date.parse(a.known_at));

export type DigestGroup = S["AlertDigestGroupOut"];

/** Readable kind names for the digest; an unknown kind is shown as filed rather than dropped. */
export const digestKindLabel = (kind: string): string => ALERT_KIND_LABEL[kind as AlertKind] ?? kind.replaceAll("_", " ");

/** Most alerts first, then the most recently known, then ticker, so the order is stable. */
export const digestOrder = (groups: readonly DigestGroup[]): DigestGroup[] =>
  [...groups].sort((a, b) => b.count - a.count || Date.parse(b.latest_known_at) - Date.parse(a.latest_known_at) || a.ticker.localeCompare(b.ticker));

/** Alerts for the chosen ticker; null means no filter. */
export const forTicker = <T extends { ticker: string }>(alerts: readonly T[], ticker: string | null): T[] => (ticker == null ? [...alerts] : alerts.filter((a) => a.ticker === ticker));

/** Adjudication labels a person may give, per dataset. */
export const RELATIONSHIP_LABELS = [
  { value: "true_relationship", label: "A current relationship" },
  { value: "historical_only", label: "Describes the past only" },
  { value: "hypothetical", label: "A risk statement, not a fact" },
  { value: "negated", label: "Says there is none" },
  { value: "unrelated_third_party", label: "About other companies" },
  { value: "ambiguous", label: "Cannot tell" },
] as const;

export const OWNERSHIP_MATCH_LABELS = [
  { value: "same_company", label: "The same company" },
  { value: "different_company", label: "A different company" },
  { value: "cannot_tell", label: "Cannot tell" },
] as const;

/** The state a deal passage supports, as `adjudication.DEAL_STATE_LABELS` in the backend. */
export const DEAL_STATE_LABELS = [
  { value: "rumored", label: "Rumoured" },
  { value: "announced", label: "Announced" },
  { value: "definitive", label: "Definitive agreement" },
  { value: "approved", label: "Approved" },
  { value: "closed", label: "Closed" },
  { value: "terminated", label: "Terminated" },
  { value: "contested", label: "Contested" },
  { value: "not_a_deal", label: "Not a deal" },
] as const;

export const INSIDER_CLASS_LABELS = [
  { value: "open_market_buy", label: "Open-market purchase" },
  { value: "open_market_sell", label: "Open-market sale" },
  { value: "option_exercise", label: "Option exercise" },
  { value: "grant_award", label: "Award or grant" },
  { value: "gift", label: "Gift" },
  { value: "tax_withholding", label: "Shares withheld for tax" },
  { value: "conversion", label: "Conversion" },
  { value: "other", label: "Other" },
  { value: "holding_snapshot", label: "Holding report" },
] as const;
