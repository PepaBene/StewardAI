"use client";

import { RefreshCwIcon } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ContextCards } from "@/components/macro/context-cards";
import { ExposureEditor } from "@/components/macro/exposure-editor";
import { HoldingMacroPanel } from "@/components/macro/holding-macro-panel";
import { PortfolioExposureMap } from "@/components/macro/portfolio-exposure-map";
import { ResourceBoundary } from "@/components/macro/resource-boundary";
import { TradePanel } from "@/components/macro/trade-panel";
import { PageHeader } from "@/components/page-header";
import { StatePlaceholder } from "@/components/data-state";
import { Blueprint } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useApiResource, type ApiResult } from "@/lib/hooks/use-api-resource";
import {
  fetchExposureMappings,
  fetchHoldings,
  fetchMacroContext,
  fetchPortfolioCountryContext,
  fetchPortfolioMacroCoverage,
  runMacroCollect,
} from "@/lib/macro/api";
import { mappedTargets } from "@/lib/macro/logic";
import type { HoldingMacroContext } from "@/lib/macro/types";

const NO_TICKER: ApiResult<HoldingMacroContext> = { error: { detail: "Choose a holding." }, response: new Response(null, { status: 404 }) };
const todayIso = () => new Date().toISOString().slice(0, 10);

/**
 * Macro, financial cycle and trade (free-data plan, Phase 2), read live from the backend: the
 * portfolio's country exposure as a choropleth overview with a ranked list and table, the
 * country indicators and exposure-weighted changes of one holding (as of a chosen date), context
 * cards for what it is mapped to, trade for a reporter and product, and the owner-editable
 * exposure mapping that ties all of it together.
 */
