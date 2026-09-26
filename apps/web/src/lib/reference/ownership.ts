import type { HoldingOwnership, ManagerChange, OwnershipPeriod } from "@/lib/api/client";

/*
 * Institutional ownership from SEC Form 13F data sets (`GET /holdings/{ticker}/ownership`)
 * and tracked managers' new / exited positions in a digest. Figures are the backend's; only
 * formatted here. Every figure is dated: the report period (quarter end) and the latest
 * filing date, since managers file up to 45 days after the quarter ends.
 */

export const OWNERSHIP_NOTE = "SEC Form 13F; managers above USD 100 M; filed up to 45 days after quarter end.";

const EM_DASH = "—";

function toNumber(value: number | string | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

/** 1234567 -> "1,234,567" (shares are whole numbers in 13F). */
export function formatShares(value: number | string | null | undefined, locale?: string): string {
  const n = toNumber(value);
  return n === null ? EM_DASH : new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(n);
}

/** 1.5e9 -> "USD 1.5 bn", 2.3e6 -> "USD 2.3 m", smaller as a plain amount. */
export function formatUsdCompact(value: number | string | null | undefined, locale?: string): string {
  const n = toNumber(value);
  if (n === null) return EM_DASH;
  const abs = Math.abs(n);
  const fmt = (v: number) => new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(v);
  if (abs >= 1e9) return `USD ${fmt(n / 1e9)} bn`;
  if (abs >= 1e6) return `USD ${fmt(n / 1e6)} m`;
  return `USD ${new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(n)}`;
}

/** +3 / −2 / 0 / "—" (no earlier period loaded), with the sign in words for screen readers. */
export function formatChange(value: number | string | null | undefined, locale?: string): { text: string; words: string } {
  const n = toNumber(value);
  if (n === null) return { text: EM_DASH, words: "no earlier period to compare" };
  const text = new Intl.NumberFormat(locale, { maximumFractionDigits: 0, signDisplay: "exceptZero" }).format(n);
  return { text, words: n > 0 ? `up ${Math.abs(n)}` : n < 0 ? `down ${Math.abs(n)}` : "unchanged" };
}

/** "Quarter ended 30 Jun 2026 · filed up to 14 Aug 2026". */
export function periodLabel(period: Pick<OwnershipPeriod, "period" | "latest_filed">, formatDate: (d: string) => string): string {
  const filed = period.latest_filed ? ` · filed up to ${formatDate(period.latest_filed.slice(0, 10))}` : " · filing date unknown";
  return `As of ${formatDate(period.period)}${filed}`;
}

export type OwnershipState =
  | { kind: "empty"; reason: string }
  | { kind: "ok"; latest: OwnershipPeriod; periods: OwnershipPeriod[] };

/**
 * Empty when no loaded 13F filing lists the holding (no CUSIP matched: usually a non-US
 * listing, which 13F does not cover), with the reason in words.
 */
export function ownershipState(data: HoldingOwnership): OwnershipState {
  if (data.cusips.length === 0 || data.periods.length === 0) {
    return {
      kind: "empty",
      reason:
        "No 13F filing in the data sets loaded so far lists this holding. Form 13F covers US-listed securities only, so a holding listed elsewhere has no 13F ownership; for a US listing, the next data set load may add it.",
    };
  }
  const periods = [...data.periods].sort((a, b) => b.period.localeCompare(a.period));
  return { kind: "ok", latest: periods[0], periods };
}

// ---------------------------------------------------------------------------
// Tracked managers in a digest
// ---------------------------------------------------------------------------

export function isNewPosition(change: Pick<ManagerChange, "change">): boolean {
  return change.change === "new";
}

/** New positions first, then exits; by manager, then ticker. */
export function sortManagerChanges(changes: readonly ManagerChange[]): ManagerChange[] {
  return [...changes].sort(
    (a, b) =>
      Number(!isNewPosition(a)) - Number(!isNewPosition(b)) ||
      (a.manager_name ?? a.cik).localeCompare(b.manager_name ?? b.cik) ||
      a.ticker.localeCompare(b.ticker),
  );
}

/** "Opened a position of 1,200 shares" / "Exited (held 1,200 shares)". */
export function managerChangeText(change: ManagerChange, locale?: string): string {
  const shares = formatShares(change.shares, locale);
  if (isNewPosition(change)) return shares === EM_DASH ? "Opened a position" : `Opened a position of ${shares} shares`;
  if (change.change === "exited") return shares === EM_DASH ? "Exited the position" : `Exited the position (held ${shares} shares)`;
  return change.change;
}

/** The SEC's EDGAR page of a filing, from its accession number ("0001067983-26-000012"). */
export function edgarFilingUrl(cik: string, accession: string): string | null {
  const cleanCik = cik.replace(/^0+/, "");
  if (!/^\d+$/.test(cleanCik) || !/^\d{10}-\d{2}-\d{6}$/.test(accession)) return null;
  return `https://www.sec.gov/Archives/edgar/data/${cleanCik}/${accession.replaceAll("-", "")}/${accession}-index.htm`;
}
