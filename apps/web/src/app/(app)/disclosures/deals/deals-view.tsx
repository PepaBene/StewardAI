"use client";

import { useEffect, useMemo, useState } from "react";

import { ConsiderationBridge, DealDefs, DealFacts, DealFilters, DealTable, DealTimeline, EncodingLegend, ImpactTable, ImpactWaterfalls, PortfolioDealsPanel, TransitionTable, VersionTable } from "@/components/disclosure/deals";
import { Panel, SectionNav } from "@/components/oversight/panel";
import { PageHeader } from "@/components/page-header";
import { Field } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { filterDeals, TYPE_LABEL, type DealFilter, type HoldingDeals, type PortfolioDeals } from "@/lib/disclosure/deals";
import { SAMPLE_DEAL_DAY, SAMPLE_DEAL_NAMES, SAMPLE_DEAL_TICKER, sampleDeals, samplePortfolioDeals } from "@/lib/disclosure/fixtures-deals";
import { dayOf, endOfDayMs, formatDay } from "@/lib/disclosure/model";

const SECTIONS = [
  { id: "deals", label: "Deals" },
  { id: "timeline", label: "Lifecycle" },
  { id: "terms", label: "Terms and versions" },
  { id: "bridge", label: "What is paid" },
  { id: "impact", label: "What it could change" },
  { id: "facts", label: "Parties, gaps and documents" },
  { id: "portfolio", label: "Across the portfolio" },
] as const;

const TICKER = /^[A-Za-z0-9._-]{1,32}$/;
const DAY_MS = 86_400_000;
const LOADING = <p className="text-sm text-text-muted">Loading…</p>;

/**
 * Public deals, financing and corporate actions for one holding, replayable by date. A deal's life is
 * drawn as dated state changes with when each became known; announced terms, management projections,
 * your own scenario and unknown terms are kept visibly apart. With no ticker entered the page shows a
 * labelled sample of invented companies.
 */
