"use client";

import Link from "next/link";
import { useCallback } from "react";

import { ScoreMark } from "@/components/insight/score-mark";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { ReasonOnce } from "@/components/ui/reason-once";
import { SkeletonRows } from "@/components/ui/skeleton";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { companyHref } from "@/lib/company/company";
import { formatDate, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { petalsFromMap } from "@/lib/insight/insight";
import { shortTicker } from "@/lib/portfolio/ticker";
import { cn } from "@/lib/utils";

type MarketContext = components["schemas"]["CompanyMarketContext"];

const WINDOW_LABEL: Record<string, string> = { "7d": "7 days", "1y": "1 year" };

function Signed({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) return <span className="text-text-faint">—</span>;
  return <span className={cn("num", value > 0 ? "text-gain" : value < 0 ? "text-loss" : "")}>{formatPercent(value, { decimals: 1, signed: true })}</span>;
}

/** Company, industry and market side by side for each window: the comparison the sentence sums up. */
function ReturnsTable({ ctx }: { ctx: MarketContext }) {
  const cols = [
    { key: "company" as const, label: shortTicker(ctx.ticker) },
    { key: "industry" as const, label: ctx.industry_label ?? "Industry" },
    { key: "market" as const, label: ctx.market_label ?? "Market" },
  ];
  return (
    <table className="w-full text-sm">
      <caption className="sr-only">Shareholder returns against the industry and the market</caption>
      <thead>
        <tr className="text-xs text-text-muted">
          <th scope="col" className="py-1 text-left font-normal">Period</th>
          {cols.map((c) => (
            <th key={c.key} scope="col" className="max-w-[10rem] truncate py-1 text-right font-normal">
              {c.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {ctx.returns.map((r) => (
          <tr key={r.window} className="border-t border-border-soft">
            <th scope="row" className="py-1.5 text-left font-normal">{WINDOW_LABEL[r.window] ?? r.window}</th>
            {cols.map((c) => (
              <td key={c.key} className={cn("py-1.5 text-right", c.key === "company" && "font-semibold")}>
                <Signed value={r[c.key]} />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Weekly price swings as three marks on one scale, so "more volatile than the market" is seen, not read. */
function VolatilityScale({ v }: { v: MarketContext["volatility"] }) {
  const items = [
    { key: "company", label: "This company", value: v.company_annualised, strong: true },
    { key: "industry", label: "Industry", value: v.industry_annualised, strong: false },
    { key: "market", label: "Market", value: v.market_annualised, strong: false },
  ].filter((i): i is typeof i & { value: number } => i.value !== null && i.value !== undefined);
  if (items.length === 0) return null;
  const max = Math.max(0.4, ...items.map((i) => i.value)) * 1.1;
  return (
    <div className="space-y-1.5" role="img" aria-label={items.map((i) => `${i.label} ${formatPercent(i.value, { decimals: 0 })} a year`).join(", ")}>
      {items.map((i) => (
        <div key={i.key} className="grid grid-cols-[6.5rem_1fr_3rem] items-center gap-2 text-xs">
          <span className={i.strong ? "font-medium text-text" : "text-text-muted"}>{i.label}</span>
          <span className="relative h-2 rounded-full bg-chart-track">
            <span
              className={cn("absolute inset-y-0 left-0 rounded-full", i.strong ? "bg-chart-line" : "bg-chart-benchmark")}
              style={{ width: `${(i.value / max) * 100}%` }}
            />
          </span>
          <span className="num text-right">{formatPercent(i.value, { decimals: 0 })}</span>
        </div>
      ))}
    </div>
  );
}

function CompetitorCards({ ctx }: { ctx: MarketContext }) {
  if (ctx.competitors.length === 0) return null;
  return (
    <ul className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {ctx.competitors.map((c) => (
        <li key={c.ticker}>
          <Link
            href={companyHref(c.ticker)}
            className="flex h-full flex-col items-center gap-2 rounded-card bg-surface-muted px-3 py-3 text-center hover:ring-1 hover:ring-border focus-visible:outline-2 focus-visible:outline-accent"
          >
            {c.scores ? (
              <ScoreMark petals={petalsFromMap(c.scores)} size="header" label={c.name ?? c.ticker} />
            ) : (
              <span aria-hidden="true" className="size-16 rounded-mark bg-chart-track" />
            )}
            <span className="min-w-0">
              <span className="block truncate text-sm font-medium">{c.name ?? shortTicker(c.ticker)}</span>
              <span className="block text-xs text-text-muted">
                {shortTicker(c.ticker)}
                {c.last_price !== null ? `, ${c.currency ?? ""} ${c.last_price.toLocaleString("en-GB", { maximumFractionDigits: 2 })}` : ""}
              </span>
              {!c.scores ? <span className="block text-xs text-text-faint">No checks run yet</span> : null}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

/**
 * The company against its industry and the market (plan §5.4 Overview): shareholder returns,
 * price swings and up to four competitors with their marks.
 */
export function MarketContextPanel({ ticker }: { ticker: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/market-context", { params: { path: { ticker } } }), [ticker]);
  const { state } = useApiResource(fetcher);
  if (state.kind === "error") return null;
  if (state.kind === "loading") {
    return (
      <Blueprint className="px-[22px] py-5">
        <PanelHeading title="Against the industry and the market" />
        <div className="mt-3">
          <SkeletonRows rows={3} />
        </div>
      </Blueprint>
    );
  }
  const ctx = state.data as MarketContext;
  const hasReturns = ctx.returns.some((r) => r.company !== null);
  return (
    <Blueprint aria-labelledby="market-context-heading" className="space-y-5 px-[22px] py-5">
      <PanelHeading
        title={<span id="market-context-heading">Against the industry and the market</span>}
        kicker={ctx.as_of ? `Closes to ${formatDate(ctx.as_of)}` : undefined}
      />
      <div className="grid gap-6 lg:grid-cols-2">
        {hasReturns ? (
          <section aria-label="Shareholder returns" className="space-y-2">
            <h3 className="text-sm font-medium">Shareholder returns</h3>
            {ctx.return_sentence ? <p className="text-sm text-text-muted">{ctx.return_sentence}</p> : null}
            <ReturnsTable ctx={ctx} />
            {ctx.industry_sentence ? <p className="text-xs text-text-muted">{ctx.industry_sentence}</p> : null}
          </section>
        ) : null}
        {ctx.volatility.company_annualised !== null ? (
          <section aria-label="Price swings" className="space-y-2">
            <h3 className="text-sm font-medium">Price swings over a year</h3>
            {ctx.volatility.sentence ? <p className="text-sm text-text-muted">{ctx.volatility.sentence}</p> : null}
            <VolatilityScale v={ctx.volatility} />
          </section>
        ) : null}
      </div>
      {ctx.competitors.length > 0 ? (
        <section aria-label="Competitors" className="space-y-2">
          <h3 className="text-sm font-medium">Competitors</h3>
          <CompetitorCards ctx={ctx} />
        </section>
      ) : null}
      <ReasonOnce reasons={ctx.reasons} />
    </Blueprint>
  );
}
