import { MARK_AXES, type MarkAxis, type MarkPetal } from "@/lib/insight/insight";

/*
 * The five-axis fund mark (redesign plan R4.7). ScoreMark draws five slots; for a company they are
 * the check axes, for a fund they are the five questions below. An axis whose input is unknown
 * has no fraction, so ScoreMark draws a gap, never a zero. Slots are mapped in MARK_AXES order.
 */

export type FundAxisKey = "cost" | "spread" | "record" | "freshness" | "completeness";

export type FundAxis = { key: FundAxisKey; label: string; question: string; slot: MarkAxis };

export const FUND_AXES: readonly FundAxis[] = [
  { key: "cost", label: "Cost", question: "How low are the yearly charges?", slot: MARK_AXES[0] },
  { key: "spread", label: "Spread", question: "How widely is the money spread over holdings?", slot: MARK_AXES[1] },
  { key: "record", label: "Track record", question: "How many years of history are there?", slot: MARK_AXES[2] },
  { key: "freshness", label: "Fresh data", question: "How recent is the holdings list?", slot: MARK_AXES[3] },
  { key: "completeness", label: "Completeness", question: "How much of the profile is filled in?", slot: MARK_AXES[4] },
];

/** The fund question each mark slot stands for, for ScoreMark's labels and tooltips. */
export const FUND_AXIS_NAMES: Partial<Record<MarkAxis, string>> = Object.fromEntries(FUND_AXES.map((a) => [a.slot, a.label]));

export type FundMarkInput = {
  /** Ongoing charge in percent per year (0.2 means 0.2%). */
  ongoingChargePct: number | null;
  holdingsCount: number | null;
  /** Share of the fund in its ten largest holdings, as a fraction. */
  top10Weight: number | null;
  yearsOfHistory: number | null;
  /** Days since the holdings list was published. */
  holdingsLagDays: number | null;
  fieldsHave: number | null;
  fieldsOf: number | null;
};

const finite = (v: number | null | undefined): v is number => typeof v === "number" && Number.isFinite(v);
const clamp01 = (v: number) => Math.max(0, Math.min(1, v));
/** 1 at `best`, 0 at `worst`, linear between (works in either direction). */
const scale = (v: number, best: number, worst: number) => clamp01((v - worst) / (best - worst));

/** Each axis as a fraction 0..1, or null when its input is unknown. */
export function fundMarkFractions(input: FundMarkInput): Record<FundAxisKey, number | null> {
  const spread = finite(input.top10Weight)
    ? scale(input.top10Weight, 0.15, 0.8)
    : finite(input.holdingsCount)
      ? clamp01(input.holdingsCount / 100)
      : null;
  return {
    cost: finite(input.ongoingChargePct) ? scale(input.ongoingChargePct, 0.1, 2) : null,
    spread,
    record: finite(input.yearsOfHistory) ? clamp01(input.yearsOfHistory / 10) : null,
    freshness: finite(input.holdingsLagDays) ? scale(input.holdingsLagDays, 0, 90) : null,
    completeness: finite(input.fieldsHave) && finite(input.fieldsOf) && input.fieldsOf > 0 ? clamp01(input.fieldsHave / input.fieldsOf) : null,
  };
}

/** Petals in ScoreMark's slot order; unknown axes keep `fraction: null` so they render as gaps. */
export function fundMarkPetals(input: FundMarkInput): MarkPetal[] {
  const f = fundMarkFractions(input);
  return FUND_AXES.map((a) => ({ axis: a.slot, fraction: f[a.key], notApplicable: false }));
}

const MISSING_INPUT: Record<FundAxisKey, string> = {
  cost: "the ongoing charge",
  spread: "the holdings list",
  record: "the price history",
  freshness: "the holdings date",
  completeness: "the profile fields",
};

/** Names of what is missing per slot, for ScoreMark's notch tooltip. */
export function fundMarkMissing(input: FundMarkInput): Partial<Record<MarkAxis, string>> {
  const f = fundMarkFractions(input);
  const out: Partial<Record<MarkAxis, string>> = {};
  for (const a of FUND_AXES) if (f[a.key] === null) out[a.slot] = MISSING_INPUT[a.key];
  return out;
}

/** The first numeric ongoing-charge figure in a profile's `charges` object, in percent. */
export function ongoingChargeFrom(charges: Record<string, unknown> | null | undefined): number | null {
  if (!charges) return null;
  for (const key of ["ongoing_charge", "ongoing_charges", "ocf", "ter", "total_expense_ratio"]) {
    const raw = charges[key];
    const n = typeof raw === "number" ? raw : typeof raw === "string" ? Number(raw) : NaN;
    if (Number.isFinite(n)) return n;
  }
  return null;
}
