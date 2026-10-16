"use client";

import { LoaderCircleIcon, PlayIcon, RefreshCwIcon, ScaleIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useState, type FormEvent } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import { PageHeader } from "@/components/page-header";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  getBrowserApiClient,
  type JobSummary,
  type PortfolioReview,
  type ThesisMemoSummary,
} from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";
import { useJobStatus } from "@/lib/hooks/use-job-status";
import { jobHref } from "@/lib/jobs/job-detail";
import { normaliseTicker } from "@/lib/research/report";

const SELECT =
  "h-9 w-full border border-border bg-surface-raised px-3 py-1 text-base text-text shadow-xs outline-none md:text-sm focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30";

const STANCE_TEXT: Record<string, string> = {
  keep: "Keep",
  add: "Add",
  trim: "Trim",
  exit: "Exit",
  buy: "Buy",
  pass: "Pass",
};

function JobLine({ label, job, onDone }: { label: string; job: JobSummary; onDone: () => void }) {
  const watched = useJobStatus(job);
  const finished = watched?.status === "succeeded" || watched?.status === "failed";
  useEffect(() => {
    if (finished) onDone();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finished]);
  if (!watched) return null;
  return (
    <div role="status" className="flex flex-wrap items-center gap-2 border border-border bg-surface-muted p-2 text-sm">
      <span>{label}</span>
      <JobStatusBadge status={watched.status} />
      <Link href={jobHref(job.id)} className="text-accent-ink hover:underline">
        job #{job.id}
      </Link>
      {watched.status === "failed" && watched.error ? <span>{watched.error}</span> : null}
      {watched.gaveUp ? <span className="text-text-muted">Still running; follow the job page.</span> : null}
      {watched.status === "succeeded" ? <span className="text-text-muted">Finished; it is in the lists below.</span> : null}
    </div>
  );
}

