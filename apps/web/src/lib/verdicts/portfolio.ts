import type { Verdict } from "@/components/insight/verdict-line";

/*
 * Portfolio answer-strip verdicts (visual spec D.3): deterministic one-sentence templates used
 * where the backend writes none. The tone comes from the comparison, never from the sign alone.
 */

export type VerdictResult = { tone: Verdict; text: string };

function signedPct(v: number, decimals = 1): string {
  const body = Math.abs(v * 100).toFixed(decimals);
  return `${v < 0 ? "−" : "+"}${body}%`;
}

function plainPct(v: number, decimals = 0): string {
  return `${(Math.abs(v) * 100).toFixed(decimals)}%`;
}

/** Return against the benchmark over the same period, as a gap in percentage points. */
export function benchmarkVerdict(portfolio: number | null, benchmark: number | null, benchmarkName = "the benchmark"): VerdictResult {
  if (portfolio === null) return { tone: "unknown", text: "Not enough history to measure a return yet." };
  if (benchmark === null) return { tone: "neutral", text: "No benchmark return for this period to compare with." };
  const gap = (portfolio - benchmark) * 100;
  if (Math.abs(gap) < 0.5) return { tone: "neutral", text: `In line with ${benchmarkName} (within 0.5 points).` };
  return gap > 0
    ? { tone: "good", text: `${gap.toFixed(1)} points ahead of ${benchmarkName}.` }
    : { tone: "bad", text: `${Math.abs(gap).toFixed(1)} points behind ${benchmarkName}.` };
}

/** Why the holdings' return and the return on the owner's own money can point different ways:
 * the first weights every day the same, the second weights each day by the money invested. */
export function timingVerdict(investments: number | null, money: number | null, windowName: string): VerdictResult | null {
  if (investments === null || money === null) return null;
  const gap = (investments - money) * 100;
  if (Math.abs(gap) < 2) return null;
  const inv = signedPct(investments);
  const mine = signedPct(money);
  if (investments > money) {
    return {
      tone: "neutral",
      text: `${windowName}: the holdings returned ${inv}, your money ${mine}. The holdings did best while little of your money was in them; most of it arrived later and has done worse.`,
    };
  }
  return {
    tone: "neutral",
    text: `${windowName}: your money returned ${mine}, more than the holdings' ${inv}, because more of it was invested in the periods that did well.`,
  };
}

/** Unrealised profit or loss as a share of what was paid. */
export function unrealisedVerdict(pctOnCost: number | null): VerdictResult {
  if (pctOnCost === null) return { tone: "unknown", text: "No cost basis to compare with." };
  if (pctOnCost > 0) return { tone: "good", text: `Open positions are worth ${plainPct(pctOnCost, 1)} more than you paid.` };
  if (pctOnCost < 0) return { tone: "bad", text: `Open positions are worth ${plainPct(pctOnCost, 1)} less than you paid.` };
  return { tone: "neutral", text: "Open positions are worth what you paid." };
}

/** Next-12-month income against the last 12 months. */
export function incomeChangeVerdict(change: number | null): VerdictResult {
  if (change === null) return { tone: "unknown", text: "No full year of received dividends to compare with yet." };
  if (Math.abs(change) < 0.005) return { tone: "neutral", text: "About the same as the last 12 months." };
  // Four times last year's income is new money at work, not dividend growth: say so instead
  // of printing a four-digit percentage.
  if (change > 3) return { tone: "neutral", text: "Mostly from positions held under a year, so the last 12 months are no fair comparison." };
  return change > 0
    ? { tone: "good", text: `${plainPct(change, 1)} more than the last 12 months.` }
    : { tone: "caution", text: `${plainPct(change, 1)} less than the last 12 months.` };
}

/** How many holdings a fair-value basis covers. */
export function coverageVerdict(covered: number, total: number): VerdictResult {
  const noun = total === 1 ? "holding" : "holdings";
  const text = `${covered} of ${total} ${noun} ${covered === 1 ? "has" : "have"} a fair value on this basis.`;
  if (total === 0) return { tone: "unknown", text: "No holdings to value." };
  const share = covered / total;
  return { tone: share >= 0.8 ? "good" : share >= 0.4 ? "caution" : "bad", text };
}

/** The covered holdings' fair value against their market value (positive = fair value above market). */
export function valueGapVerdict(gap: number | null): VerdictResult {
  if (gap === null) return { tone: "unknown", text: "No holding has a fair value on this basis yet." };
  if (gap > 0.2) return { tone: "good", text: `Covered holdings trade ${plainPct(gap)} below fair value.` };
  if (gap < -0.2) return { tone: "bad", text: `Covered holdings trade ${plainPct(gap)} above fair value.` };
  return { tone: "neutral", text: `Covered holdings trade about right (${signedPct(gap, 0)} to fair value).` };
}

/** The share of value priced above fair value. */
export function overvaluedWeightVerdict(weight: number | null): VerdictResult {
  if (weight === null) return { tone: "unknown", text: "No covered holdings to weigh." };
  if (weight > 0.5) return { tone: "bad", text: "More than half of the covered value is priced above fair value." };
  if (weight > 0.25) return { tone: "caution", text: "A quarter or more of the covered value is priced above fair value." };
  return { tone: "good", text: "Little of the covered value is priced above fair value." };
}

/**
 * How spread out the equity holdings are, from the effective number of holdings (1 / HHI): the
 * count of equal-sized positions that would carry the same concentration.
 */
export function concentrationVerdict(effectiveN: number | null, holdings: number): VerdictResult {
  if (effectiveN === null || holdings === 0) return { tone: "unknown", text: "No equity holdings to measure." };
  const n = effectiveN.toFixed(1);
  const base = `Your ${holdings} holdings behave like ${n} equal ones`;
  if (effectiveN < 5) return { tone: "bad", text: `${base}: a few positions decide the result.` };
  if (effectiveN < 10) return { tone: "caution", text: `${base}: fairly concentrated.` };
  return { tone: "good", text: `${base}: well spread.` };
}

/** Fair values whose inputs are too old to trust. */
export function staleVerdict(count: number): VerdictResult {
  return count === 0
    ? { tone: "good", text: "Every fair value is recent enough to trust." }
    : { tone: "caution", text: `${count} fair value${count === 1 ? " is" : "s are"} too old to trust until refreshed.` };
}
