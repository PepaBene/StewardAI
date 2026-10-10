"use client";

import { useMemo, useState } from "react";

import { BasisWarnings, CoverageList, FiveLanes, GateNote, PeriodRanges, RevisionSlopes, SegmentMultiples, SurpriseBridge } from "@/components/disclosure/estimates";
import { PairReviewPanel } from "@/components/disclosure/pair-reviews";
import { ReleaseNotice } from "@/components/disclosure/release-notice";
import { outcomeOf } from "@/lib/disclosure/keyed";
import { useKeyedLoad } from "@/lib/disclosure/use-keyed-load";
import { Panel, SectionNav } from "@/components/oversight/panel";
import { PageHeader } from "@/components/page-header";
import { Field, SELECT_CLASS } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { useUrlTicker } from "@/lib/disclosure/url-ticker";
import { allLines, choices, defaultChoice, type EstimatesView } from "@/lib/disclosure/estimates";
import { SAMPLE_EST_DAY, SAMPLE_EST_TICKER, sampleEstimates } from "@/lib/disclosure/fixtures-estimates";
import { dayOf, endOfDayMs, formatDay } from "@/lib/disclosure/model";

const SECTIONS = [
  { id: "lanes", label: "Five lanes" },
  { id: "ranges", label: "Range by period" },
  { id: "revisions", label: "Revisions" },
  { id: "surprise", label: "Earnings surprise" },
  { id: "segments", label: "Segments" },
  { id: "warnings", label: "Basis and panel warnings" },
  { id: "review", label: "Review pairs" },
  { id: "coverage", label: "Coverage" },
] as const;

// A ticker with no segment made only of dots: "." and ".." would normalise the request path to another route.
const TICKER = /^(?!\.+$)[A-Za-z0-9._-]{1,32}$/;
const DAY_MS = 86_400_000;

/**
 * Published consensus, named analyst estimates, company guidance, event-calendar estimates and your
 * own scenarios for one holding, replayable by date. The five are separate lanes and never averaged.
 * With no ticker entered the page shows a labelled sample.
 */
