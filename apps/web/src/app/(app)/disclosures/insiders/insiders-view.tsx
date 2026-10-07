"use client";

import { useEffect, useMemo, useState } from "react";

import { BundleList, EventStrip, InsiderTable, NetPanel } from "@/components/disclosure/insiders";
import { Panel, SectionNav } from "@/components/oversight/panel";
import { PageHeader } from "@/components/page-header";
import { Field } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { SAMPLE_INSIDER_DAY, SAMPLE_INSIDER_TICKER, sampleInsiders } from "@/lib/disclosure/fixtures-insiders";
import type { InsiderActivity } from "@/lib/disclosure/insiders";
import { dayOf, endOfDayMs, formatDay } from "@/lib/disclosure/model";

const SECTIONS = [
  { id: "strip", label: "Filings over time" },
  { id: "net", label: "Net buying and selling" },
  { id: "detail", label: "Every transaction" },
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
  const [until] = useState(() => Date.now());
  const [cutoff, setCutoff] = useState(() => endOfDayMs(SAMPLE_INSIDER_DAY));
  const [input, setInput] = useState("");
  const [ticker, setTicker] = useState<string | null>(null);
  const [error, setError] = useState<string | undefined>();
  const [live, setLive] = useState<InsiderActivity | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const sample = ticker === null;

  useEffect(() => {
    if (!ticker) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const r = await getBrowserApiClient().GET("/disclosures/holdings/{ticker}/insiders", { params: { path: { ticker }, query: { as_of: new Date(cutoff).toISOString() } } });
        if (cancelled) return;
        if (r.data) {
          setFailure(null);
          setLive(r.data);
        } else setFailure(r.response.status === 401 || r.response.status === 403 ? "Sign in again (with MFA) and retry." : `The insider read failed (HTTP ${r.response.status}).`);
      } catch (e) {
        if (!cancelled) setFailure(`Backend unreachable: ${e instanceof Error ? e.message : String(e)}`);
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [ticker, cutoff, nonce]);

  const view = useMemo(() => (sample ? sampleInsiders(cutoff) : live), [sample, cutoff, live]);

  function apply(e: React.FormEvent) {
    e.preventDefault();
    const t = input.trim();
    setLive(null);
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
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Research & Verification"
        title="Insider activity"
        description="What a company's officers, directors and large holders filed, as it was known on a chosen date. Only open-market purchases and sales count toward the net."
        aside={<span>{ticker ?? SAMPLE_INSIDER_TICKER} · as known on {formatDay(new Date(cutoff).toISOString())}</span>}
      />

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
        {view ? <EventStrip txs={view.transactions} cutoffMs={cutoff} /> : <p className="text-sm text-text-muted">Loading insider filings…</p>}
      </Panel>

      <Panel id="net" title="Net buying and selling" description="Discretionary open-market trades only, by role. Zero is the centre line.">
        {view ? <NetPanel view={view} cutoffMs={cutoff} /> : <p className="text-sm text-text-muted">Loading…</p>}
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
          <p className="text-sm text-text-muted">Loading…</p>
        )}
      </Panel>

      <Panel id="bundles" title="Exercise and sale on one day" description="An option exercise with a same-day sale is one event, so the sale is not read as a choice to sell.">
        {view ? <BundleList view={view} /> : <p className="text-sm text-text-muted">Loading…</p>}
      </Panel>
    </div>
  );
}
