/*
 * Pure logic for the Filings landing summary: alerts ranked by company, and the coverage
 * matrix of markets (the jurisdictions of your holdings) against kinds of data.
 */

export type AlertLike = { ticker: string; known_at: string };
export type CellLike = { dataset: string; label: string; jurisdiction: string; tickers: number; covered: number; stale: number };

export type CompanyAlerts = { ticker: string; count: number };

const DAY_MS = 86_400_000;

/** Alerts that became known in the last `days` days, counted per company, most first. */
export function alertsByCompany(alerts: readonly AlertLike[], now: number, days = 7): { rows: CompanyAlerts[]; total: number } {
  const since = now - days * DAY_MS;
  const counts = new Map<string, number>();
  let total = 0;
  for (const a of alerts) {
    const at = Date.parse(a.known_at);
    if (!Number.isFinite(at) || at < since || at > now + DAY_MS) continue;
    counts.set(a.ticker, (counts.get(a.ticker) ?? 0) + 1);
    total += 1;
  }
  const rows = [...counts].map(([ticker, count]) => ({ ticker, count })).sort((a, b) => b.count - a.count || a.ticker.localeCompare(b.ticker));
  return { rows, total };
}

export type CoverageStatus = "covered" | "partial" | "missing" | "none";

export const COVERAGE_STATUS_LABEL: Record<CoverageStatus, string> = {
  covered: "Covered",
  partial: "Partly covered",
  missing: "Missing",
  none: "No holdings",
};

/** Covered when every holding there has a record, partial when some do, missing when none do. */
export function cellStatus(cell: Pick<CellLike, "tickers" | "covered"> | undefined): CoverageStatus {
  if (!cell || cell.tickers === 0) return "none";
  if (cell.covered >= cell.tickers) return "covered";
  return cell.covered > 0 ? "partial" : "missing";
}

export type MatrixRow = { jurisdiction: string; cells: { dataset: string; status: CoverageStatus; text: string }[] };
export type Matrix = { columns: { dataset: string; label: string }[]; rows: MatrixRow[] };

export function coverageMatrix(cells: readonly CellLike[]): Matrix {
  const columns: { dataset: string; label: string }[] = [];
  for (const c of cells) if (!columns.some((x) => x.dataset === c.dataset)) columns.push({ dataset: c.dataset, label: c.label });
  const jurisdictions = [...new Set(cells.map((c) => c.jurisdiction))].sort();
  const rows = jurisdictions.map((jurisdiction) => ({
    jurisdiction,
    cells: columns.map((col) => {
      const cell = cells.find((c) => c.dataset === col.dataset && c.jurisdiction === jurisdiction);
      const status = cellStatus(cell);
      return { dataset: col.dataset, status, text: status === "none" ? "No holdings" : `${cell!.covered} of ${cell!.tickers}` };
    }),
  }));
  return { columns, rows };
}

/** Share of all (holdings x dataset) cells that are fully covered; null when nothing is held. */
export function coveredShare(cells: readonly CellLike[]): number | null {
  const tickers = cells.reduce((n, c) => n + c.tickers, 0);
  if (tickers === 0) return null;
  return cells.reduce((n, c) => n + c.covered, 0) / tickers;
}
