"use client";

import { useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Segmented, StatePlaceholder } from "@/components/data-state";
import { ActionLedger } from "@/components/markets/action-ledger";
import { CorrelationPanel } from "@/components/markets/correlation-panel";
import { EventStudyPanel } from "@/components/markets/event-study-panel";
import { PortfolioCoveragePanel } from "@/components/markets/portfolio-coverage";
import { PriceChartPanel } from "@/components/markets/price-chart-panel";
import { PriceDiagnosticsPanel } from "@/components/markets/price-diagnostics";
import { lastGood, ParsedGate, ResourceGate } from "@/components/markets/resource-gate";
import { ReturnsPanel } from "@/components/markets/returns-panel";
import { SessionStrip } from "@/components/markets/session-strip";
import { PageHeader } from "@/components/page-header";
import { assembleCorrelation, addDays, buildSessionCells, chosenSeries, eventStudyGate, todayIso } from "@/lib/markets/logic";
import {
  useCorporateActions,
  useCorrelationRows,
  useExchangeCalendar,
  useExchangeSessions,
  useExpectedSessions,
  useHoldingListing,
  useHoldingPrices,
  useHoldingReturns,
  usePortfolioHoldings,
  usePortfolioHoldingsPrices,
  usePortfolioRisk,
  usePriceDiagnostics,
} from "@/lib/markets/use-markets";
import type { Parsed, PriceDiagnostic } from "@/lib/markets/types";

const RETURN_WINDOWS = [
  { id: "30", label: "1M" },
  { id: "91", label: "3M" },
  { id: "182", label: "6M" },
  { id: "365", label: "1Y" },
] as const;

/**
 * Price coverage and corporate-action truth (free-data plan, Phase 3), from the live API only:
 * candles with the corporate-action lane and an explicit price basis, the action ledger, window
 * returns by basis, an event study that appears only on a reconciled series, venue-session
 * coverage, price-source diagnostics, cross-holding correlation and portfolio completeness.
 */
export function MarketsView() {
  const holdings = usePortfolioHoldings();
  const [selected, setSelected] = useState<string | null>(null);
  const known = lastGood(holdings.state);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Portfolio"
        title="Price coverage"
        description="Defensible prices and returns: raw and adjusted figures kept apart, every corporate action sourced, and a gap on a real trading day told apart from a holiday."
        aside={
          known ? (
            <HoldingSelect holdings={known.holdings.map((h) => ({ ticker: h.ticker, name: h.name }))} value={selected} onChange={setSelected} />
          ) : null
        }
      />
      <ResourceGate state={holdings.state} label="holdings">
        {(data) => {
          const list = data.holdings;
          if (list.length === 0) {
            return <StatePlaceholder state="not_covered" reason="The latest portfolio snapshot has no open positions, so there is nothing to price. Sync the portfolio first." />;
          }
          const current = list.find((h) => h.ticker === selected) ?? list[0];
          return (
            <>
              <HoldingSection key={current.ticker} ticker={current.ticker} name={current.name} />
              <CorrelationSection holdings={list.map((h) => ({ ticker: h.ticker, name: h.name }))} />
              <CoverageSection onSelect={setSelected} />
            </>
          );
        }}
      </ResourceGate>
    </div>
  );
}

function HoldingSelect({ holdings, value, onChange }: { holdings: { ticker: string; name: string | null }[]; value: string | null; onChange: (t: string) => void }) {
  return (
    <label className="flex min-w-0 items-center gap-2 text-xs text-text-muted">
      Holding
      <select
        value={value ?? holdings[0]?.ticker ?? ""}
        onChange={(e) => onChange(e.target.value)}
        className="h-9 min-w-0 max-w-[16rem] border border-border bg-surface-raised px-2 text-sm text-text outline-none focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30"
      >
        {holdings.map((h) => (
          <option key={h.ticker} value={h.ticker}>
            {h.ticker}
            {h.name ? ` · ${h.name}` : ""}
          </option>
        ))}
      </select>
    </label>
  );
}

function parsedOr<T>(p: Parsed<T> | undefined): { data: T | null; reason: string | null } {
  if (!p) return { data: null, reason: null };
  return p.ok ? { data: p.data, reason: null } : { data: null, reason: p.reason };
}

