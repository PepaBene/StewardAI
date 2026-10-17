/*
 * What moved the portfolio over a window (a digest's own days): deterministic sentences from
 * each holding's contribution, in percentage points of the portfolio (visual spec D.3: no
 * AI-written sentence sits beside a number).
 */

export type Mover = { ticker: string; name: string; pct: number };

/** Below this a holding did not move the portfolio (0.005 pp, shown as 0.00 or 0.01). */
export const MOVED = 0.00005;

export function splitMovers(rows: readonly Mover[], n = 5): { up: Mover[]; down: Mover[]; still: number } {
  const up = rows.filter((r) => r.pct >= MOVED).sort((a, b) => b.pct - a.pct);
  const down = rows.filter((r) => r.pct <= -MOVED).sort((a, b) => a.pct - b.pct);
  return { up: up.slice(0, n), down: down.slice(0, n), still: rows.length - Math.min(up.length, n) - Math.min(down.length, n) };
}

function pp(v: number): string {
  return `${v < 0 ? "−" : "+"}${Math.abs(v * 100).toFixed(2)} pp`;
}

function pct(v: number): string {
  return `${Math.abs(v * 100).toFixed(2)}%`;
}

function names(movers: Mover[]): string {
  const parts = movers.map((m) => `${m.name} (${pp(m.pct)})`);
  return parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0];
}

/**
 * "Down 0.32%, against −0.77% for the S&P 500. Nvidia (−0.12 pp) and AMD (−0.08 pp) pulled it
 * down most; Philip Morris (+0.03 pp) helped." Null when there is no portfolio move to explain.
 */
export function moversSentence(
  portfolio: number | null,
  benchmark: { pct: number; name: string } | null,
  rows: readonly Mover[],
): string | null {
  if (portfolio === null) return null;
  const direction = Math.abs(portfolio) < 0.00005 ? "Flat" : portfolio > 0 ? `Up ${pct(portfolio)}` : `Down ${pct(portfolio)}`;
  const against = benchmark ? `, against ${benchmark.pct < 0 ? "−" : "+"}${pct(benchmark.pct)} for the ${benchmark.name}` : "";
  const { up, down } = splitMovers(rows, 2);
  const [lead, other] = portfolio < 0 ? [down, up] : [up, down];
  if (lead.length === 0) return `${direction}${against}. No holding moved it by 0.01 points or more.`;
  const verb = portfolio < 0 ? "pulled it down most" : "lifted it most";
  const offset = other.length > 0 ? `; ${names(other.slice(0, 1))} ${portfolio < 0 ? "helped" : "held it back"}` : "";
  return `${direction}${against}. ${names(lead)} ${verb}${offset}.`;
}
