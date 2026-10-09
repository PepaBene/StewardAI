"use client";

import { useMemo, useState } from "react";

import { OwnershipLanes, QuarterNote, RankFlow, StakeChain, StakeTimeline } from "@/components/disclosure/ownership";
import { ReleaseNotice } from "@/components/disclosure/release-notice";
import { outcomeOf } from "@/lib/disclosure/keyed";
import { useKeyedLoad } from "@/lib/disclosure/use-keyed-load";
import { Panel, SectionNav } from "@/components/oversight/panel";
import { PageHeader } from "@/components/page-header";
import { Field } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { SAMPLE_STAKES, SAMPLE_TICKER, sampleOwnership } from "@/lib/disclosure/fixtures-ownership";
import { dayOf, endOfDayMs, formatDay } from "@/lib/disclosure/model";
import type { OwnershipView, StakeChainView } from "@/lib/disclosure/ownership";

const SECTIONS = [
  { id: "lanes", label: "Who reports owning shares" },
  { id: "quarter", label: "Which quarter" },
  { id: "movement", label: "Manager movement" },
  { id: "control", label: "Control stakes over time" },
] as const;

const TICKER = /^[A-Za-z0-9._-]{1,32}$/;
const DAY_MS = 86_400_000;

/**
 * Ownership as separate dated lanes. Each system (13F, 13D/G, fund holdings, national notices) has
 * its own bar and its own table, and nothing adds them together because they overlap. The date
 * control replays what was known then. With no ticker entered the page shows a labelled sample.
 */
