import type { Confidence, VerdictStatus } from "@/lib/api/client";

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

/* Pass and breach are financial state and use gain and loss; unknown is styled apart. */
export const VERDICT_STATUS = {
  pass: { label: "Pass", tone: "gain" },
  breach: { label: "Breach", tone: "loss" },
  unknown: { label: "Unknown", tone: "unknown" },
} as const satisfies Record<VerdictStatus, { label: string; tone: string }>;

export function confidenceLabel(confidence: Confidence): string {
  return CONFIDENCE[confidence].label;
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
