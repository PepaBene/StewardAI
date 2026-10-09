import type { components } from "@/lib/api/schema";

type S = components["schemas"];
export type FactorExposures = S["FactorExposures"];
export type FitOut = S["FitOut"];
export type FactorBeta = S["FactorBeta"];
export type PositionFactors = S["PositionFactors"];
export type Tilt = S["Tilt"];

/** The style factors first, then the currencies, as the report returns them. */
export function isCurrency(factor: string): boolean {
  return factor === "USD" || factor === "EUR";
}

/** A loading counts as measured when its t-statistic is at least 2 either way. */
export function isSignificant(t: number | null | undefined): boolean {
  return typeof t === "number" && Number.isFinite(t) && Math.abs(t) >= 2;
}

/** The largest absolute value, never zero, so widths stay finite. */
export function betaScale(values: readonly (number | null | undefined)[]): number {
  const m = Math.max(0, ...values.filter((v): v is number => typeof v === "number" && Number.isFinite(v)).map(Math.abs));
  return m > 0 ? m : 1;
}

/** A bar growing from the centre line: where it starts and how wide it is, both as percentages of the track. */
export function divergingBar(beta: number | null | undefined, scale: number): { left: number; width: number } {
  if (typeof beta !== "number" || !Number.isFinite(beta) || !(scale > 0)) return { left: 50, width: 0 };
  const half = Math.min(50, (Math.abs(beta) / scale) * 50);
  return beta >= 0 ? { left: 50, width: half } : { left: 50 - half, width: half };
}

/** Loading of one factor in a fit, if the fit has it. */
export function betaOf(fit: FitOut | null | undefined, factor: string): FactorBeta | undefined {
  return fit?.betas.find((b) => b.factor === factor);
}
