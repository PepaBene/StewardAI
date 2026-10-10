import type { components } from "@/lib/api/schema";

type S = components["schemas"];

export type RankingResponse = S["RankingResponse"];
export type RankingRow = S["RankingRow"];
export type SwitchResponse = S["SwitchResponse"];
export type SwitchLine = S["SwitchLine"];

/** The suggested action in the owner's words. */
export function actionText(line: Pick<SwitchLine, "action" | "share" | "sell" | "buy">): string {
  if (line.action === "hold") return `Keep ${line.sell}`;
  if (line.action === "full") return `Switch all of ${line.sell} into ${line.buy}`;
  return `Switch ${Math.round(line.share * 100)} % of ${line.sell} into ${line.buy}`;
}

/** Why a row has no score, or an empty string when it has one. */
export function missingText(row: Pick<RankingRow, "score" | "reasons">): string {
  if (row.score != null) return "";
  const reasons = row.reasons ?? [];
  return reasons.length > 0 ? `No score: ${reasons.join("; ")}.` : "No score.";
}

/** The score to two decimals, an em dash when there is none. */
export function scoreText(score: number | null | undefined): string {
  return score == null || !Number.isFinite(score) ? "—" : score.toFixed(2);
}

/** Whether a row clears the return hurdle and the asymmetry gate, as short words. */
export function gateText(row: Pick<RankingRow, "returns">): string {
  const r = row.returns;
  if (!r) return "—";
  const parts: string[] = [];
  if (r.meets_hurdle != null) parts.push(r.meets_hurdle ? "hurdle met" : "under hurdle");
  if (r.meets_asymmetry != null) parts.push(r.meets_asymmetry ? "upside outweighs loss" : "loss too large for upside");
  return parts.length > 0 ? parts.join(", ") : "—";
}

/** The portfolio test in words: does the idea beat what the book already earns per unit of risk. */
export function improvesText(value: boolean | null | undefined): string {
  if (value == null) return "—";
  return value ? "Improves the portfolio" : "Does not improve the portfolio";
}

/** The label on a risk figure: its stage, so a daily-close approximation is never read as a model. */
export function stageLabel(stage: string | null | undefined): string {
  if (stage === "B") return "model";
  if (stage === "A") return "approximate";
  return "";
}
