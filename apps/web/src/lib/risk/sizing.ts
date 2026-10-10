import type { components } from "@/lib/api/schema";

type S = components["schemas"];

export type TradeRisk = S["TradeRisk"];
export type StartSize = S["StartSize"];
export type AllocationPlan = S["AllocationPlan"];
export type OverCap = S["OverCap"];
export type ConcentrationReport = S["ConcentrationReport"];
export type IdeaFitReport = S["IdeaFitReport"];

const CHANGE_TEXT: Record<string, string> = {
  new: "New breach",
  deeper: "Deeper breach",
  still_open: "Still open",
  eased: "Eased",
  cleared: "Cleared",
};

/** The words for how a limit moved between the book as it is and the book after the trade. */
export function changeText(change: string): string {
  return CHANGE_TEXT[change] ?? change;
}

/** Whether a limit change is one the owner should look at first. */
export function isWorse(change: string): boolean {
  return change === "new" || change === "deeper";
}

const STAGE_TEXT: Record<string, string> = {
  core_below_target: "Core below target",
  under_represented: "Under-represented",
  above_hurdle: "Above the return hurdle",
  core_fallback: "Core, nothing else ready",
};

export function stageText(stage: string): string {
  return STAGE_TEXT[stage] ?? stage;
}

/** A move between two fractions in percentage points, e.g. 0.158 to 0.141 is "−1.7 pts". */
export function pointsChange(before: number | null | undefined, after: number | null | undefined): string {
  if (before === null || before === undefined || after === null || after === undefined) return "—";
  const points = (after - before) * 100;
  if (Math.abs(points) < 0.05) return "no change";
  return `${points > 0 ? "+" : "−"}${Math.abs(points).toFixed(1)} pts`;
}

/** Whole shares and the money they cost, e.g. "12 shares, 14 400 CZK". */
export function sharesText(shares: number | null | undefined): string {
  if (shares === null || shares === undefined) return "—";
  return `${shares} ${shares === 1 ? "share" : "shares"}`;
}
