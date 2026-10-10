import type { components } from "@/lib/api/schema";

type S = components["schemas"];

export type ModelValidation = S["ModelValidation"];
export type Evaluation = S["Evaluation"];
export type PeriodOut = S["PeriodOut"];
export type ProcessReport = S["ProcessReport"];
export type ClosedThesis = S["ClosedThesis"];
export type PositionRiskPack = S["PositionRiskPack"];

const CONFIDENCE_TEXT: Record<string, string> = {
  ok: "Holds up",
  reduced: "Trust it less",
  untested: "Not enough history to test",
};

/** The words for how far the risk model can be trusted after its own back-test. */
export function confidenceText(confidence: string): string {
  return CONFIDENCE_TEXT[confidence] ?? confidence;
}

/** A test's p-value as plain words: small means the forecast and reality disagree. */
export function testOutcome(passed: boolean): string {
  return passed ? "Passed" : "Failed";
}

/** The p-value to two decimals, "<0.01" when it rounds to nothing. */
export function pValueText(p: number | null | undefined): string {
  if (p === null || p === undefined || !Number.isFinite(p)) return "—";
  return p < 0.005 ? "<0.01" : p.toFixed(2);
}

/** A reconciliation error in basis points; "0.0 bp" means the effects add up to the excess return. */
export function basisPointsText(bp: number | null | undefined): string {
  if (bp === null || bp === undefined || !Number.isFinite(bp)) return "—";
  return `${Math.abs(bp) < 0.05 ? "0.0" : bp.toFixed(1)} bp`;
}

/** A signed share in percent with one decimal, e.g. 0.0123 → "+1.2 %" (uses a true minus). */
export function signedPercent(fraction: number | null | undefined): string {
  if (fraction === null || fraction === undefined || !Number.isFinite(fraction)) return "—";
  const v = fraction * 100;
  if (Math.abs(v) < 0.05) return "0.0 %";
  return `${v > 0 ? "+" : "−"}${Math.abs(v).toFixed(1)} %`;
}
