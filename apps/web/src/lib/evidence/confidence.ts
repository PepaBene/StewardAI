import type { Confidence, EvidenceRelation, FactCheck, FactStatus, VerdictStatus } from "@/lib/api/client";

/*
 * The four confidence labels of the Ledger design system (section 4.2 of the redesign
 * proposal): a dedicated hue per state — blue / teal / amber / red, validated against the
 * OKLab colourblindness simulator — never gain or loss, and always with a text label and
 * an icon so meaning never rests on colour alone. Ordinal, in this fixed order.
 */
export const CONFIDENCE = {
  verified: {
    label: "Verified",
    tone: "verified",
    description: "Primary source, and every validator check passed.",
  },
  corroborated: {
    label: "Corroborated",
    tone: "corroborated",
    description: "Confirmed by independent sources.",
  },
  unconfirmed: {
    label: "Unconfirmed",
    tone: "unconfirmed",
    description: "Held for review or not yet backed by a verified figure.",
  },
  disputed: {
    label: "Disputed",
    tone: "disputed",
    description: "Contradicted by its own source, or marked wrong.",
  },
} as const satisfies Record<Confidence, { label: string; tone: string; description: string }>;

/*
 * How a piece of evidence relates to the claim it backs — a different question from
 * confidence (how sure we are the value is right). Reuses the diverging pair's cool/warm/
 * neutral logic rather than a new hue: supports is the "as expected" cool side, contradicts
 * is the "look here" warm side, mentions sits at the neutral centre.
 */
export const EVIDENCE_RELATION = {
  supports: {
    label: "Supports",
    tone: "diverging-under",
    description: "This source backs the claim: the fact is verified and every check passed.",
  },
  mentions: {
    label: "Mentions",
    tone: "diverging-neutral",
    description: "This source is on record but hasn't yet been corroborated enough to count as support.",
  },
  contradicts: {
    label: "Contradicts",
    tone: "diverging-over",
    description: "This source conflicts with the claim: it failed a check, or was rejected.",
  },
} as const satisfies Record<EvidenceRelation, { label: string; tone: string; description: string }>;

export function evidenceRelationLabel(relation: EvidenceRelation): string {
  return EVIDENCE_RELATION[relation].label;
}

/* Pass and breach are financial state and use gain and loss; unknown is styled apart. */
export const VERDICT_STATUS = {
  pass: { label: "Pass", tone: "gain" },
  breach: { label: "Breach", tone: "loss" },
  unknown: { label: "Unknown", tone: "unknown" },
} as const satisfies Record<VerdictStatus, { label: string; tone: string }>;

export function confidenceLabel(confidence: Confidence): string {
  return CONFIDENCE[confidence].label;
}

/**
 * Why a fact carries the confidence it does, beyond the chip's own generic description:
 * the validator's or owner's stated reason, plus any check it failed. Null when there's
 * nothing more specific than the chip already says (a plain Verified fact).
 */
export function confidenceReason(fact: {
  status_reason: string | null;
  checks: readonly FactCheck[];
}): string | null {
  const failed = fact.checks.filter((c) => !c.ok);
  const parts: string[] = [];
  if (fact.status_reason) parts.push(fact.status_reason);
  for (const check of failed) {
    if (check.detail && check.detail !== fact.status_reason) parts.push(check.detail);
  }
  return parts.length > 0 ? parts.join(" — ") : null;
}

/** A concrete next step for a fact that isn't (yet) settled, from its status alone. */
export function confidenceNextAction(status: FactStatus): string | null {
  switch (status) {
    case "unverified":
      return "Awaiting a second source or an owner decision — see the review queue.";
    case "quarantined":
      return "Held by cross-source reconciliation against the filing — see the review queue.";
    default:
      return null;
  }
}

/** A Verified fact or verdict stops earning full chrome once it's held that long unchanged
 *  — long enough to have survived at least one more sync cycle without being revisited. */
export const VERIFIED_QUIET_AFTER_DAYS = 90;

/** Whether a Verified confidence chip should render quietly (label only, no fill) because
 *  it's been unchanged for a while and doesn't need to compete with what actually needs
 *  attention. `since` is the timestamp of its last status change. */
export function isLongStandingVerified(
  confidence: Confidence,
  since: string | Date | null | undefined,
  now: Date = new Date(),
): boolean {
  if (confidence !== "verified" || !since) return false;
  const days = (now.getTime() - new Date(since).getTime()) / 86_400_000;
  return Number.isFinite(days) && days >= VERIFIED_QUIET_AFTER_DAYS;
}

/** Counts of each confidence state across a list — feeds ConfidenceBar. */
export function tallyConfidence(values: readonly Confidence[]): Record<Confidence, number> {
  const counts: Record<Confidence, number> = { verified: 0, corroborated: 0, unconfirmed: 0, disputed: 0 };
  for (const v of values) counts[v] += 1;
  return counts;
}

/** "5 verified · 2 corroborated" — omits states with nothing in them, for a ConfidenceBar's label. */
export function tallyConfidenceLabel(counts: Record<Confidence, number>): string {
  return (["verified", "corroborated", "unconfirmed", "disputed"] as const satisfies readonly Confidence[])
    .filter((k) => counts[k] > 0)
    .map((k) => `${counts[k]} ${CONFIDENCE[k].label.toLowerCase()}`)
    .join(" · ");
}

const ACRONYMS: Record<string, string> = {
  eps: "EPS",
  yoy: "YoY",
  qoq: "QoQ",
  fcf: "FCF",
  ebit: "EBIT",
  ebitda: "EBITDA",
  ttm: "TTM",
};

/** "revenue_growth_yoy" -> "Revenue growth YoY", "eps_diluted" -> "EPS diluted". */
export function humanizeMetric(metric: string): string {
  const words = metric.split("_").filter(Boolean).map((w) => ACRONYMS[w] ?? w);
  const text = words.join(" ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** A KPI's target, e.g. ">= 5 %" or "> 2 USD bn". */
export function formatTarget(operator: string, threshold: number, unit: string): string {
  const value = Number.isInteger(threshold) ? String(threshold) : String(Number(threshold.toFixed(6)));
  return `${operator} ${value} ${unit}`;
}

/** An observed KPI value in the KPI's unit, two decimals. */
export function formatObserved(value: number | null | undefined, unit: string): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const fixed = value.toFixed(2);
  return unit === "%" ? `${fixed}%` : `${fixed} ${unit}`;
}

const SCALE_ABBREV: Record<string, string> = { units: "", thousands: "k", millions: "m", billions: "bn" };

/** A fact as printed with its unit: "USD 94,036 m", "USD 1.57 / share", "46.5%". */
export function formatFactValue(fact: {
  value_as_written: string;
  scale: string;
  unit: string;
  currency: string | null;
}): string {
  const scale = SCALE_ABBREV[fact.scale] ?? fact.scale;
  const written = fact.value_as_written;
  // Already printed with its unit ("€18.1 billion", "14.1%"): shown as printed, not decorated twice.
  if (/[^\d\s.,+\-−'()]/.test(written.trim())) return written.trim();
  switch (fact.unit) {
    case "percent":
      return `${written}%`;
    case "per_share":
      return `${fact.currency ?? ""} ${written} / share`.trim();
    case "ratio":
      return `${written}x`;
    case "count":
      return `${written}${scale ? ` ${scale}` : ""}`;
    default:
      return `${fact.currency ?? ""} ${written}${scale ? ` ${scale}` : ""}`.trim();
  }
}
