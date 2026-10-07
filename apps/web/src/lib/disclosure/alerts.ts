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
};

/** Where to read more about an alert, by kind. */
export const ALERT_HREF: Record<AlertKind, string> = {
  insider_trade: "/disclosures/insiders",
  major_holder: "/disclosures/ownership",
  relationship_change: "/disclosures/network",
};

export function splitByState(alerts: readonly DisclosureAlert[]): { open: DisclosureAlert[]; done: DisclosureAlert[] } {
  const open = alerts.filter((a) => a.acknowledged_at == null);
  const done = alerts.filter((a) => a.acknowledged_at != null);
  return { open, done };
}

/** Newest known first: what the owner learned most recently is read first. */
export const byKnown = (alerts: readonly DisclosureAlert[]): DisclosureAlert[] => [...alerts].sort((a, b) => Date.parse(b.known_at) - Date.parse(a.known_at));

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