export function OwnershipPageView() {
  const [until] = useState(() => Date.now());
  const [cutoff, setCutoff] = useState(until);
  const [input, setInput] = useState("");
  const [ticker, setTicker] = useState<string | null>(null);
  const [error, setError] = useState<string | undefined>();
  const [cikInput, setCikInput] = useState("");
  const [cikError, setCikError] = useState<string | undefined>();
  const [filerCik, setFilerCik] = useState<string | null>(null);
  // "Show" bumps this so the same inputs fetch again
  const [nonce, setNonce] = useState(0);

  const sample = ticker === null;
  // Results are keyed by the inputs that produced them, so a failed refetch never shows old data under new inputs.
  const chainKey = ticker && filerCik ? `${ticker}:${filerCik}:${cutoff}` : null;
  const chainState = useKeyedLoad<StakeChainView>(chainKey, "The filing history", async () => {
    const r = await getBrowserApiClient().GET("/disclosures/holdings/{ticker}/control-stakes/{filer_cik}/chain", { params: { path: { ticker: ticker as string, filer_cik: filerCik as string }, query: { as_of: new Date(cutoff).toISOString() } } });
    return outcomeOf("The filing history", r);
  });
  const chain = chainState?.state === "ready" ? chainState.data : null;
  const chainFailure = chainState?.state === "error" ? chainState.message : null;

  const liveKey = ticker ? `${ticker}:${cutoff}` : null;
  const liveState = useKeyedLoad<OwnershipView>(liveKey, "The ownership read", async () => {
    const r = await getBrowserApiClient().GET("/disclosures/holdings/{ticker}/ownership", { params: { path: { ticker: ticker as string }, query: { as_of: new Date(cutoff).toISOString() } } });
    return outcomeOf("The ownership read", r);
  }, nonce, 250);
  const live = liveState?.state === "ready" ? liveState.data : null;
  const failure = liveState?.state === "error" ? liveState.message : null;
  const loadingText = failure ? "Not loaded." : "Loading…";

  const view = useMemo(() => (sample ? sampleOwnership(cutoff) : live), [sample, cutoff, live]);
  const trend = view?.trend ?? null;

  function apply(e: React.FormEvent) {
    e.preventDefault();
    const t = input.trim();
    if (t === "") {
      setError(undefined);
      setTicker(null);
      return;
    }
    if (!TICKER.test(t)) return setError("Use letters, digits, dot, dash or underscore, up to 32 characters.");
    setError(undefined);
    setTicker(t.toUpperCase());
    setNonce((n) => n + 1); // the same inputs still refetch
  }

  const book = (b: Record<string, string>) => Object.fromEntries(Object.entries(b).map(([k, v]) => [k, Number(v)]));

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Research & Verification"
        title="Ownership"
        description="Who reports owning a company's shares, by source, with the date each figure was measured and filed. The sources are never added together."
        aside={<span>{ticker ?? SAMPLE_TICKER} · as known on {formatDay(new Date(cutoff).toISOString())}</span>}
      />

      <ReleaseNotice page="ownership" />

      {sample ? (
        <p role="note" className="border border-dashed border-border bg-surface-muted px-3 py-2 text-sm">
          <strong>Sample data.</strong> This shows an invented company, “Acme Corp”, to demonstrate the screen. Enter a ticker you hold to read real filings. Nothing here is real.
        </p>
      ) : null}

      <form onSubmit={apply} noValidate className="flex flex-wrap items-end gap-3">
        <Field label="Ticker" hint="Leave empty for the sample." error={error}>
          <Input value={input} onChange={(e) => setInput(e.target.value)} className="w-40 uppercase" autoCapitalize="characters" spellCheck={false} />
        </Field>
        <Button type="submit" size="sm">Show ownership</Button>
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

      {failure ? (
        <p role="alert" className="border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">{failure}</p>
      ) : null}

      <SectionNav sections={SECTIONS} />

      <Panel id="lanes" title="Who reports owning shares" description="Each bar is one source measured against the same share count. The hatched part is what that source does not account for; it is not assumed to be anyone's.">
        {view ? <OwnershipLanes view={view} /> : <p className="text-sm text-text-muted">{failure ? "Not loaded." : "Loading ownership…"}</p>}
      </Panel>

      <Panel id="quarter" title="Which quarter, and who is missing" description="The quarter the manager lane uses, and what each manager’s own report says about its size.">
        {view ? <QuarterNote view={view} /> : <p className="text-sm text-text-muted">{loadingText}</p>}
      </Panel>

      <Panel id="movement" title="Manager movement" description="The largest managers' reported holdings, quarter to quarter.">
        {trend ? (
          <RankFlow prev={book(trend.prev)} cur={book(trend.cur)} prevLabel={formatDay(trend.prev_period)} curLabel={formatDay(trend.cur_period)} />
        ) : (
          <p className="text-sm text-text-muted">{view ? "Fewer than two quarters of manager reports are known at this date, so there is no movement to show." : loadingText}</p>
        )}
      </Panel>

      <Panel id="control" title="Control stakes over time" description="A holder's 13D and 13G filings, each with the percent it reported.">
        {sample ? (
          <StakeTimeline events={SAMPLE_STAKES} cutoffMs={cutoff} />
        ) : (
          <div className="space-y-3">
            <form
              noValidate
              onSubmit={(e) => {
                e.preventDefault();
                const v = cikInput.trim();
                if (!/^[0-9]{1,10}$/.test(v)) return setCikError("A filer id is 1 to 10 digits.");
                setCikError(undefined);
                setFilerCik(v);
              }}
              className="flex flex-wrap items-end gap-3"
            >
              <Field label="Filer id (CIK)" hint="Shown on the filing. One filer’s filings are followed from first to latest." error={cikError}>
                <Input value={cikInput} onChange={(e) => setCikInput(e.target.value)} inputMode="numeric" className="num w-44" />
              </Field>
              <Button type="submit" size="sm">Show this filer’s filings</Button>
            </form>
            {chainFailure ? <p role="alert" className="border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">{chainFailure}</p> : null}
            {chain ? <StakeChain chain={chain} /> : filerCik ? <p className="text-sm text-text-muted">{chainFailure ? "Not loaded." : "Loading filings…"}</p> : null}
          </div>
        )}
      </Panel>
    </div>
  );
}