export function DealsPageView() {
  const [until] = useState(() => Date.now());
  const [cutoff, setCutoff] = useState(() => endOfDayMs(SAMPLE_DEAL_DAY));
  const [input, setInput] = useState("");
  const [offerInput, setOfferInput] = useState("");
  const [offer, setOffer] = useState<number | null>(null);
  const [ticker, setTicker] = useState<string | null>(null);
  const [error, setError] = useState<string | undefined>();
  const [offerError, setOfferError] = useState<string | undefined>();
  const [live, setLive] = useState<HoldingDeals | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioDeals | null>(null);
  const [filter, setFilter] = useState<DealFilter>({ type: "", state: "" });
  const [selected, setSelected] = useState<number | null>(null);

  const sample = ticker === null;

  useEffect(() => {
    if (sample) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await getBrowserApiClient().GET("/disclosures/portfolio/deals", { params: { query: { as_of: new Date(cutoff).toISOString() } } });
        if (!cancelled) setPortfolio(r.data ?? null);
      } catch {
        if (!cancelled) setPortfolio(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sample, cutoff]);

  useEffect(() => {
    if (!ticker) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const r = await getBrowserApiClient().GET("/disclosures/holdings/{ticker}/deals", {
          params: { path: { ticker }, query: { as_of: new Date(cutoff).toISOString(), ...(offer != null ? { offer_price: offer } : {}) } },
        });
        if (cancelled) return;
        if (r.data) {
          setFailure(null);
          setLive(r.data);
        } else setFailure(r.response.status === 401 || r.response.status === 403 ? "Sign in again (with MFA) and retry." : `The deals read failed (HTTP ${r.response.status}). Try again in a minute; if it keeps failing, check the backend log.`);
      } catch (e) {
        if (!cancelled) setFailure(`Backend unreachable: ${e instanceof Error ? e.message : String(e)}. Start the backend or check your connection, then press Show deals again.`);
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [ticker, cutoff, offer]);

  const view = useMemo(() => (sample ? sampleDeals(cutoff, offer) : live), [sample, cutoff, offer, live]);
  const portfolioView = useMemo(() => (sample ? samplePortfolioDeals(cutoff) : portfolio), [sample, cutoff, portfolio]);
  const shown = useMemo(() => (view ? filterDeals(view.deals, filter) : []), [view, filter]);
  const current = shown.find((d) => d.id === selected) ?? shown[0] ?? null;
  const currencies = useMemo(() => [...new Set((view?.deals ?? []).map((d) => d.currency).filter((c): c is string => c != null))].sort(), [view]);
  const types = useMemo(() => Object.keys(TYPE_LABEL).filter((t) => view?.deals.some((d) => d.deal_type === t)), [view]);
  const asOf = new Date(cutoff).toISOString();

  function apply(e: React.FormEvent) {
    e.preventDefault();
    const t = input.trim();
    const o = offerInput.trim();
    setLive(null);
    setSelected(null);
    if (o === "") {
      setOffer(null);
      setOfferError(undefined);
    } else if (Number.isFinite(Number(o)) && Number(o) > 0) {
      setOffer(Number(o));
      setOfferError(undefined);
    } else return setOfferError("Enter a positive price per share, or leave it empty.");
    if (t === "") {
      setError(undefined);
      setTicker(null);
      setCutoff(endOfDayMs(SAMPLE_DEAL_DAY));
      return;
    }
    if (!TICKER.test(t)) return setError("Use letters, digits, dot, dash or underscore, up to 32 characters.");
    setError(undefined);
    setCutoff(until);
    setTicker(t.toUpperCase());
  }

  return (
    <div className="flex flex-col gap-5">
      <DealDefs />
      <PageHeader
        kicker="Research & Verification"
        title="Deals and financing"
        description="Acquisitions, sales, spin-offs, offerings, debt and buybacks a holding has made public: what stage each is at, what the terms said and when that was known, and what the stated terms would change. Terms that are not stated stay unknown."
        aside={<span>{ticker ?? SAMPLE_DEAL_TICKER} · as known on {formatDay(asOf)}</span>}
      />

      {sample ? (
        <p role="note" className="border border-dashed border-border bg-surface-muted px-3 py-2 text-sm">
          <strong>Sample data.</strong> This shows invented companies (“Acme Corp”, “Borealis Systems” and others) to demonstrate the screen. Enter a ticker you hold to read real filings. Nothing here is real.
        </p>
      ) : null}

      <form onSubmit={apply} noValidate className="flex flex-wrap items-end gap-3">
        <Field label="Ticker" hint="Leave empty for the sample." error={error}>
          <Input value={input} onChange={(e) => setInput(e.target.value)} className="w-40 uppercase" autoCapitalize="characters" spellCheck={false} />
        </Field>
        <Field label="Try an offer price" hint="Optional: your own scenario, per share." error={offerError}>
          <Input value={offerInput} onChange={(e) => setOfferInput(e.target.value)} inputMode="decimal" className="num w-32" spellCheck={false} />
        </Field>
        <Button type="submit" size="sm">Show deals</Button>
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
            <li key={n}>{n}{view.deals.length === 0 && view.total_known === 0 ? " Deals appear once the filings have been ingested; try an earlier date or run the filing ingest first." : ""}</li>
          ))}
        </ul>
      ) : null}

      <EncodingLegend />
      <SectionNav sections={SECTIONS} />

      <Panel id="deals" title="Deals" description="Every deal known at this date. Choose one to read its timeline and terms.">
        {view ? (
          <div className="space-y-3">
            <DealFilters value={filter} onChange={(f) => { setFilter(f); setSelected(null); }} types={types} currencies={currencies} />
            {shown.length === 0 && view.deals.length > 0 ? <p className="text-sm text-text-muted">No deal matches these filters. Clear the filters to see all {view.deals.length}.</p> : null}
            <DealTable deals={shown} selected={current?.id ?? null} onSelect={setSelected} asOf={asOf} />
          </div>
        ) : LOADING}
      </Panel>

      <Panel id="timeline" title="Lifecycle" description={current ? `${current.title}. Squares show when each stage took effect; the small tick shows when it became known.` : "Choose a deal."}>
        {current ? <DealTimeline key={current.id} deal={current} /> : view ? <p className="text-sm text-text-muted">No deal to draw at this date.</p> : LOADING}
      </Panel>

      <Panel id="terms" title="Terms and versions" description="State changes with their evidence, and every version of the terms. An amendment adds a version; nothing is overwritten.">
        {current ? (
          <div className="space-y-5">
            <TransitionTable deal={current} />
            <VersionTable deal={current} />
          </div>
        ) : view ? <p className="text-sm text-text-muted">No deal selected.</p> : LOADING}
      </Panel>

      <Panel id="bridge" title="What is paid" description="Cash, stock, debt and earnout as the documents state them. A part that is not stated is left open, never counted as zero.">
        {current ? <ConsiderationBridge deal={current} /> : view ? <p className="text-sm text-text-muted">No deal selected.</p> : LOADING}
      </Panel>

      <Panel id="impact" title="What it could change" description="Premium over a correctly dated unaffected price, dilution from a stated exchange ratio, new debt against enterprise value, and more. Each is worked out only from stated inputs.">
        {current ? (
          <div className="space-y-5">
            <ImpactWaterfalls deal={current} />
            <ImpactTable impacts={current.impacts} />
          </div>
        ) : view ? <p className="text-sm text-text-muted">No deal selected.</p> : LOADING}
      </Panel>

      <Panel id="facts" title="Parties, gaps and documents" description="Who is involved, which required terms are missing, the three closing dates, and the filings behind the deal.">
        {current ? <DealFacts deal={current} /> : view ? <p className="text-sm text-text-muted">No deal selected.</p> : LOADING}
      </Panel>

      <Panel id="portfolio" title="Across the portfolio" description="How much of the portfolio sits in holdings with an active deal. Holdings with none known are listed as unknown.">
        {portfolioView ? <PortfolioDealsPanel data={portfolioView} names={sample ? SAMPLE_DEAL_NAMES : undefined} /> : <p className="text-sm text-text-muted">Loading the portfolio…</p>}
      </Panel>
    </div>
  );
}
