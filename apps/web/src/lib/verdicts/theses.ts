import type { Verdict } from "@/components/insight/verdict-line";
import type { BoardRow } from "@/lib/api/client";
import type { RankingRow } from "@/lib/risk/ranking";

/*
 * The thesis board and ranking in words (visual spec D.3): deterministic sentences that say what
 * the scores and returns mean for the owner, so no number on /theses stands without its reading.
 */

export type VerdictResult = { tone: Verdict; text: string };

type ScoredRow = Pick<BoardRow, "ticker" | "name" | "state" | "score" | "counts" | "kpis">;
type RankedRow = Pick<RankingRow, "ticker" | "name" | "held" | "returns">;

function count(row: Pick<BoardRow, "counts">, key: "pass" | "breach" | "unknown"): number {
  return row.counts?.[key] ?? 0;
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

function nameOf(row: { ticker: string; name?: string | null }): string {
  return row.name || row.ticker;
}

function list(names: string[]): string {
  return names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : (names[0] ?? "");
}

/** What a card's score says: "3 of 4 KPIs pass; 1 has no verdict yet." */
export function scoreExplain(row: Pick<BoardRow, "counts" | "kpis" | "score">): string {
  const total = row.kpis.length;
  if (total === 0) return "This thesis has no KPIs, so nothing is judged.";
  if (row.score == null) return `None of its ${plural(total, "KPI")} has a verdict yet.`;
  const parts = [`${count(row, "pass")} of ${plural(total, "KPI")} pass`];
  if (count(row, "breach") > 0) parts.push(`${count(row, "breach")} breached`);
  if (count(row, "unknown") > 0) parts.push(`${count(row, "unknown")} without a verdict yet`);
  return `${parts.join("; ")}.`;
}

/** The board in one sentence: which theses are breaking, and what an all-intact board means. */
export function boardVerdict(rows: readonly ScoredRow[]): VerdictResult {
  const by = (s: BoardRow["state"]) => rows.filter((r) => r.state === s);
  const atRisk = by("at_risk");
  const watch = by("watch");
  const intact = by("intact");
  const unjudged = by("unjudged");
  const tail = unjudged.length > 0 ? ` ${plural(unjudged.length, "thesis", "theses")} without judged KPIs (${list(unjudged.map(nameOf))}) are not scored.` : "";
  if (atRisk.length + watch.length === 0) {
    return {
      tone: intact.length > 0 ? "good" : "unknown",
      text:
        intact.length > 0
          ? `All ${plural(intact.length, "judged thesis", "judged theses")} are intact: no KPI is breached by the latest reported figures. That says the businesses deliver what their theses measure, not that the stocks are cheap; the ranking below covers price.${tail}`
          : `No thesis has a judged KPI yet.${tail}`,
    };
  }
  const parts: string[] = [];
  if (atRisk.length > 0) parts.push(`${plural(atRisk.length, "thesis", "theses")} at risk (${list(atRisk.map(nameOf))})`);
  if (watch.length > 0) parts.push(`${watch.length} on watch (${list(watch.map(nameOf))})`);
  return {
    tone: atRisk.length > 0 ? "bad" : "caution",
    text: `${list(parts)}; ${intact.length} intact. A breached KPI calls for a thesis review, not an automatic sale.${tail}`,
  };
}

/** Holdings whose score is below 100, weakest first, each with why. */
export function weakest(rows: readonly ScoredRow[]): { row: ScoredRow; reason: string }[] {
  return rows
    .filter((r) => r.score != null && r.score < 100)
    .sort((a, b) => (a.score ?? 0) - (b.score ?? 0))
    .map((row) => ({ row, reason: scoreExplain(row) }));
}

/** The ranking in one sentence: which theses clear the owner's return hurdle and what that implies. */
export function rankingVerdict(rows: readonly RankedRow[], hurdle: number | null | undefined): VerdictResult | null {
  const valued = rows.filter((r) => r.returns != null);
  if (valued.length === 0) return null;
  const pct = hurdle == null ? null : Math.round(hurdle * 100);
  const clear = valued.filter((r) => r.returns?.meets_hurdle);
  const losing = valued.filter((r) => (r.returns?.total ?? 0) < 0);
  const thin = valued.filter((r) => {
    const t = r.returns?.total ?? 0;
    return t >= 0 && t < 0.1;
  });
  const who =
    clear.length === 0
      ? "No thesis clears"
      : clear.length === 1
        ? `Only ${nameOf(clear[0])} clears`
        : `${clear.length} theses (${list(clear.map(nameOf))}) clear`;
  const hurdleText = pct == null ? "the return hurdle" : `your ${pct}% return hurdle`;
  let text = `${who} ${hurdleText}. ${losing.length} of ${valued.length} expect a loss to their own probability-weighted value and ${thin.length} expect less than 10%.`;
  if (losing.length + thin.length > valued.length / 2) {
    text += " By your own theses most of the book is priced at or above what it is worth, so new money belongs in the core ETFs (principle 2c) rather than in more of these.";
  }
  return { tone: clear.length > 0 ? "neutral" : "caution", text };
}

/** What the ranking's columns mean, in the owner's words. */
export const RANKING_COLUMNS = {
  expected: "Return to the thesis's probability-weighted value (bear, base and bull), from today's price.",
  year: "The same return per year, to the thesis's catalyst date (two years when it has none).",
  volatility: "How much the price swung over the last year.",
  score: "Return a year above the cash rate, per unit of volatility. Above 0 beats cash for the risk taken; higher is better.",
  book: "Whether adding to it would raise the whole portfolio's return per unit of risk.",
} as const;