export function MacroView() {
  const holdings = useApiResource(fetchHoldings);
  const portfolio = useApiResource(fetchPortfolioCountryContext);
  const coverage = useApiResource(fetchPortfolioMacroCoverage);
  const exposures = useApiResource(fetchExposureMappings);

  const [picked, setPicked] = useState<string | null>(null);
  const [asOfDate, setAsOfDate] = useState("");
  const holdingList = holdings.state.kind === "ok" ? holdings.state.data.holdings : holdings.state.kind === "error" ? (holdings.state.previous?.holdings ?? []) : [];
  const ticker = picked && holdingList.some((h) => h.ticker === picked) ? picked : (holdingList[0]?.ticker ?? null);
  const asOfIso = asOfDate ? `${asOfDate}T23:59:59Z` : null;

  const contextFetcher = useCallback((): Promise<ApiResult<HoldingMacroContext>> => (ticker ? fetchMacroContext(ticker, asOfIso) : Promise.resolve(NO_TICKER)), [ticker, asOfIso]);
  const context = useApiResource(contextFetcher);

  const [job, setJob] = useState<{ tone: "info" | "warning"; text: string } | null>(null);
  const [starting, setStarting] = useState(false);

  const contextData = context.state.kind === "ok" ? context.state.data : context.state.kind === "error" ? context.state.previous : null;
  const targets = useMemo(() => (contextData ? mappedTargets(contextData.effects) : []), [contextData]);
  const issuers = useMemo(() => new Map((contextData?.effects ?? []).map((e) => [e.issuerId, e.ticker] as const)), [contextData]);
  const defaultIssuerId = contextData?.effects[0]?.issuerId ?? null;
  const initialTrade = useMemo(
    () => ({ reporter: targets.find((t) => t.kind === "geography")?.key ?? "", hsCode: targets.find((t) => t.kind === "hs_product")?.key ?? "" }),
    [targets],
  );

  const reloadAll = () => {
    exposures.reload();
    portfolio.reload();
    coverage.reload();
    context.reload();
  };

  async function collect() {
    setStarting(true);
    setJob(null);
    try {
      const { data, response } = await runMacroCollect();
      if (response.ok && data) setJob({ tone: "info", text: `Macro collection queued as job ${data.id} (${data.status}). New series appear here after a sync or reload.` });
      else setJob({ tone: "warning", text: `The API did not queue the collection (HTTP ${response.status}).` });
    } catch (err) {
      setJob({ tone: "warning", text: `Could not reach the API: ${err instanceof Error ? err.message : String(err)}` });
    } finally {
      setStarting(false);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Research & Verification"
        title="Macro and trade"
        description="Country and sector context with release dates and known-since dates, tied to a holding only through a mapped, evidence-labelled exposure. Every figure is read live; missing data is shown as missing."
        aside={
          <Button type="button" variant="outline" size="sm" onClick={() => void collect()} disabled={starting}>
            <RefreshCwIcon aria-hidden="true" className={starting ? "animate-spin" : undefined} />
            {starting ? "Queuing…" : "Collect macro data"}
          </Button>
        }
      />
      {job ? <AlertMessage tone={job.tone} title={job.tone === "info" ? "Collection queued" : "Collection not started"}>{job.text}</AlertMessage> : null}

      <ResourceBoundary
        state={portfolio.state}
        what="portfolio country exposure"
        notFound={<StatePlaceholder state="not_covered" reason="No portfolio snapshot or country mapping is available yet, so there is no country exposure to map." />}
      >
        {(ctx) => <PortfolioExposureMap ctx={ctx} coverage={coverage.state.kind === "ok" ? coverage.state.data : coverage.state.kind === "error" ? coverage.state.previous : null} />}
      </ResourceBoundary>
      {coverage.state.kind === "error" && coverage.state.previous === null && coverage.state.status !== 404 ? (
        <AlertMessage title="Series coverage unavailable">
          {coverage.state.title}
          {coverage.state.detail ? `: ${coverage.state.detail}` : ""}. The map above is unaffected; the per-country series counts are missing.
        </AlertMessage>
      ) : null}

      <Blueprint as="section" aria-labelledby="holding-pick-heading" className="flex flex-col gap-3 px-5 py-4">
        <h2 id="holding-pick-heading" className="text-[22px] leading-tight font-semibold">One holding</h2>
        <ResourceBoundary state={holdings.state} what="holdings">
          {() =>
            holdingList.length === 0 ? (
              <StatePlaceholder state="not_covered" reason="There are no open holdings to read macro context for." />
            ) : (
              <div className="flex flex-wrap items-end gap-4">
                <div className="flex min-w-0 flex-col gap-1">
                  <Label htmlFor="macro-holding">Holding</Label>
                  <select
                    id="macro-holding"
                    value={ticker ?? ""}
                    onChange={(e) => setPicked(e.target.value)}
                    className="h-9 w-64 max-w-full min-w-0 border border-border bg-surface-raised px-2 text-sm text-text outline-none focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30"
                  >
                    {holdingList.map((h) => (
                      <option key={h.ticker} value={h.ticker}>
                        {h.ticker}
                        {h.name ? ` · ${h.name}` : ""}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="flex min-w-0 flex-col gap-1">
                  <Label htmlFor="macro-asof">Known as of</Label>
                  <Input id="macro-asof" type="date" value={asOfDate} max={todayIso()} onChange={(e) => setAsOfDate(e.target.value)} className="w-44" />
                </div>
                {asOfDate ? (
                  <Button type="button" variant="ghost" size="sm" onClick={() => setAsOfDate("")}>
                    Use today
                  </Button>
                ) : null}
                <p className="max-w-prose text-xs text-text-muted">
                  A past date shows only what the app had learned by then (point in time): later releases and revisions are hidden.
                </p>
              </div>
            )
          }
        </ResourceBoundary>
      </Blueprint>

      {ticker ? (
        <ResourceBoundary state={context.state} what={`macro context for ${ticker}`}>
          {(ctx) => <HoldingMacroPanel ctx={ctx} ticker={ticker} />}
        </ResourceBoundary>
      ) : null}

      {ticker ? <ContextCards targets={targets} ticker={ticker} /> : null}

      <TradePanel key={`${initialTrade.reporter}|${initialTrade.hsCode}`} initial={initialTrade} />

      <ResourceBoundary state={exposures.state} what="exposure mappings">
        {(list) => <ExposureEditor key={`${ticker}-${defaultIssuerId}`} mappings={list} issuers={issuers} defaultIssuerId={defaultIssuerId} onChanged={reloadAll} />}
      </ResourceBoundary>
    </div>
  );
}
