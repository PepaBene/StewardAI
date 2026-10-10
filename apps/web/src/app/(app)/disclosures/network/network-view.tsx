"use client";

import { useMemo, useState } from "react";

import { ConcentrationBars, ConflictList, DependencyGraph, LivePortfolio, OwnerWeights, PortfolioExposure, ScenarioView, SuggestedEdges } from "@/components/disclosure/network";
import { ReleaseNotice } from "@/components/disclosure/release-notice";
import { outcomeOf } from "@/lib/disclosure/keyed";
import { useKeyedLoad } from "@/lib/disclosure/use-keyed-load";
import { Panel, SectionNav } from "@/components/oversight/panel";
import { PageHeader } from "@/components/page-header";
import { Field } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { useUrlTicker } from "@/lib/disclosure/url-ticker";
import type { DependencyView, PortfolioDependencies } from "@/lib/disclosure/dependencies";
import { SAMPLE_DEP_DAY, SAMPLE_DEP_TICKER, SAMPLE_NAMES, sampleDependencies, samplePortfolio } from "@/lib/disclosure/fixtures-dependencies";
import { dayOf, endOfDayMs, formatDay } from "@/lib/disclosure/model";

const SECTIONS = [
  { id: "graph", label: "Who it depends on" },
  { id: "concentration", label: "Concentration" },
  { id: "scenario", label: "If a customer shrinks" },
  { id: "review", label: "Waiting for review" },
  { id: "conflicts", label: "Where reporters disagree" },
  { id: "weights", label: "Your own estimates" },
  { id: "portfolio", label: "Across the portfolio" },
] as const;

const TICKER = /^[A-Za-z0-9._-]{1,32}$/;
const DAY_MS = 86_400_000;

/**
 * Customers, suppliers and revenue dependence for one holding, replayable by date. Confirmed claims
 * carry the numbers; suggestions are shown with their source passage and counted for nothing. With no
 * ticker entered the page shows a labelled sample.
 */
