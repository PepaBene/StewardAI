"use client";

import { useMemo, useState } from "react";

import { ReleaseNotice } from "@/components/disclosure/release-notice";
import { outcomeOf } from "@/lib/disclosure/keyed";
import { useKeyedLoad } from "@/lib/disclosure/use-keyed-load";
import { BundleList, ContextPanel, EventStrip, InsiderTable, NetPanel, PdmrPanel } from "@/components/disclosure/insiders";
import { Panel, SectionNav } from "@/components/oversight/panel";
import { PageHeader } from "@/components/page-header";
import { Field } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { useUrlTicker } from "@/lib/disclosure/url-ticker";
import { SAMPLE_INSIDER_DAY, SAMPLE_INSIDER_TICKER, sampleInsiders } from "@/lib/disclosure/fixtures-insiders";
import type { InsiderActivity, PdmrActivity } from "@/lib/disclosure/insiders";
import { dayOf, endOfDayMs, formatDay } from "@/lib/disclosure/model";

const SECTIONS = [
  { id: "strip", label: "Filings over time" },
  { id: "net", label: "Net buying and selling" },
  { id: "detail", label: "Every transaction" },
  { id: "context", label: "Around the trades" },
  { id: "bundles", label: "Exercise and sale on one day" },
] as const;

const TICKER = /^[A-Za-z0-9._-]{1,32}$/;
const DAY_MS = 86_400_000;

/**
 * Insider filings for one holding, replayable by date. The strip shows every filed line by class; the
 * net chart counts only discretionary open-market trades; the table keeps every line with its filing,
 * holdings after the trade and caveats. With no ticker entered the page shows a labelled sample.
 */
export function InsidersPageView() {
  const fromUrl = useUrlTicker();
  const [until] = useState(() => Date.now());
  const [cutoff, setCutoff] = useState(() => (fromUrl ? until : endOfDayMs(SAMPLE_INSIDER_DAY)));
  const [input, setInput] = useState(fromUrl ?? "");
  const [ticker, setTicker] = useState<string | null>(fromUrl);
  const [error, setError] = useState<string | undefined>();
  // bumped by "Show" and after an override, so the same inputs fetch again
  const [nonce, setNonce] = useState(0);

  const sample = ticker === null;

  // results are keyed by the inputs that produced them: a failed refetch never leaves old data under new inputs
  const key = ticker ? `${ticker}:${cutoff}` : null;
  const pdmrState = useKeyedLoad<PdmrActivity>(key, "UK notices", async () => {
    const r = await getBrowserApiClient().GET("/disclosures/holdings/{ticker}/pdmr", { params: { path: { ticker: ticker as string }, query: { as_of: new Date(cutoff).toISOString() } } });
    return outcomeOf("UK notices", r);
  }, nonce);
  const liveState = useKeyedLoad<InsiderActivity>(key, "The insider read", async () => {
    const r = await getBrowserApiClient().GET("/disclosures/holdings/{ticker}/insiders", { params: { path: { ticker: ticker as string }, query: { as_of: new Date(cutoff).toISOString() } } });
    return outcomeOf("The insider read", r);
  }, nonce, 250);
  const live = liveState?.state === "ready" ? liveState.data : null;
  const failure = liveState?.state === "error" ? liveState.message : null;

  const view = useMemo(() => (sample ? sampleInsiders(cutoff) : live), [sample, cutoff, live]);
  const loadingText = failure ? "Not loaded." : "Loading…";

  function apply(e: React.FormEvent) {
    e.preventDefault();
    const t = input.trim();
    if (t === "") {
      setError(undefined);
      setTicker(null);
      setCutoff(endOfDayMs(SAMPLE_INSIDER_DAY));
      return;
    }
    if (!TICKER.test(t)) return setError("Use letters, digits, dot, dash or underscore, up to 32 characters.");
    setError(undefined);
    setCutoff(until);
    setTicker(t.toUpperCase());
    setNonce((n) => n + 1); // same inputs still refetch
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Research & Verification"
        title="Insider activity"
        description="What a company's officers, directors and large holders filed, as it was known on a chosen date. Only open-market purchases and sales count toward the net."
        aside={<span>{ticker ?? SAMPLE_INSIDER_TICKER} · as known on {formatDay(new Date(cutoff).toISOString())}</span>}
      />

      <ReleaseNotice page="insiders" />

      {sample ? (
        <p role="note" className="border border-dashed border-border bg-surface-muted px-3 py-2 text-sm">
          <strong>Sample data.</strong> This shows an invented company, “Acme Corp”, to demonstrate the screen. Enter a ticker you hold to read real filings. Nothing here is real.
        </p>
      ) : null}

      <form onSubmit={apply} noValidate className="flex flex-wrap items-end gap-3">
        <Field label="Ticker" hint="Leave empty for the sample." error={error}>
          <Input value={input} onChange={(e) => setInput(e.target.value)} className="w-40 uppercase" autoCapitalize="characters" spellCheck={false} />
        </Field>
        <Button type="submit" size="sm">Show insider activity</Button>
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

      <SectionNav sections={SECTIONS} />

      <Panel id="strip" title="Filings over time" description="Each filed line, by class. The stem runs from the trade to the day it was filed.">
        {view ? <EventStrip txs={view.transactions} cutoffMs={cutoff} /> : <p className="text-sm text-text-muted">{failure ? "Not loaded." : "Loading insider filings…"}</p>}
      </Panel>

      <Panel id="net" title="Net buying and selling" description="Discretionary open-market trades only, by role. Zero is the centre line.">
        {view ? <NetPanel view={view} cutoffMs={cutoff} /> : <p className="text-sm text-text-muted">{loadingText}</p>}
      </Panel>

      <Panel id="detail" title="Every transaction" description="The full list, including lines that are not in the net, each with the filing it came from.">
        {view ? (
          <>
            {view.notes.length ? (
              <ul role="note" className="mb-3 space-y-0.5 border border-dashed border-border bg-surface-muted px-3 py-2 text-sm">
                {view.notes.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            ) : null}
            <InsiderTable txs={view.transactions} editable={!sample} onChanged={() => setNonce((n) => n + 1)} />
          </>
        ) : (
          <p className="text-sm text-text-muted">{loadingText}</p>
        )}
      </Panel>

      <Panel id="context" title="Around the trades" description="The share count at the date and confirmed company events from the year before.">
        {view ? <ContextPanel view={view} /> : <p className="text-sm text-text-muted">{loadingText}</p>}
      </Panel>

      {sample ? null : (
        <Panel id="uk" title="UK manager dealings" description="Notices UK companies must publish when a director or senior manager trades. A separate regime from Form 4, never added to it.">
          <PdmrPanel state={pdmrState} />
        </Panel>
      )}

      <Panel id="bundles" title="Exercise and sale on one day" description="An option exercise with a same-day sale is one event, so the sale is not read as a choice to sell.">
        {view ? <BundleList view={view} /> : <p className="text-sm text-text-muted">{loadingText}</p>}
      </Panel>
    </div>
  );
}