export function EstimatesPageView() {
  const [until] = useState(() => Date.now());
  const fromUrl = useUrlTicker();
  const [cutoff, setCutoff] = useState(() => (fromUrl ? until : endOfDayMs(SAMPLE_EST_DAY)));
  const [input, setInput] = useState(fromUrl ?? "");
  const [ticker, setTicker] = useState<string | null>(fromUrl);
  const [error, setError] = useState<string | undefined>();
  const [pick, setPick] = useState<string>("");
  const [segPick, setSegPick] = useState(0);
  // "Show" bumps this so the same inputs fetch again
  const [nonce, setNonce] = useState(0);

  const sample = ticker === null;

  // keyed by the inputs that produced it: a failed refetch never shows the old ticker or date under new inputs
  const liveState = useKeyedLoad<EstimatesView>(ticker ? `${ticker}:${cutoff}` : null, "The estimates read", async () => {
    const r = await getBrowserApiClient().GET("/disclosures/holdings/{ticker}/estimates", { params: { path: { ticker: ticker as string }, query: { as_of: new Date(cutoff).toISOString() } } });
    return outcomeOf("The estimates read", r);
  }, nonce, 250);
  const live = liveState?.state === "ready" ? liveState.data : null;
  const failure = liveState?.state === "error" ? liveState.message : null;

  const loading = <p className="text-sm text-text-muted">{failure ? "Not loaded." : "Loading…"}</p>;
  const view = useMemo(() => (sample ? sampleEstimates(cutoff) : live), [sample, cutoff, live]);
  const lines = useMemo(() => (view ? allLines(view) : []), [view]);
  const options = useMemo(() => choices(lines), [lines]);
  const segIndex = view && segPick < view.segment_bridges.length ? segPick : 0;
  const choice = options.find((o) => o.key === pick) ?? defaultChoice(options, lines);

  function apply(e: React.FormEvent) {
    e.preventDefault();
    const t = input.trim();
    setPick("");
    setSegPick(0);
    if (t === "") {
      setError(undefined);
      setTicker(null);
      setCutoff(endOfDayMs(SAMPLE_EST_DAY));
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
        title="Published estimates"
        description="What a company and the analysts it publishes said to expect, how that moved, and how the result compared. Five kinds of number, kept in five lanes."
        aside={<span>{ticker ?? SAMPLE_EST_TICKER} · as known on {formatDay(new Date(cutoff).toISOString())}</span>}
      />

      <ReleaseNotice page="estimates" />

      {sample ? (
        <p role="note" className="border border-dashed border-border bg-surface-muted px-3 py-2 text-sm">
          <strong>Sample data.</strong> This shows an invented company (“Acme Instruments”) to demonstrate the screen. Enter a ticker you hold to read real consensus files. Nothing here is real.
        </p>
      ) : null}
      <p role="note" className="text-sm text-text-muted">
        A consensus file published by one company is not the opinion of all analysts, and a company’s own target is not an analyst’s estimate. Where a number is missing it says so; it is never zero.
      </p>

      <form onSubmit={apply} noValidate className="flex flex-wrap items-end gap-3">
        <Field label="Ticker" hint="Leave empty for the sample." error={error}>
          <Input value={input} onChange={(e) => setInput(e.target.value)} className="w-40 uppercase" autoCapitalize="characters" spellCheck={false} />
        </Field>
        <Button type="submit" size="sm">Show estimates</Button>
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
        {options.length > 1 ? (
          <label className="flex items-center gap-2 text-sm">
            <span className="font-medium">Metric and period</span>
            <select className={SELECT_CLASS} value={choice?.key ?? ""} onChange={(e) => setPick(e.target.value)}>
              {options.map((o) => (
                <option key={o.key} value={o.key}>{o.label}</option>
              ))}
            </select>
          </label>
        ) : null}
      </form>

      {failure ? <p role="alert" className="border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">{failure}</p> : null}
      {view?.notes.length ? (
        <ul role="note" className="space-y-0.5 border border-dashed border-border bg-surface-muted px-3 py-2 text-sm">
          {view.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : null}
      {view ? <GateNote gate={view.revision_gate} /> : null}

      <SectionNav sections={SECTIONS} />

      <Panel id="lanes" title="Five lanes" description="One metric and fiscal period. Each lane has its own mark and is never overlaid on, or averaged with, another.">
        {view ? choice ? <FiveLanes view={view} choice={choice} /> : <p className="text-sm text-text-muted">No estimate of any kind is known for this holding at this date.</p> : loading}
      </Panel>

      <Panel id="ranges" title="Range by period" description="Published consensus for each fiscal period: low to high, mean and median, with the size of the panel.">
        {view && choice ? <PeriodRanges lines={view.lanes.published_analyst_consensus} metric={choice.metric} /> : view ? <p className="text-sm text-text-muted">No published consensus at this date.</p> : loading}
      </Panel>

      <Panel id="revisions" title="Revisions" description="How an estimate moved between files. Only snapshots measured the same way are compared; otherwise the chain shows a break and the reason.">
        {view ? <RevisionSlopes pairs={view.pairs} lines={lines} /> : loading}
      </Panel>

      <Panel id="surprise" title="Earnings surprise" description="The estimate known before the release, the reported actual, and the formula. A file published later is never used.">
        {view ? <SurpriseBridge events={view.events} lines={lines} /> : loading}
      </Panel>

      <Panel id="segments" title="Segments" description="Reported segment against the segment estimate the company published. A segment with no estimate is left blank.">
        {view && view.segment_bridges.length > 1 ? (
          <label className="mb-3 flex items-center gap-2 text-sm">
            <span className="font-medium">Segment period</span>
            <select className={SELECT_CLASS} value={segIndex} onChange={(e) => setSegPick(Number(e.target.value))}>
              {view.segment_bridges.map((b, i) => (
                <option key={`${b.metric}|${b.period_end}`} value={i}>{b.metric}, {b.fiscal_period ?? b.period_end}</option>
              ))}
            </select>
          </label>
        ) : null}
        {view ? <SegmentMultiples bridge={view.segment_bridges[segIndex]} /> : loading}
      </Panel>

      <Panel id="warnings" title="Basis and panel warnings" description="Adjusted definitions, unstated panel sizes and corrected files for the lines shown.">
        {view ? <BasisWarnings lines={lines} /> : loading}
      </Panel>

      <Panel id="review" title="Review pairs" description="Say whether each comparison was handled correctly. Revision alerts and revision-based screens stay off until enough pairs are labelled correct.">
        {ticker ? <PairReviewPanel key={ticker} ticker={ticker} onChanged={() => setNonce((n) => n + 1)} /> : <p className="text-sm text-text-muted">Enter a ticker you hold to review its real consensus pairs. The sample company has none to label.</p>}
      </Panel>

      <Panel id="coverage" title="Coverage" description="What is not covered, and where the files come from.">
        {view ? <CoverageList gaps={view.coverage} sources={view.sources} /> : loading}
      </Panel>
    </div>
  );
}
