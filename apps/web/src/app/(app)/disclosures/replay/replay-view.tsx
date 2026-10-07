"use client";

import { useState } from "react";

import { Panel } from "@/components/oversight/panel";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { EvidenceAnswerView, ReplayHoldingTable } from "@/components/disclosure/replay";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDay } from "@/lib/disclosure/model";
import { asOfFromDay, replayTotals, type EvidenceAnswer, type Replay } from "@/lib/disclosure/replay";

const failure = (status: number): string => (status === 401 || status === 403 ? "Sign in again (with MFA) and retry." : `The request failed (HTTP ${status}).`);

/**
 * Pick a past date and see only what was publicly known then, with later changes listed apart.
 * Holdings come from the last stored snapshot on or before the date. “Ask the evidence” answers
 * from verified stored facts as of that date and says what it could not find.
 */
export function ReplayPageView() {
  const [day, setDay] = useState("");
  const [replay, setReplay] = useState<Replay | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [ticker, setTicker] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<EvidenceAnswer | null>(null);
  const [answerError, setAnswerError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [reword, setReword] = useState(false);

  const asOf = asOfFromDay(day);

  async function run() {
    if (!asOf) return;
    setBusy(true);
    setError(null);
    try {
      const r = await getBrowserApiClient().GET("/disclosures/replay", { params: { query: { as_of: asOf } } });
      if (r.data) setReplay(r.data);
      else setError(failure(r.response.status));
    } catch (e) {
      setError(`Backend unreachable: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  async function ask() {
    setAsking(true);
    setAnswerError(null);
    try {
      const r = await getBrowserApiClient().POST("/disclosures/answer", { body: { ticker: ticker.trim(), question: question.trim(), as_of: asOf, phrase: reword } });
      if (r.data) setAnswer(r.data);
      else setAnswerError(failure(r.response.status));
    } catch (e) {
      setAnswerError(`Backend unreachable: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setAsking(false);
    }
  }

  const totals = replay ? replayTotals(replay) : null;
  const canAsk = ticker.trim().length > 0 && question.trim().length >= 3;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Research & Verification"
        title="Decision replay"
        description="Choose a date to see your holdings and only the filings, estimates, deals, owners and insider trades that were public by then. What appeared afterwards is shown separately."
      />

      <Panel id="date" title="Date" description="Everything published on the chosen day counts as known.">
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            void run();
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            Replay as of
            <input type="date" value={day} onChange={(e) => setDay(e.target.value)} max={new Date().toISOString().slice(0, 10)} className="num border border-border bg-surface-raised px-2 py-1.5 focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none" />
          </label>
          <Button type="submit" size="sm" disabled={!asOf || busy}>
            {busy ? "Reading…" : "Replay"}
          </Button>
        </form>
        {error ? (
          <p role="alert" className="mt-3 border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">{error}</p>
        ) : null}
      </Panel>

      {replay && totals ? (
        <Panel id="result" title={`Known on ${formatDay(replay.as_of)}`} description={`Read ${formatDay(replay.read_at)}. ${totals.known} items were known then; ${totals.since} appeared later.`}>
          {replay.notes.map((n) => (
            <p key={n} className="mb-2 border border-border bg-surface-muted px-3 py-2 text-sm">{n}</p>
          ))}
          {totals.unreadable > 0 ? (
            <p role="alert" className="mb-2 border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">
              {totals.unreadable} {totals.unreadable === 1 ? "dataset" : "datasets"} could not be read; they are marked below and are not shown as empty.
            </p>
          ) : null}
          {replay.holdings.length === 0 ? (
            <p className="text-sm text-text-muted">No holdings were recorded on or before this date.</p>
          ) : (
            <div className="space-y-6">
              {replay.holdings.map((h) => (
                <section key={h.ticker} aria-label={h.ticker}>
                  <h3 className="mb-1 text-sm font-semibold">
                    <span className="num">{h.ticker}</span>
                    <span className="num font-normal text-text-muted"> · {h.quantity} units · {h.value} {h.currency}</span>
                  </h3>
                  {h.issuer_id == null ? <p className="mb-1 text-xs text-text-muted">Not linked to an issuer: filings and estimates cannot be read.</p> : null}
                  <ReplayHoldingTable holding={h} />
                </section>
              ))}
            </div>
          )}
        </Panel>
      ) : null}

      <Panel id="ask" title="Ask the evidence" description="Answers use verified stored facts known by the chosen date (today if none). A number appears only if it is in the evidence.">
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (canAsk) void ask();
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            Ticker
            <input value={ticker} onChange={(e) => setTicker(e.target.value)} maxLength={30} className="num w-28 border border-border bg-surface-raised px-2 py-1.5 uppercase focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none" />
          </label>
          <label className="flex min-w-64 flex-1 flex-col gap-1 text-sm">
            Question
            <input value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={500} placeholder="Which customers does it name?" className="border border-border bg-surface-raised px-2 py-1.5 focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none" />
          </label>
          <label className="flex items-center gap-2 pb-2 text-sm" title="Uses the chat model when it is an OpenAI or Google model. The wording is kept only if every number is in the evidence.">
            <input type="checkbox" checked={reword} onChange={(e) => setReword(e.target.checked)} />
            Reword with the chat model
          </label>
          <Button type="submit" size="sm" disabled={!canAsk || asking}>
            {asking ? "Searching…" : "Ask"}
          </Button>
        </form>
        {answerError ? (
          <p role="alert" className="mt-3 border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">{answerError}</p>
        ) : null}
        {answer ? <div className="mt-4"><EvidenceAnswerView answer={answer} /></div> : null}
      </Panel>
    </div>
  );
}
