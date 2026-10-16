"use client";

import Link from "next/link";
import { useCallback } from "react";

import { ChartFooter } from "@/components/insight/chart-footer";
import { SkeletonStrip } from "@/components/ui/skeleton";
import { getBrowserApiClient } from "@/lib/api/client";
import { companyHref, formatRatio, toNumber, type RatioRow, type RatiosView } from "@/lib/company/company";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

import type { CompanySectionId } from "./sections";

/** The few ratios that answer each section's question, in reading order. */
export const SECTION_FIGURES: Partial<Record<CompanySectionId, readonly string[]>> = {
  valuation: ["pe", "ev_to_ebit", "p_fcf", "fcf_yield"],
  past: ["revenue_cagr_3y", "operating_margin", "net_margin", "roic"],
  health: ["net_debt_to_ebitda", "interest_coverage", "fcf_margin", "roe"],
  dividend: ["dividend_yield", "fcf_yield", "fcf_margin"],
};

/** "Above its 5-year median", or null when either side is missing or the ratio has no better side. */
export function againstOwnHistory(row: Pick<RatioRow, "latest" | "own_median_5y" | "higher_is_better">): { text: string; better: boolean | null } | null {
  const now = toNumber(row.latest?.value);
  const med = toNumber(row.own_median_5y);
  if (now === null || med === null) return null;
  if (Math.abs(now - med) <= Math.abs(med) * 0.02) return { text: "In line with its 5-year median", better: null };
  const above = now > med;
  const better = row.higher_is_better === null ? null : above === row.higher_is_better;
  return { text: `${above ? "Above" : "Below"} its 5-year median`, better };
}

/**
 * Key figures under a section head (plan R4.3): up to four ratios with their period and how they
 * compare with the company's own 5-year median, then the data-status line (dataset, latest
 * period, prices date, sources).
 */
export function SectionFigures({ ticker, section }: { ticker: string; section: CompanySectionId }) {
  const ids = SECTION_FIGURES[section];
  const fetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/ratios", { params: { path: { ticker }, query: { peers: "none" } } }), [ticker]);
  const { state } = useApiResource(fetcher);
  if (!ids) return null;
  if (state.kind === "loading") return <SkeletonStrip />;
  if (state.kind === "error") return null;
  const view = state.data as RatiosView;
  const all = view.groups.flatMap((g) => g.ratios);
  const rows = ids.map((id) => all.find((r) => r.id === id)).filter((r): r is RatioRow => !!r);
  if (rows.length === 0) return null;
  const latest = rows.map((r) => r.latest?.label).find(Boolean);
  return (
    <div className="space-y-2">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 lg:grid-cols-4">
        {rows.map((r) => {
          const text = formatRatio(r.latest?.value, r.unit);
          const cmp = againstOwnHistory(r);
          return (
            <div key={r.id} className="min-w-0">
              <dt className="text-xs text-text-muted">{r.label}</dt>
              <dd className="kpi-figure num mt-0.5 text-[22px] leading-7">{text ?? <span className="text-text-faint">—</span>}</dd>
              <p className={cn("text-xs", cmp?.better === true ? "text-gain" : cmp?.better === false ? "text-loss" : "text-text-muted")}>
                {text ? (cmp?.text ?? r.latest?.label) : (r.latest?.reason ?? "Not computable from the filed statements")}
              </p>
            </div>
          );
        })}
      </dl>
      <ChartFooter
        dataset={`Filed statements${latest ? `, latest ${latest}` : ""}${view.price_date ? `; prices to ${formatDate(view.price_date)}` : ""}`}
        asOf={view.as_of ? formatDate(view.as_of) : null}
        source={view.sources.length ? `${view.sources.length} ${view.sources.length === 1 ? "filing" : "filings"}` : null}
        learnHref={companyHref(ticker, "ratios")}
      />
      {view.warnings.length > 0 ? (
        <p className="text-xs text-text-muted">
          {view.warnings[0]}{" "}
          {view.warnings.length > 1 ? (
            <Link href={companyHref(ticker, "ratios")} className="text-accent-ink hover:underline">
              {view.warnings.length - 1} more notes
            </Link>
          ) : null}
        </p>
      ) : null}
    </div>
  );
}
