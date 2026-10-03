"use client";

import { useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ChartFrame } from "@/components/data-state";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPct } from "@/lib/charts/format";
import { formatDay } from "@/lib/charts/time";
import { portfolioPriceCoverage } from "@/lib/markets/logic";
import type { HoldingsPrices } from "@/lib/markets/types";

/**
 * Price completeness across the portfolio (GET /portfolio/holdings/prices): which holdings have
 * stored bars in the window, how many, the latest close and its source. A holding with no bars
 * is named, never counted as flat. A confirmed action starts the price-history job.
 */
export function PortfolioCoveragePanel({ data, onSelect }: { data: HoldingsPrices; onSelect?: (ticker: string) => void }) {
  const cov = portfolioPriceCoverage(data.holdings);
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  async function run() {
    setBusy(true);
    setMessage(null);
    try {
      const { data: job, response } = await getBrowserApiClient().POST("/jobs/price-history");
      setMessage(response.ok && job ? { ok: true, text: `Price-history job #${job.id} queued (${job.status}).` } : { ok: false, text: `The job was not queued (HTTP ${response.status}).` });
    } catch (err) {
      setMessage({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
      setArmed(false);
    }
  }

  return (
    <ChartFrame
      id="portfolio-coverage"
      kicker="Portfolio price completeness"
      title="Which holdings have prices"
      takeaway={
        cov.total === 0
          ? "No holdings are in the latest snapshot."
          : cov.withoutBars.length === 0
            ? `All ${cov.total} holdings have stored bars in the last ${data.days} days${cov.stale.length > 0 ? `; ${cov.stale.join(", ")} lag the newest by more than 5 days` : ""}.`
            : `${cov.withoutBars.length} of ${cov.total} holdings have no stored bars in the last ${data.days} days: ${cov.withoutBars.join(", ")}.`
      }
      units={`Holdings with at least one stored bar in the last ${data.days} days; staleness is calendar days behind the newest holding`}
      hasValue={cov.total > 0}
      controls={
        armed ? (
          <>
            <span className="text-xs text-text-muted">Collect price and FX history now?</span>
            <Button size="sm" disabled={busy} onClick={() => void run()}>
              {busy ? "Queuing…" : "Yes, run job"}
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setArmed(false)}>
              Cancel
            </Button>
          </>
        ) : (
          <Button size="sm" variant="outline" onClick={() => setArmed(true)}>
            Refresh price history…
          </Button>
        )
      }
      provenance={{
        source: "Stored daily bars (Stooq, else Yahoo, as for the holding page) via GET /portfolio/holdings/prices",
        asOf: cov.newestAsOf,
        method: "Count of holdings with bars in the window; per-series completeness against the exchange calendar is in the diagnostics panel",
        state: cov.total === 0 ? "not_covered" : cov.withoutBars.length > 0 ? "partial" : cov.stale.length > 0 ? "stale" : "verified",
        reason: cov.withoutBars.length > 0 ? `${cov.withoutBars.length} holding${cov.withoutBars.length === 1 ? " has" : "s have"} no stored prices.` : null,
        coverage: cov.total > 0 ? { covered: cov.withBars, total: cov.total, unit: "holdings" } : null,
      }}
      table={{
        caption: "Stored prices per holding",
        columns: ["Holding", "Source", "Bars", "Last close", "As of", "Day change"],
        rows: data.holdings.map((h) => [
          h.ticker,
          h.source ?? "none",
          h.bars.length,
          h.last_close === null ? "none" : h.last_close.toLocaleString("en-US", { maximumFractionDigits: 2 }),
          h.as_of ? formatDay(h.as_of) : "none",
          h.day_change_pct === null ? "n/a" : formatPct(h.day_change_pct / 100, 2, true),
        ]),
      }}
    >
      {message ? <AlertMessage tone={message.ok ? "info" : "warning"} title={message.ok ? "Job queued" : "Job not queued"}>{message.text}</AlertMessage> : null}
      <ul className="m-0 grid list-none grid-cols-1 gap-1.5 p-0 text-sm sm:grid-cols-2 lg:grid-cols-3">
        {data.holdings.map((h) => {
          const missing = h.bars.length === 0;
          return (
            <li key={h.ticker} className={missing ? "border border-dashed border-border px-3 py-2" : "border border-border-soft px-3 py-2"}>
              <button type="button" onClick={() => onSelect?.(h.ticker)} className="block w-full min-w-0 text-left outline-hidden focus-visible:outline-2 focus-visible:outline-focus">
                <span className="block truncate font-medium">{h.ticker}</span>
                <span className="num block text-xs text-text-muted">
                  {missing ? "No stored bars" : `${h.bars.length} bars · ${h.source ?? "source n/a"} · as of ${h.as_of ? formatDay(h.as_of) : "n/a"}`}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </ChartFrame>
  );
}
