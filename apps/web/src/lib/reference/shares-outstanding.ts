import type { HoldingOwnership, SharesOutstandingOut } from "@/lib/api/client";
import { formatCompact, formatPct } from "@/lib/charts/format";

/*
 * 13F ownership as a share of shares outstanding (`HoldingOwnership.shares_outstanding`,
 * `pct_of_shares_outstanding`). The backend computes the fractions; this only words them:
 * which denominator, from which filing, on which basis (ADS ratio for US lines of foreign
 * issuers), and why a holding cannot have 13F figures at all.
 */

const EM_DASH = "—";

function toNumber(value: number | string | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

/** 0.0781 -> "7.8%", 0.00042 -> "0.04%", null -> "—". */
export function formatOutstandingPct(fraction: number | null | undefined): string {
  if (fraction === null || fraction === undefined || !Number.isFinite(fraction)) return EM_DASH;
  return formatPct(fraction, Math.abs(fraction) < 0.001 ? 2 : 1);
}

/** "1 ADS = 5 shares" for a ratio other than 1; null for one to one. */
export function adsRatioText(so: Pick<SharesOutstandingOut, "ads_ratio">): string | null {
  const ratio = toNumber(so.ads_ratio);
  if (ratio === null || ratio === 1) return null;
  return `1 US share = ${ratio} home-market shares`;
}

/**
 * The denominator in words: "25.93B shares (all share classes), as of 31 Dec 2025 · cover
 * page (dei), SEC companyfacts · 20-F".
 */
export function denominatorText(so: SharesOutstandingOut, formatDate: (d: string) => string): string {
  const shares = toNumber(so.shares);
  const parts = [
    `${shares === null ? EM_DASH : formatCompact(shares)} shares outstanding (${so.denominator_label})`,
    `as of ${formatDate(so.as_of)}`,
  ];
  const source = [so.method_label, so.form].filter(Boolean).join(" · ");
  return `${parts.join(", ")} — ${source}`;
}

export type OutstandingView =
  | { kind: "not_applicable"; reason: string }
  | { kind: "no_denominator" }
  | { kind: "ok"; pct: number | null; basis: string; ratio: string | null; so: SharesOutstandingOut };

/** What the ownership panels show about % of shares outstanding. */
export function outstandingView(data: HoldingOwnership | null): OutstandingView | null {
  if (!data) return null;
  if (data.applicable === false) {
    return { kind: "not_applicable", reason: data.not_applicable_reason ?? "Form 13F does not cover this holding." };
  }
  const so = data.shares_outstanding;
  if (!so || so.method === "manual") return { kind: "no_denominator" };
  return {
    kind: "ok",
    pct: data.pct_of_shares_outstanding ?? null,
    basis: data.basis_label ?? "13F filers' holdings of shares outstanding",
    ratio: adsRatioText(so),
    so,
  };
}

/** Per holder, by CIK: the fraction of shares outstanding (null when not computed). */
export function holderPctByCik(data: HoldingOwnership | null): Map<string, number | null> {
  return new Map((data?.top_holders ?? []).map((h) => [h.cik, h.pct_of_shares_outstanding ?? null]));
}