function ThesisStarter({ onDone }: { onDone: () => void }) {
  const id = useId();
  const [ticker, setTicker] = useState("");
  const [mode, setMode] = useState<"held" | "buy">("held");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [queued, setQueued] = useState<{ job: JobSummary; ticker: string } | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const t = normaliseTicker(ticker);
    if (!t) {
      setError("Type a ticker: letters, digits, dots, dashes or underscores, e.g. AAPL_US_EQ.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      const { data, response } = await getBrowserApiClient().POST("/jobs/thesis", { body: { ticker: t, mode } });
      if (data) setQueued({ job: data, ticker: t });
      else setError(`Not queued (HTTP ${response.status}).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-2">
      <form onSubmit={(e) => void submit(e)} className="flex flex-wrap items-end gap-2">
        <div className="w-48 space-y-1">
          <Label htmlFor={`${id}-ticker`}>Ticker</Label>
          <Input
            id={`${id}-ticker`}
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            placeholder="AAPL_US_EQ"
            spellCheck={false}
            autoCapitalize="characters"
            maxLength={32}
          />
        </div>
        <div className="w-64 space-y-1">
          <Label htmlFor={`${id}-mode`}>This stock is</Label>
          <select id={`${id}-mode`} value={mode} onChange={(e) => setMode(e.target.value as "held" | "buy")} className={SELECT}>
            <option value="held">One I own (the thesis is saved)</option>
            <option value="buy">One I might buy (I accept it first)</option>
          </select>
        </div>
        <Button type="submit" size="sm" disabled={pending} className="mb-0.5">
          {pending ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <PlayIcon aria-hidden="true" />}
          Write the thesis
        </Button>
      </form>
      <p className="text-xs text-text-muted">
        Runs on its own once started: deep research with every record the app holds plus web search, then the thesis with a
        stance, an executive summary and a detailed memo. A deep run costs a few dollars of AI spend at most.
      </p>
      {error ? <AlertMessage title="Thesis not started">{error}</AlertMessage> : null}
      {queued ? <JobLine label={`Thesis for ${queued.ticker}:`} job={queued.job} onDone={onDone} /> : null}
    </div>
  );
}

function ReviewStarter({ onDone }: { onDone: () => void }) {
  const id = useId();
  const [tickers, setTickers] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [queued, setQueued] = useState<JobSummary | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const list = tickers
      .split(/[\s,]+/)
      .map((t) => normaliseTicker(t))
      .filter((t): t is string => Boolean(t));
    setPending(true);
    setError(null);
    try {
      const { data, response } = await getBrowserApiClient().POST("/jobs/portfolio-review", { body: { tickers: list } });
      if (data) setQueued(data);
      else setError(`Not queued (HTTP ${response.status}).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-2">
      <form onSubmit={(e) => void submit(e)} className="flex flex-wrap items-end gap-2">
        <div className="w-96 max-w-full space-y-1">
          <Label htmlFor={`${id}-tickers`}>Stocks to look at as buys (optional)</Label>
          <Input
            id={`${id}-tickers`}
            value={tickers}
            onChange={(e) => setTickers(e.target.value)}
            placeholder="leave empty to let the review search"
            spellCheck={false}
          />
        </div>
        <Button type="submit" size="sm" disabled={pending} className="mb-0.5">
          {pending ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <ScaleIcon aria-hidden="true" />}
          Review the portfolio
        </Button>
      </form>
      <p className="text-xs text-text-muted">
        Flags holdings to trim or exit, finds the gaps in your strategy, researches buy candidates in depth and judges each against
        your return hurdle and asymmetry gate. Nothing is traded.
      </p>
      {error ? <AlertMessage title="Review not started">{error}</AlertMessage> : null}
      {queued ? <JobLine label="Portfolio review:" job={queued} onDone={onDone} /> : null}
    </div>
  );
}

export function ThesisView() {
  const [memos, setMemos] = useState<ThesisMemoSummary[] | null>(null);
  const [reviews, setReviews] = useState<PortfolioReview[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const api = getBrowserApiClient();
    Promise.all([api.GET("/thesis/memos", { params: { query: { limit: 50 } } }), api.GET("/thesis/reviews")])
      .then(([m, r]) => {
        if (cancelled) return;
        if (m.data && r.data) {
          setMemos(m.data);
          setReviews(r.data);
          setError(null);
        } else setError(`The API answered HTTP ${m.response.status} and ${r.response.status}.`);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const reload = () => setReloadKey((k) => k + 1);

  return (
    <div className="flex flex-col gap-[22px]">
      <PageHeader
        title="Thesis workup"
        description="Ask for the thesis of a stock and the app does the rest: research, a stance (keep, add, trim or exit; buy or pass), the executive summary and the detailed memo. You can also ask the chat about any thesis."
        aside={
          <Button variant="ghost" size="sm" onClick={reload}>
            <RefreshCwIcon aria-hidden="true" />
            Reload
          </Button>
        }
      />

      <Blueprint aria-labelledby="new-thesis-heading" className="flex flex-col gap-3 px-5 py-[18px]">
        <PanelHeading kicker="Start a run" title={<span id="new-thesis-heading">New thesis</span>} />
        <ThesisStarter onDone={reload} />
      </Blueprint>

      <Blueprint aria-labelledby="new-review-heading" className="flex flex-col gap-3 px-5 py-[18px]">
        <PanelHeading kicker="What to sell, trim and buy" title={<span id="new-review-heading">Portfolio review</span>} />
        <ReviewStarter onDone={reload} />
      </Blueprint>

      {error ? <AlertMessage title="Could not load">{error}</AlertMessage> : null}

      <section aria-labelledby="reviews-heading" className="flex flex-col gap-2">
        <h2 id="reviews-heading" className="text-[22px] leading-tight font-semibold">
          Reviews
        </h2>
        {reviews === null ? (
          <p className="text-sm text-text-muted" role="status">Loading…</p>
        ) : reviews.length === 0 ? (
          <EmptyState title="No review yet">Start one above.</EmptyState>
        ) : (
          <Blueprint className="divide-y divide-border-soft">
            {reviews.map((r) => (
              <Link key={r.id} href={`/thesis/reviews/${r.id}`} className="block px-4 py-3 hover:bg-surface-muted">
                <span className="font-heading text-[17px] font-semibold">Review #{r.id}</span>
                <span className="num ml-2 text-xs text-text-muted">
                  {r.status === "done" ? "done" : "researching"} · {formatDateTime(r.created_at)}
                </span>
                {r.summary ? <p className="mt-1 line-clamp-2 text-sm text-text-muted">{r.summary}</p> : null}
              </Link>
            ))}
          </Blueprint>
        )}
      </section>

      <section aria-labelledby="memos-heading" className="flex flex-col gap-2">
        <h2 id="memos-heading" className="text-[22px] leading-tight font-semibold">
          Theses
        </h2>
        {memos === null ? (
          <p className="text-sm text-text-muted" role="status">Loading…</p>
        ) : memos.length === 0 ? (
          <EmptyState title="No thesis yet">Write one above.</EmptyState>
        ) : (
          <Blueprint className="divide-y divide-border-soft">
            {memos.map((m) => (
              <Link key={m.id} href={`/thesis/memos/${m.id}`} className="block px-4 py-3 hover:bg-surface-muted">
                <span className="font-mono text-sm">{m.ticker}</span>
                <span className="ml-2 bg-border-soft px-2 py-px text-xs font-medium">{STANCE_TEXT[m.stance] ?? m.stance}</span>
                <span className="num ml-2 text-xs text-text-muted">
                  {m.mode === "buy" ? (m.status === "open" ? "waiting for your decision" : m.status) : "saved"} ·{" "}
                  {formatDateTime(m.created_at)}
                </span>
                <p className="mt-1 line-clamp-2 text-sm text-text-muted">{m.executive_summary}</p>
              </Link>
            ))}
          </Blueprint>
        )}
      </section>
    </div>
  );
}