function HoldingSection({ ticker, name }: { ticker: string; name: string | null }) {
  const prices = useHoldingPrices(ticker, 400);
  const diagnostics = usePriceDiagnostics(ticker);
  const actions = useCorporateActions(ticker);
  const listing = useHoldingListing(ticker);
  const [windowId, setWindowId] = useState<(typeof RETURN_WINDOWS)[number]["id"]>("365");
  const today = useMemo(() => todayIso(), []);
  const returns = useHoldingReturns(ticker, addDays(today, -Number(windowId)), today);

  const diagRows: readonly PriceDiagnostic[] | null = lastGood(diagnostics.state);
  const actionList = parsedOr(lastGood(actions.state) ?? undefined);
  const actionsReason = actions.state.kind === "error" && !actions.state.previous ? actions.state.title : actions.state.kind === "loading" ? "still loading" : actionList.reason;
  const returnsParsed = parsedOr(lastGood(returns.state) ?? undefined);
  const listingParsed = parsedOr(lastGood(listing.state) ?? undefined);
  const chosen = diagRows ? chosenSeries(diagRows) : null;
  const mic = chosen?.mic ?? listingParsed.data?.listing?.mic ?? null;

  return (
    <>
      <ResourceGate state={prices.state} label="price history">
        {(p) => (
          <>
            <PriceChartPanel ticker={ticker} name={name} prices={p} diagnostics={diagRows} actions={actionList.data} actionsReason={actionsReason} />
            <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-2">
              <ResourceGate state={returns.state} label="returns">
                {(r) => (
                  <ParsedGate parsed={r} label="returns">
                    {(report) => (
                      <ReturnsPanel
                        report={report}
                        windowControl={<Segmented label="Return window" value={windowId} onChange={setWindowId} options={RETURN_WINDOWS} />}
                      />
                    )}
                  </ParsedGate>
                )}
              </ResourceGate>
              <EventStudyPanel
                ticker={ticker}
                source={`${p.source}${p.symbol ? ` · ${p.symbol}` : ""}`}
                bars={p.bars}
                actions={actionList.data ?? []}
                gate={eventStudyGate({
                  hasAdjustedBars: p.bars.length > 0,
                  returns: returnsParsed.data,
                  actions: actionList.data ?? [],
                })}
              />
            </div>
            {mic ? (
              <SessionSection ticker={ticker} mic={mic} barDates={p.bars.map((b) => b.date)} firstBar={p.bars[0]?.date ?? null} asOf={p.as_of} />
            ) : (
              <StatePlaceholder state="not_covered" reason="The exchange (MIC) of this holding is not known from its stored series or a derived listing, so no venue calendar can be checked." />
            )}
          </>
        )}
      </ResourceGate>

      <ResourceGate state={actions.state} label="corporate actions">
        {(a) => <ParsedGate parsed={a} label="corporate-actions">{(list) => <ActionLedger ticker={ticker} actions={list} onChanged={actions.reload} />}</ParsedGate>}
      </ResourceGate>

      <ResourceGate state={diagnostics.state} label="price diagnostics">
        {(rows) => (
          <PriceDiagnosticsPanel
            ticker={ticker}
            rows={rows}
            listing={listingParsed.data}
            listingNote={
              listing.state.kind === "loading"
                ? "Loading the derived listing…"
                : listingParsed.reason
                  ? `The listing response did not match the expected shape (${listingParsed.reason}).`
                  : listing.state.kind === "error" && listing.state.status === 404
                    ? "No listing has been derived for this holding yet; corporate actions cannot be recorded until one is confirmed."
                    : listing.state.kind === "error"
                      ? `The listing could not be read: ${listing.state.title}.`
                      : null
            }
          />
        )}
      </ResourceGate>
    </>
  );
}

const SESSION_BACK = 90;
const SESSION_AHEAD = 28;

function SessionSection({ ticker, mic, barDates, firstBar, asOf }: { ticker: string; mic: string; barDates: readonly string[]; firstBar: string | null; asOf: string | null }) {
  const today = useMemo(() => todayIso(), []);
  const start = addDays(today, -SESSION_BACK);
  const end = addDays(today, SESSION_AHEAD);
  const sessions = useExchangeSessions(mic, start, end);
  const expected = useExpectedSessions(mic, start, end);
  const calendar = useExchangeCalendar();

  const sessionsP = lastGood(sessions.state);
  const expectedP = lastGood(expected.state);
  const calendarData = lastGood(calendar.state);

  const loading = [sessions.state, expected.state, calendar.state].some((s) => s.kind === "loading");
  const failed = [sessions.state, expected.state, calendar.state].find((s) => s.kind === "error" && (s.previous === null || s.previous === undefined));
  if (loading) return <p role="status" className="text-sm text-text-muted">Loading venue sessions…</p>;
  if (failed && failed.kind === "error") {
    return <AlertMessage title="Could not load venue sessions">{failed.title}. {failed.detail}</AlertMessage>;
  }
  if (!sessionsP || !expectedP || !calendarData) return null;
  if (!sessionsP.ok) return <StatePlaceholder state="unavailable" reason={`The exchange-sessions response did not match the expected shape (${sessionsP.reason}).`} />;
  if (!expectedP.ok) return <StatePlaceholder state="unavailable" reason={`The expected-sessions response did not match the expected shape (${expectedP.reason}).`} />;

  const cells = buildSessionCells({
    mic,
    start,
    end,
    today,
    expected: expectedP.data,
    sessions: sessionsP.data,
    calendar: calendarData,
    barDates,
    firstBar,
  });
  return (
    <SessionStrip
      ticker={ticker}
      mic={mic}
      cells={cells}
      basis={expectedP.data.basis}
      confirmedDays={expectedP.data.confirmedDays}
      suggestionsNote="National-holiday suggestions are listed by the backend only from today onward, so a national holiday on a past day cannot be shown; past closures appear only when confirmed."
      asOf={asOf}
    />
  );
}

function CorrelationSection({ holdings }: { holdings: { ticker: string; name: string | null }[] }) {
  const tickers = useMemo(() => holdings.map((h) => h.ticker), [holdings]);
  const rows = useCorrelationRows(tickers);
  const risk = usePortfolioRisk(365);
  const names = useMemo(() => Object.fromEntries(holdings.map((h) => [h.ticker, h.name ?? h.ticker])), [holdings]);
  const riskData = lastGood(risk.state);
  return (
    <ResourceGate state={rows.state} label="correlations">
      {(load) => <CorrelationPanel grid={assembleCorrelation(load.rows)} names={names} asOf={load.asOf} source={load.source} failed={load.failed} risk={riskData} />}
    </ResourceGate>
  );
}

function CoverageSection({ onSelect }: { onSelect: (ticker: string) => void }) {
  const prices = usePortfolioHoldingsPrices(30);
  return <ResourceGate state={prices.state} label="portfolio prices">{(data) => <PortfolioCoveragePanel data={data} onSelect={onSelect} />}</ResourceGate>;
}