export function NetworkPageView() {
  const [until] = useState(() => Date.now());
  const fromUrl = useUrlTicker();
  const [cutoff, setCutoff] = useState(() => (fromUrl ? until : endOfDayMs(SAMPLE_DEP_DAY)));
  const [shock, setShock] = useState(0.2);
  const [input, setInput] = useState(fromUrl ?? "");
  const [ticker, setTicker] = useState<string | null>(fromUrl);
  const [error, setError] = useState<string | undefined>();
  // "Show" and every edit bump this so the same inputs fetch again
  const [nonce, setNonce] = useState(0);

  const sample = ticker === null;

  // keyed by the inputs that produced it: a failed refetch never shows old data under new inputs, and an HTTP error is an error
  const portfolioState = useKeyedLoad<PortfolioDependencies>(sample ? null : `${cutoff}`, "The portfolio dependencies", async () => {
    const r = await getBrowserApiClient().GET("/disclosures/portfolio/dependencies", { params: { query: { as_of: new Date(cutoff).toISOString() } } });
    return outcomeOf("The portfolio dependencies", r);
  }, nonce);
  const liveState = useKeyedLoad<DependencyView>(ticker ? `${ticker}:${cutoff}:${shock}` : null, "The dependency read", async () => {
    const r = await getBrowserApiClient().GET("/disclosures/holdings/{ticker}/dependencies", { params: { path: { ticker: ticker as string }, query: { as_of: new Date(cutoff).toISOString(), shock } } });
    return outcomeOf("The dependency read", r);
  }, nonce, 250);
  const live = liveState?.state === "ready" ? liveState.data : null;
  const failure = liveState?.state === "error" ? liveState.message : null;
  const portfolio = portfolioState?.state === "ready" ? portfolioState.data : null;
  const portfolioFailure = portfolioState?.state === "error" ? portfolioState.message : null;

  const view = useMemo(() => (sample ? sampleDependencies(cutoff, shock) : live), [sample, cutoff, shock, live]);
  const holdings = useMemo(() => (sample ? samplePortfolio(cutoff) : view ? [{ ticker: view.ticker, weight: 1, claims: view.claims }] : []), [sample, cutoff, view]);

  function apply(e: React.FormEvent) {
    e.preventDefault();
    const t = input.trim();
    if (t === "") {
      setError(undefined);
      setTicker(null);
      setCutoff(endOfDayMs(SAMPLE_DEP_DAY));
      return;
    }
    if (!TICKER.test(t)) return setError("Use letters, digits, dot, dash or underscore, up to 32 characters.");
    setError(undefined);
    setCutoff(until);
    setTicker(t.toUpperCase());
    setNonce((n) => n + 1); // the same inputs still refetch
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Research & Verification"
        title="Customers and suppliers"
        description="Who a company says it sells to and buys from, how much of its revenue that is, and what is still unknown. Only confirmed claims carry numbers."
        aside={<span>{ticker ?? SAMPLE_DEP_TICKER} · as known on {formatDay(new Date(cutoff).toISOString())}</span>}
      />

      <ReleaseNotice page="network" />

      {sample ? (
        <p role="note" className="border border-dashed border-border bg-surface-muted px-3 py-2 text-sm">
          <strong>Sample data.</strong> This shows invented companies (“Acme Corp” and its customers) to demonstrate the screen. Enter a ticker you hold to read real filings. Nothing here is real.
        </p>
      ) : null}

      <form onSubmit={apply} noValidate className="flex flex-wrap items-end gap-3">
        <Field label="Ticker" hint="Leave empty for the sample." error={error}>
          <Input value={input} onChange={(e) => setInput(e.target.value)} className="w-40 uppercase" autoCapitalize="characters" spellCheck={false} />
        </Field>
        <Button type="submit" size="sm">Show customers and suppliers</Button>
        <label className="flex items-center gap-2 text-sm">
          <span className="font-medium">Show what was known on</span>
          <input
            type="date"
            value={dayOf(cutoff)}
            max={dayOf(until)}
            onChange={(e) => {
              const t = endOfDayMs(e.target.value);
              if (Number.isFinite(t)) setCutoff(Math.min(t, until + DAY_MS));
            }}
            className="num h-9 border border-border bg-surface-raised px-2 text-sm outline-none focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30"
          />
        </label>
      </form>

      {failure ? <p role="alert" className="border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">{failure}</p> : null}
      {view?.notes.length ? (
        <ul role="note" className="space-y-0.5 border border-dashed border-border bg-surface-muted px-3 py-2 text-sm">
          {view.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : null}

      <SectionNav sections={SECTIONS} />

      <Panel id="graph" title="Who it depends on" description="The company in the middle, with an arrow for each way money flows. Line weight is the reported share.">
        {view ? <DependencyGraph ticker={view.ticker} claims={view.claims} /> : <p className="text-sm text-text-muted">{failure ? "Not loaded." : "Loading claims…"}</p>}
      </Panel>

      <Panel id="concentration" title="Concentration" description="Customers ranked by reported share. The open bar is what no confirmed claim accounts for.">
        {view ? <ConcentrationBars groups={view.concentration} /> : <p className="text-sm text-text-muted">{failure ? "Not loaded." : "Loading…"}</p>}
      </Panel>

      <Panel id="scenario" title="If a customer shrinks" description="How far a fall in one customer’s revenue could reach, over confirmed shares only and at most two steps.">
        {view ? <ScenarioView scenarios={view.scenarios} shock={shock} onShock={setShock} names={sample ? SAMPLE_NAMES : {}} /> : <p className="text-sm text-text-muted">{failure ? "Not loaded." : "Loading…"}</p>}
      </Panel>

      <Panel id="review" title="Waiting for review" description="Claims a machine found that nobody has confirmed. Each shows the passage it came from.">
        {view ? <SuggestedEdges ticker={view.ticker} claims={view.claims} editable={!sample} onChanged={() => setNonce((n) => n + 1)} /> : <p className="text-sm text-text-muted">{failure ? "Not loaded." : "Loading…"}</p>}
      </Panel>

      <Panel id="conflicts" title="Where reporters disagree" description="When the company and one of its counterparties tell different stories, both are kept.">
        {view ? <ConflictList conflicts={view.conflicts} claims={view.claims} inbound={view.inbound_claims} /> : <p className="text-sm text-text-muted">{failure ? "Not loaded." : "Loading…"}</p>}
      </Panel>

      <Panel id="weights" title="Your own estimates" description="Weights you assign where no filing gives one. Kept apart from reported figures.">
        {view ? <OwnerWeights ticker={view.ticker} weights={view.owner_weights} editable={!sample} onChanged={() => setNonce((n) => n + 1)} /> : <p className="text-sm text-text-muted">{failure ? "Not loaded." : "Loading…"}</p>}
      </Panel>

      <Panel id="portfolio" title="Across the portfolio" description="Where portfolio weight reaches a confirmed customer.">
        {sample ? (
          view ? <PortfolioExposure holdings={holdings} /> : null
        ) : portfolio ? (
          <LivePortfolio data={portfolio} />
        ) : (
          portfolioFailure ? <p role="alert" className="border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">{portfolioFailure}</p> : <p className="text-sm text-text-muted">Loading the portfolio…</p>
        )}
      </Panel>
    </div>
  );
}
