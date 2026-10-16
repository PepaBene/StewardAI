"use client";

import { useEffect, useMemo, useState } from "react";

import { EntityName } from "@/components/entity-name";
import { KpiStrip } from "@/components/insight/kpi-tile";
import { getBrowserApiClient } from "@/lib/api/client";
import { COVERAGE_STATUS_LABEL, alertsByCompany, coverageMatrix, coveredShare, type CoverageStatus } from "@/lib/disclosure/summary";
import type { CoverageReport } from "@/lib/disclosure/coverage";
import type { DisclosureAlert } from "@/lib/disclosure/alerts";
import { cn } from "@/lib/utils";

type Load<T> = { kind: "loading" } | { kind: "error" } | { kind: "ok"; data: T };

const CELL: Record<CoverageStatus, string> = {
  covered: "bg-accent text-accent-foreground",
  partial: "bg-accent-300 text-text",
  missing: "border border-loss text-loss",
  none: "border border-dashed border-border text-text-muted",
};

/**
 * The landing view of Filings: this week's alerts ranked by company, and a status matrix of
 * your holdings' markets against kinds of data. Every cell says its state in words.
 */
export function PortfolioSummary({ issuers }: { issuers: number }) {
  const [alerts, setAlerts] = useState<Load<DisclosureAlert[]>>({ kind: "loading" });
  const [coverage, setCoverage] = useState<Load<CoverageReport>>({ kind: "loading" });
  const [now] = useState(() => Date.now());

  useEffect(() => {
    let live = true;
    const api = getBrowserApiClient();
    api
      .GET("/disclosures/alerts", { params: { query: { include_acknowledged: true, limit: 200 } } })
      .then((r) => live && setAlerts(r.data ? { kind: "ok", data: r.data } : { kind: "error" }))
      .catch(() => live && setAlerts({ kind: "error" }));
    api
      .GET("/disclosures/data/coverage", { params: { query: { include_watchlist: false } } })
      .then((r) => live && setCoverage(r.data ? { kind: "ok", data: r.data } : { kind: "error" }))
      .catch(() => live && setCoverage({ kind: "error" }));
    return () => {
      live = false;
    };
  }, []);

  const week = useMemo(() => (alerts.kind === "ok" ? alertsByCompany(alerts.data, now) : null), [alerts, now]);
  const matrix = useMemo(() => (coverage.kind === "ok" ? coverageMatrix(coverage.data.cells) : null), [coverage]);
  const share = coverage.kind === "ok" ? coveredShare(coverage.data.cells) : null;
  const max = week?.rows[0]?.count ?? 1;

  return (
    <section aria-labelledby="summary-heading" className="space-y-4">
      <h2 id="summary-heading" className="font-heading text-[22px] leading-tight font-semibold">
        Your portfolio this week
      </h2>

      <KpiStrip
        label="Filings summary"
        tiles={[
          { label: "Alerts this week", value: week ? <span className="num">{week.total}</span> : "…" },
          { label: "Coverage share", value: share === null ? "Unknown" : <span className="num">{Math.round(share * 100)}%</span>, note: share === null ? "No holdings to measure" : "of holdings have records" },
          { label: "Issuers", value: <span className="num">{issuers}</span>, note: "with stored filings" },
        ]}
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-2 rounded-xl border border-border bg-surface-raised p-4">
          <h3 className="font-heading text-base font-semibold">Alerts by company</h3>
          {alerts.kind === "error" ? <p className="text-sm text-text-muted">Alerts could not be loaded. Reload the page to try again.</p> : null}
          {week && week.rows.length === 0 ? <p className="text-sm text-text-muted">No alerts in the last 7 days.</p> : null}
          {week && week.rows.length > 0 ? (
            <ol className="m-0 list-none space-y-1.5 p-0" aria-label="Alerts in the last 7 days, most first">
              {week.rows.slice(0, 8).map((r) => (
                <li key={r.ticker} className="grid grid-cols-[minmax(0,9rem)_1fr_2rem] items-center gap-2 text-sm">
                  <EntityName id={r.ticker} className="truncate" />
                  <span className="h-2 rounded-sm bg-surface-muted" aria-hidden="true">
                    <span className="block h-full rounded-sm bg-accent" style={{ width: `${(r.count / max) * 100}%` }} />
                  </span>
                  <span className="num text-right">{r.count}</span>
                </li>
              ))}
            </ol>
          ) : null}
        </div>

        <div className="space-y-2 rounded-xl border border-border bg-surface-raised p-4">
          <h3 className="font-heading text-base font-semibold">What we have on your holdings</h3>
          {coverage.kind === "error" ? <p className="text-sm text-text-muted">Coverage could not be loaded. Reload the page to try again.</p> : null}
          {matrix && matrix.rows.length === 0 ? <p className="text-sm text-text-muted">Add holdings to see which filings and data we cover.</p> : null}
          {matrix && matrix.rows.length > 0 ? (
            <>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <caption className="sr-only">Holdings by market and kind of data</caption>
                  <thead>
                    <tr>
                      <th scope="col" className="py-1 pr-2 text-left font-medium text-text-muted">
                        Market
                      </th>
                      {matrix.columns.map((c) => (
                        <th key={c.dataset} scope="col" className="px-1 py-1 text-left font-medium text-text-muted">
                          {c.label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {matrix.rows.map((r) => (
                      <tr key={r.jurisdiction}>
                        <th scope="row" className="py-1 pr-2 text-left font-medium">
                          {r.jurisdiction}
                        </th>
                        {r.cells.map((c) => (
                          <td key={c.dataset} className="p-0.5">
                            <span className={cn("block rounded-sm px-1.5 py-1 whitespace-nowrap", CELL[c.status])}>
                              {COVERAGE_STATUS_LABEL[c.status]}
                              <span className="num block opacity-80">{c.text}</span>
                            </span>
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <ul className="m-0 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-xs text-text-muted" aria-label="Legend">
                {(["covered", "partial", "missing", "none"] as const).map((s) => (
                  <li key={s} className="flex items-center gap-1.5">
                    <span aria-hidden="true" className={cn("size-3 rounded-sm", CELL[s])} />
                    {COVERAGE_STATUS_LABEL[s]}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      </div>
    </section>
  );
}
