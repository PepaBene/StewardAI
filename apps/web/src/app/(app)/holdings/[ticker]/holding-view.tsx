"use client";

import {
  ChevronDownIcon,
  ChevronRightIcon,
  InboxIcon,
  LayoutGridIcon,
  LoaderCircleIcon,
  PencilIcon,
  PlayIcon,
  RefreshCwIcon,
} from "lucide-react";
import Link from "next/link";
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { AskAboutSelection } from "@/components/chat/ask-about-selection";
import { EmptyState } from "@/components/empty-state";
import { ConfidenceBadge } from "@/components/evidence/confidence-badge";
import { ConfidenceBar } from "@/components/evidence/confidence-bar";
import { EvidenceLinks } from "@/components/evidence/evidence-links";
import { FactReviewForm, type ReviewResolution } from "@/components/evidence/fact-review-form";
import { ProvenanceTag } from "@/components/evidence/provenance-tag";
import { VerdictStatusBadge } from "@/components/evidence/verdict-status-badge";
import { FeedbackControl } from "@/components/feedback/feedback-control";
import { HoldingNews, useHoldingNews } from "@/components/news/holding-news";
import { HoldingMorePanels } from "@/components/reference/holding-more-panels";
import { IdentitySection, useHoldingIdentity } from "@/components/reference/identity-section";
import { CashDebtPanel } from "@/components/reference/cash-debt-panel";
import { ConcentrationPanel, PatentOverlapPanel, PriceDataPanel, StakesPanel } from "@/components/reference/holding-data-panels";
import { CorporateActionCoveragePanel } from "@/components/reference/holding-coverage";
import { EstimatesPanel } from "@/components/reference/estimates-panel";
import { FinancialHistoryPanel } from "@/components/reference/financial-history-panel";
import { InsiderTradesSection } from "@/components/reference/insider-trades-section";
import { InputCostsPanel } from "@/components/reference/input-costs-panel";
import { MacroContextPanel } from "@/components/reference/macro-context-panel";
import { TradePanel } from "@/components/reference/trade-panel";
import { NetworkPanel } from "@/components/reference/network-panel";
import { PatentsPanel } from "@/components/reference/patents-panel";
import { PeerPanel } from "@/components/reference/peer-panel";
import { OwnershipSection, useHoldingOwnership } from "@/components/reference/ownership-section";
import { PriceRiskSection } from "@/components/reference/price-risk-section";
import { ValuationPanel } from "@/components/reference/valuation-panel";
import { SegmentsPanel } from "@/components/reference/segments-panel";
import { HoldingResearch } from "@/components/research/holding-research";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  getBrowserApiClient,
  type FactOut,
  type FeedbackOut,
  type HoldingDetail,
  type JobSummary,
  type KpiStatus,
  type VerdictOut,
} from "@/lib/api/client";
import { NEVER_JUDGED } from "@/lib/evidence/board";
import {
  confidenceNextAction,
  confidenceReason,
  formatFactValue,
  formatObserved,
  formatTarget,
  humanizeMetric,
  tallyConfidence,
  tallyConfidenceLabel,
} from "@/lib/evidence/confidence";
import { factProvenance } from "@/lib/evidence/provenance";
import { allowedReviewActions, isHeld, REVIEW_ACTIONS, reviewQueueHref, scaleHint } from "@/lib/evidence/review";
import { formatDate, formatRelative } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { useNow } from "@/lib/hooks/use-now";
import { jobHref } from "@/lib/jobs/job-detail";
import { byLead, groupByStory } from "@/lib/news/feed-filters";
import { kpiAnchorId } from "@/lib/portfolio/kpi-anchor";
import { BASE_PRICE_DAYS, buildTimeline } from "@/lib/reference/holding-detail";
import { isExternalUrl } from "@/lib/sources/sources";

import {
  CandlePanel,
  current,
  DrawdownPanel,
  HoldersPanel,
  HoldingHeader,
  KpiBarsPanel,
  NextReportPanel,
  PositionPanel,
  ScorePanel,
  TimelinePanel,
} from "./holding-overview";
import { intradayData, useHoldingIntraday } from "./holding-intraday";

const POLL_MS = 3000;
const POLL_LIMIT_MS = 10 * 60 * 1000;
const TERMINAL = new Set(["succeeded", "failed"]);

export function HoldingView({ ticker }: { ticker: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}", { params: { path: { ticker } } }),
    [ticker],
  );
  const { state, reload } = useApiResource(fetcher);
  const [watching, setWatching] = useState<{ jobId: number; since: number } | null>(null);
  const detail = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;

  // After queueing a job, re-read the page until that job has finished.
  const watchedJob = watching
    ? [detail?.last_earnings_check, detail?.last_verdict_run].find((j) => j?.id === watching.jobId)
    : undefined;
  const busy = watching !== null && !(watchedJob && TERMINAL.has(watchedJob.status));
  useEffect(() => {
    if (!busy || !watching) return;
    const timer = setInterval(() => {
      if (Date.now() - watching.since > POLL_LIMIT_MS) setWatching(null);
      else reload();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [busy, watching, reload]);

  return (
    <div className="space-y-[22px]">
      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading {ticker}…
        </p>
      ) : null}
      {state.kind === "error" ? (
        <AlertMessage title={state.title}>
          <p>{state.detail}</p>
          <Button variant="outline" size="sm" className="mt-2" onClick={() => reload()}>
            <RefreshCwIcon aria-hidden="true" />
            Try again
          </Button>
        </AlertMessage>
      ) : null}
      {detail ? (
        <HoldingContent
          detail={detail}
          busy={busy}
          onQueued={(job) => {
            setWatching({ jobId: job.id, since: Date.now() });
            reload();
          }}
          reload={reload}
        />
      ) : null}
    </div>
  );
}

const fetchBoard = () => getBrowserApiClient().GET("/theses/board");
const fetchStrategy = () => getBrowserApiClient().GET("/config/strategy");
const fetchOverview = () => getBrowserApiClient().GET("/portfolio/overview");
const fetchPortfolioHistory = () =>
  getBrowserApiClient().GET("/portfolio/history", { params: { query: { days: BASE_PRICE_DAYS } } });

function isoDay(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function HoldingContent({
  detail,
  busy,
  onQueued,
  reload,
}: {
  detail: HoldingDetail;
  busy: boolean;
  onQueued: (job: JobSummary) => void;
  reload: () => void;
}) {
  const { thesis, ticker } = detail;
  const facts = useMemo(() => new Map(detail.facts.map((f) => [f.id, f])), [detail.facts]);
  const now = useNow();

  const pricesFetcher = useCallback(
    () =>
      getBrowserApiClient().GET("/holdings/{ticker}/prices", {
        params: { path: { ticker }, query: { days: BASE_PRICE_DAYS } },
      }),
    [ticker],
  );
  const prices = useApiResource(pricesFetcher).state;
  const board = useApiResource(fetchBoard).state;
  const strategy = useApiResource(fetchStrategy).state;
  const overview = useApiResource(fetchOverview).state;
  const portfolioHistory = useApiResource(fetchPortfolioHistory).state;
  const ownership = useHoldingOwnership(ticker);
  const identity = useHoldingIdentity(ticker);
  const news = useHoldingNews(ticker);
  const intraday = useHoldingIntraday(ticker);

  const priceData = current(prices);
  const boardData = current(board);
  const row = boardData?.rows.find((r) => r.ticker === ticker) ?? null;
  const classes = current(strategy)?.body.classifications?.[ticker] ?? null;
  const drift = current(overview)?.drift?.positions.find((p) => p.ticker === ticker) ?? null;
  const indexPoints = current(portfolioHistory)?.points ?? null;

  const timeline = useMemo(() => {
    const verdicts = detail.kpis.flatMap((k) =>
      k.history.map((v) => ({
        kpiLabel: humanizeMetric(k.kpi.metric),
        period: v.period,
        status: v.status,
        observed: formatObserved(v.observed_value, k.kpi.unit),
        target: formatTarget(k.kpi.operator, k.kpi.threshold, k.kpi.unit),
        created_at: v.created_at,
        factCount: v.fact_ids.length,
      })),
    );
    const stories = news.items
      ? groupByStory(news.items).map((group) => {
          const [lead] = byLead(group);
          const sources = lead.story.source_count;
          return {
            id: lead.story.id,
            date: lead.published_at,
            headline: lead.headline,
            source: `${lead.outlet_name ?? lead.outlet ?? "Source"}${sources > 1 ? ` · ${sources} sources` : ""}`,
            severity: lead.story.severity,
            href: isExternalUrl(lead.url) ? lead.url : null,
          };
        })
      : [];
    return buildTimeline(verdicts, stories);
  }, [detail.kpis, news.items]);

  // A fact marked wrong is quarantined and the verdicts re-judged: follow that run.
  const onFeedback = useCallback(
    (mark: FeedbackOut) => {
      const job = mark.effects.verdict_job_id;
      if (typeof job === "number") {
        onQueued({
          id: job,
          job_type: "thesis_verdicts",
          trigger: "event",
          status: "queued",
          error: null,
          created_at: mark.created_at,
          started_at: null,
          finished_at: null,
        });
      } else if (mark.effects.quarantined) {
        reload();
      }
    },
    [onQueued, reload],
  );

  return (
    <>
      <HoldingHeader
        detail={detail}
        identity={current(identity.state)}
        classes={classes}
        row={row}
        bars={priceData?.bars ?? []}
        intraday={intradayData(intraday)}
      />

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,2.1fr)_minmax(0,1fr)]">
        <CandlePanel ticker={ticker} history={priceData} loading={prices.kind === "loading"} intraday={intraday} />
        <div className="flex min-w-0 flex-col gap-5">
          <PositionPanel position={detail.position} base={detail.base_currency ?? ""} drift={drift} />
          <ScorePanel row={row} kpis={detail.kpis} method={boardData?.score_method ?? null} loaded={board.kind !== "loading"} />
          <div className="lg:sticky lg:top-3 lg:self-start">
            <ValuationPanel ticker={ticker} compact />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <KpiBarsPanel ticker={ticker} kpis={detail.kpis} hasThesis={Boolean(thesis)} />
        <DrawdownPanel history={priceData} portfolioIndex={indexPoints} />
        <NextReportPanel detail={detail} today={isoDay(now)} />
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <TimelinePanel ticker={ticker} entries={timeline} loaded={news.items !== null || news.failed} />
        <HoldersPanel data={current(ownership.state)} loaded={ownership.state.kind !== "loading"} />
      </div>

      <SegmentsPanel ticker={ticker} />

      <FinancialHistoryPanel ticker={ticker} />

      <CashDebtPanel ticker={ticker} />

      <PeerPanel ticker={ticker} />

      <EstimatesPanel ticker={ticker} />

      <NetworkPanel ticker={ticker} />

      <PatentsPanel ticker={ticker} />

      <PatentOverlapPanel ticker={ticker} />

      <ConcentrationPanel ticker={ticker} />

      <MacroContextPanel ticker={ticker} />

      <TradePanel ticker={ticker} />

      <InputCostsPanel ticker={ticker} />

      <Blueprint aria-labelledby="thesis-heading" className="space-y-5 px-[22px] py-5">
        <PanelHeading
          kicker="Thesis & evidence"
          title={<span id="thesis-heading">Why you own it, and the verdicts</span>}
          action={
            <span className="flex flex-wrap gap-1">
              <Button asChild variant="ghost" size="sm">
                <Link href="/theses">
                  <LayoutGridIcon aria-hidden="true" />
                  Thesis board
                </Link>
              </Button>
              <Button asChild variant="ghost" size="sm">
                <Link href={`/config/theses#${encodeURIComponent(ticker)}`}>
                  <PencilIcon aria-hidden="true" />
                  Edit thesis
                </Link>
              </Button>
            </span>
          }
        />
        <JobActions detail={detail} busy={busy} onQueued={onQueued} />
        {thesis ? (
          <div className="space-y-3 border-l-2 border-accent pl-4 text-sm">
            <p className="whitespace-pre-line">{thesis.why_owned}</p>
            {thesis.exit_conditions && thesis.exit_conditions.length > 0 ? (
              <div>
                <p className="font-medium">Exit conditions</p>
                <ul className="mt-1 list-disc space-y-0.5 pl-5 text-text-muted">
                  {thesis.exit_conditions.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {thesis.review_date ? <p className="text-text-muted">Review by {formatDate(thesis.review_date)}</p> : null}
          </div>
        ) : (
          <EmptyState title="No thesis yet">
            Write why you own {ticker} and the KPIs that must hold in the{" "}
            <Link className="text-accent-ink underline-offset-4 hover:underline" href="/config/theses">
              theses editor
            </Link>
            .
          </EmptyState>
        )}
        {thesis && thesis.kpis && thesis.kpis.length > 0 ? (
          <KpiTable kpis={detail.kpis} facts={facts} onFeedback={onFeedback} />
        ) : null}
      </Blueprint>

      <FactsSection ticker={ticker} facts={detail.facts} reload={reload} onQueued={onQueued} onFeedback={onFeedback} />

      <HoldingResearch ticker={ticker} />

      <HoldingNews ticker={ticker} news={news} />

      <PriceRiskSection ticker={ticker} currency={detail.position?.instrument_currency} />

      <OwnershipSection resource={ownership} />

      <StakesPanel ticker={ticker} />

      <PriceDataPanel ticker={ticker} />

      <CorporateActionCoveragePanel ticker={ticker} />

      <InsiderTradesSection ticker={ticker} />

      <IdentitySection ticker={ticker} resource={identity} />

      <HoldingMorePanels ticker={ticker} />
    </>
  );
}

function JobLine({ label, job }: { label: string; job: JobSummary | null | undefined }) {
  if (!job) return null;
  const at = job.finished_at ?? job.started_at ?? job.created_at;
  return (
    <p className="text-sm text-text-muted">
      {label}: <span className="font-medium text-text">{job.status}</span>
      {" · "}
      <time dateTime={at}>{formatRelative(new Date(at))}</time>
      {" · "}
      <Link href={jobHref(job.id)} className="text-accent-ink hover:underline">
        job #{job.id}
      </Link>
      {job.status === "failed" && job.error ? <span className="block text-text">{job.error}</span> : null}
    </p>
  );
}

function JobActions({
  detail,
  busy,
  onQueued,
}: {
  detail: HoldingDetail;
  busy: boolean;
  onQueued: (job: JobSummary) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [showUrl, setShowUrl] = useState(false);
  const [url, setUrl] = useState("");
  const [reportDate, setReportDate] = useState("");

  async function run(kind: "check" | "verdicts") {
    setPending(true);
    setError(null);
    try {
      const api = getBrowserApiClient();
      const result =
        kind === "check"
          ? await api.POST("/jobs/earnings-check", {
              body: {
                ticker: detail.ticker,
                ...(url.trim() ? { url: url.trim() } : {}),
                ...(reportDate ? { report_date: reportDate } : {}),
              },
            })
          : await api.POST("/jobs/thesis-verdicts", { body: { ticker: detail.ticker } });
      if (result.data) onQueued(result.data);
      else setError(`Could not queue the job (HTTP ${result.response.status}).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <section aria-label="Checks" className="space-y-2 border border-border-soft bg-surface-muted p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={() => void run("check")} disabled={pending || busy}>
          {busy ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <PlayIcon aria-hidden="true" />}
          Run earnings check
        </Button>
        <Button size="sm" variant="outline" onClick={() => void run("verdicts")} disabled={pending || busy}>
          <RefreshCwIcon aria-hidden="true" />
          Re-run verdicts
        </Button>
        <Button size="sm" variant="ghost" aria-expanded={showUrl} onClick={() => setShowUrl((v) => !v)}>
          {showUrl ? <ChevronDownIcon aria-hidden="true" /> : <ChevronRightIcon aria-hidden="true" />}
          Use a specific document
        </Button>
      </div>
      {showUrl ? (
        <div className="grid gap-3 sm:grid-cols-[2fr_1fr]">
          <div className="space-y-1">
            <Label htmlFor="check-url">Release or report URL</Label>
            <Input id="check-url" type="url" placeholder="https://…" value={url} onChange={(e) => setUrl(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="check-date">Report date</Label>
            <Input id="check-date" type="date" value={reportDate} onChange={(e) => setReportDate(e.target.value)} />
          </div>
        </div>
      ) : null}
      <JobLine label="Last earnings check" job={detail.last_earnings_check} />
      <JobLine label="Last verdict run" job={detail.last_verdict_run} />
      {error ? <AlertMessage title="Not queued">{error}</AlertMessage> : null}
    </section>
  );
}

type OnFeedback = (mark: FeedbackOut) => void;

function KpiTable({ kpis, facts, onFeedback }: { kpis: KpiStatus[]; facts: Map<number, FactOut>; onFeedback: OnFeedback }) {
  const [open, setOpen] = useState<string | null>(null);
  // A command-palette KPI result ("revenue growth AAPL") links to #kpi-<id>: open that row
  // and scroll to it once, on arrival — not just the holding page, the KPI itself. Done
  // after mount (never in the initial render) so server and client markup still agree.
  useEffect(() => {
    const hash = window.location.hash.slice(1);
    const match = kpis.find((k) => kpiAnchorId(k.kpi.id) === hash);
    if (!match) return;
    queueMicrotask(() => setOpen(match.kpi.id));
    document.getElementById(hash)?.scrollIntoView({ block: "center" });
    // Only once, for the link that brought us here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <section aria-labelledby="kpi-heading" className="scroll-mt-6 space-y-2">
      <h3 id="kpi-heading" className="font-heading text-lg font-semibold">
        Thesis KPIs: verdicts and evidence
      </h3>
      <AskAboutSelection className="relative overflow-x-auto border border-border">
        <table className="w-full min-w-[48rem] text-sm">
          <caption className="sr-only">Latest verdict per KPI; open a row for its evidence and history</caption>
          <thead className="bg-surface-muted">
            <tr className="border-b border-border text-[11px] tracking-[0.08em] text-text-muted uppercase">
              <th scope="col" className="px-3 py-2 text-left font-medium">KPI</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Target</th>
              <th scope="col" className="px-3 py-2 text-left font-medium">Period</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Observed</th>
              <th scope="col" className="px-3 py-2 text-left font-medium">Verdict</th>
              <th scope="col" className="px-3 py-2 text-left font-medium">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {kpis.map(({ kpi, current, history }) => {
              const expanded = open === kpi.id;
              return (
                <KpiRows
                  key={kpi.id}
                  kpiId={kpi.id}
                  label={humanizeMetric(kpi.metric)}
                  target={formatTarget(kpi.operator, kpi.threshold, kpi.unit)}
                  unit={kpi.unit}
                  basis={kpi.basis}
                  current={current ?? null}
                  history={history}
                  expanded={expanded}
                  onToggle={() => setOpen(expanded ? null : kpi.id)}
                  facts={facts}
                  onFeedback={onFeedback}
                />
              );
            })}
          </tbody>
        </table>
      </AskAboutSelection>
    </section>
  );
}

function KpiRows({
  kpiId,
  label,
  target,
  unit,
  basis,
  current,
  history,
  expanded,
  onToggle,
  facts,
  onFeedback,
}: {
  kpiId: string;
  label: string;
  target: string;
  unit: string;
  basis: string;
  current: VerdictOut | null;
  history: VerdictOut[];
  expanded: boolean;
  onToggle: () => void;
  facts: Map<number, FactOut>;
  onFeedback: OnFeedback;
}) {
  const panelId = `kpi-${kpiId}`;
  return (
    <>
      <tr id={kpiAnchorId(kpiId)} className="border-b border-border last:border-0">
        <th scope="row" className="px-3 py-2 text-left font-normal">
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={expanded}
            aria-controls={panelId}
            className="inline-flex items-start gap-1 rounded-sm text-left hover:text-accent"
          >
            {expanded ? (
              <ChevronDownIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            ) : (
              <ChevronRightIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            )}
            <span>
              <span className="block font-medium text-text">{label}</span>
              <span className="block font-mono text-xs text-text-muted">
                {kpiId} · {basis}
              </span>
            </span>
          </button>
        </th>
        <td className="num px-3 py-2 text-right">{target}</td>
        <td className="px-3 py-2">{current?.period ?? "—"}</td>
        <td className="num px-3 py-2 text-right">{formatObserved(current?.observed_value, unit)}</td>
        <td className="px-3 py-2">
          {current ? (
            <span className="inline-flex flex-wrap items-center gap-1">
              <VerdictStatusBadge status={current.status} />
              {current.stale ? (
                <span className="text-xs text-text-muted" title="Judged under an older theses config">
                  (old config)
                </span>
              ) : null}
              <FeedbackControl
                target={{ kind: "verdict", id: current.id }}
                label={`verdict on ${label}${current.period ? ` ${current.period}` : ""}`}
              />
            </span>
          ) : (
            <span className="inline-flex flex-wrap items-center gap-1">
              <VerdictStatusBadge status="unknown" />
              <span className="text-xs text-text-muted">never judged</span>
            </span>
          )}
        </td>
        <td className="px-3 py-2">
          {current ? <ConfidenceBadge confidence={current.confidence} since={current.created_at} /> : null}
        </td>
      </tr>
      {expanded ? (
        <tr className="border-b border-border bg-surface-muted/60">
          <td colSpan={6} id={panelId} className="space-y-4 px-4 py-3">
            {current ? <VerdictEvidence verdict={current} facts={facts} onFeedback={onFeedback} /> : <p>{NEVER_JUDGED}.</p>}
            {history.length > 1 ? (
              <div>
                <p className="mb-1 text-xs font-medium tracking-wide text-text-muted uppercase">History</p>
                <ul className="divide-y divide-border rounded-md border border-border bg-surface-raised">
                  {history.map((v) => (
                    <li key={v.id} className="flex flex-wrap items-center gap-3 px-3 py-1.5">
                      <span className="w-24">{v.period ?? "—"}</span>
                      <span className="num w-28 text-right">{formatObserved(v.observed_value, unit)}</span>
                      <VerdictStatusBadge status={v.status} />
                      <ConfidenceBadge confidence={v.confidence} since={v.created_at} />
                      <FeedbackControl target={{ kind: "verdict", id: v.id }} label={`verdict on ${label} ${v.period ?? ""}`.trim()} />
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </td>
        </tr>
      ) : null}
    </>
  );
}

function VerdictEvidence({
  verdict,
  facts,
  onFeedback,
}: {
  verdict: VerdictOut;
  facts: Map<number, FactOut>;
  onFeedback: OnFeedback;
}) {
  const used = verdict.fact_ids.map((id) => facts.get(id)).filter((f): f is FactOut => Boolean(f));
  return (
    <div className="space-y-2 text-sm">
      <p className="flex flex-wrap items-center gap-1.5">
        {verdict.rationale}
        <ProvenanceTag provenance="computed" />
      </p>
      {used.length === 0 ? (
        <p className="text-text-muted">No verified figure behind this verdict.</p>
      ) : (
        <ul className="space-y-2">
          {used.map((f) => (
            <li key={f.id}>
              <FactEvidence fact={f} onFeedback={onFeedback} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function FactEvidence({ fact, onFeedback }: { fact: FactOut; onFeedback: OnFeedback }) {
  return (
    <figure className="rounded-md border border-border bg-surface-raised p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">
          {humanizeMetric(fact.metric)} {fact.period}
        </span>
        <span className="num">{formatFactValue(fact)}</span>
        <ConfidenceBadge
          confidence={fact.confidence}
          reason={confidenceReason(fact)}
          nextAction={confidenceNextAction(fact.status)}
          since={fact.reviewed_at ?? fact.extracted_at}
        />
        <ProvenanceTag provenance={factProvenance(fact)} />
        <EvidenceLinks fact={fact} />
        <FactFeedback fact={fact} onFeedback={onFeedback} />
      </div>
      <blockquote className="mt-2 border-l-2 border-border pl-3 font-mono text-xs break-words text-text-muted">
        {fact.snippet}
        {fact.unit_snippet ? <span className="mt-1 block">{fact.unit_snippet}</span> : null}
      </blockquote>
    </figure>
  );
}

/** Wrong / irrelevant / useful on one figure; wrong quarantines it and re-judges the verdicts. */
function FactFeedback({ fact, onFeedback }: { fact: FactOut; onFeedback: OnFeedback }) {
  return (
    <FeedbackControl
      target={{ kind: "fact", id: fact.id }}
      label={`figure ${humanizeMetric(fact.metric)} ${fact.period}`}
      valueHint={scaleHint(fact)}
      onMarked={onFeedback}
    />
  );
}

function FactsSection({
  ticker,
  facts,
  reload,
  onQueued,
  onFeedback,
}: {
  ticker: string;
  facts: FactOut[];
  reload: () => void;
  onQueued: (job: JobSummary) => void;
  onFeedback: OnFeedback;
}) {
  const [lastReview, setLastReview] = useState<ReviewResolution | null>(null);
  const shown = facts.filter((f) => f.status !== "rejected");
  const rejected = facts.filter((f) => f.status === "rejected");
  const held = facts.filter((f) => isHeld(f.status)).length;
  const confidence = tallyConfidence(shown.map((f) => f.confidence));
  const confidenceLabel = tallyConfidenceLabel(confidence);

  function onResolved(resolution: ReviewResolution) {
    setLastReview(resolution);
    onQueued(resolution.result.verdict_job);
    reload();
  }

  return (
    <Blueprint aria-labelledby="facts-heading" className="space-y-3 px-[22px] py-5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <span className="flex flex-wrap items-end gap-3">
          <PanelHeading kicker="Facts" title={<span id="facts-heading">Extracted figures</span>} />
          {shown.length > 0 ? (
            <ConfidenceBar counts={confidence} label={confidenceLabel || "No figures yet"} />
          ) : null}
        </span>
        {held > 0 ? (
          <Button asChild variant="outline" size="sm">
            <Link href={reviewQueueHref(ticker)}>
              <InboxIcon aria-hidden="true" />
              <span className="num">{held}</span> held figure{held === 1 ? "" : "s"}: open the review queue
            </Link>
          </Button>
        ) : null}
      </div>
      {held > 0 ? (
        <p className="text-sm text-text-muted">
          Held and quarantined figures are used in no verdict until you accept or correct them.
        </p>
      ) : null}
      <div role="status" aria-live="polite">
        {lastReview ? (
          <p className="rounded-md border border-border bg-surface-muted p-2 text-sm">
            <span className="font-medium">
              {REVIEW_ACTIONS[lastReview.action].done} {humanizeMetric(lastReview.fact.metric)} {lastReview.fact.period}.
            </span>{" "}
            Verdicts are being re-judged (job #{lastReview.result.verdict_job.id}); the KPI table above updates when the run
            finishes.
          </p>
        ) : null}
      </div>
      {facts.length === 0 ? (
        <EmptyState title="No figures yet">
          Run an earnings check to read this company&rsquo;s latest results release.
        </EmptyState>
      ) : (
        <AskAboutSelection>
          <FactTable facts={shown} onResolved={onResolved} onFeedback={onFeedback} />
        </AskAboutSelection>
      )}
      {rejected.length > 0 ? (
        <details className="border border-border-soft p-3 text-sm">
          <summary className="cursor-pointer font-medium">
            {rejected.length} figure{rejected.length === 1 ? "" : "s"} rejected by the validator or by you
          </summary>
          <FactTable facts={rejected} onResolved={onResolved} onFeedback={onFeedback} />
        </details>
      ) : null}
    </Blueprint>
  );
}

function FactTable({
  facts,
  onResolved,
  onFeedback,
}: {
  facts: FactOut[];
  onResolved: (resolution: ReviewResolution) => void;
  onFeedback: OnFeedback;
}) {
  const [open, setOpen] = useState<number | null>(null);
  if (facts.length === 0) return null;
  return (
    <div className="relative mt-2 overflow-x-auto border border-border">
      <table className="w-full min-w-[48rem] text-sm">
        <thead className="bg-surface-muted">
          <tr className="border-b border-border text-[11px] tracking-[0.08em] text-text-muted uppercase">
            <th scope="col" className="px-3 py-2 text-left font-medium">Period</th>
            <th scope="col" className="px-3 py-2 text-left font-medium">Metric</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Value</th>
            <th scope="col" className="px-3 py-2 text-left font-medium">Confidence</th>
            <th scope="col" className="px-3 py-2 text-left font-medium">Provenance</th>
            <th scope="col" className="px-3 py-2 text-left font-medium">Source</th>
            <th scope="col" className="px-3 py-2 text-left font-medium">Feedback</th>
          </tr>
        </thead>
        <tbody>
          {facts.map((f) => {
            const actions = allowedReviewActions(f.status);
            const expanded = open === f.id;
            const panelId = `review-fact-${f.id}`;
            return (
              <Fragment key={f.id}>
                <tr className="border-b border-border align-top last:border-0">
                  <td className="px-3 py-2 whitespace-nowrap">{f.period}</td>
                  <th scope="row" className="px-3 py-2 text-left font-normal">
                    {humanizeMetric(f.metric)}
                    {f.basis === "adjusted" ? <span className="ml-1 text-xs text-text-muted">(adjusted)</span> : null}
                  </th>
                  <td className="num px-3 py-2 text-right whitespace-nowrap">{formatFactValue(f)}</td>
                  <td className="px-3 py-2">
                    <ConfidenceBadge
                      confidence={f.confidence}
                      reason={confidenceReason(f)}
                      nextAction={confidenceNextAction(f.status)}
                      since={f.reviewed_at ?? f.extracted_at}
                    />
                    {actions.length > 0 ? (
                      <Button
                        size="sm"
                        variant={isHeld(f.status) ? "outline" : "ghost"}
                        className="mt-1"
                        aria-expanded={expanded}
                        aria-controls={expanded ? panelId : undefined}
                        onClick={() => setOpen(expanded ? null : f.id)}
                      >
                        {expanded ? <ChevronDownIcon aria-hidden="true" /> : <ChevronRightIcon aria-hidden="true" />}
                        {isHeld(f.status) ? "Review" : "Correct or reject"}
                        <span className="sr-only">
                          {" "}
                          {humanizeMetric(f.metric)} {f.period}
                        </span>
                      </Button>
                    ) : null}
                  </td>
                  <td className="px-3 py-2">
                    <ProvenanceTag provenance={factProvenance(f)} />
                  </td>
                  <td className="px-3 py-2">
                    <EvidenceLinks fact={f} />
                  </td>
                  <td className="px-3 py-2">
                    <FactFeedback fact={f} onFeedback={onFeedback} />
                  </td>
                </tr>
                {expanded ? (
                  <tr className="border-b border-border bg-surface-muted/60">
                    <td colSpan={7} id={panelId} className="px-4 py-3">
                      <blockquote className="mb-3 border-l-2 border-border pl-3 font-mono text-xs break-words text-text-muted">
                        {f.snippet}
                      </blockquote>
                      <FactReviewForm
                        fact={f}
                        onCancel={() => setOpen(null)}
                        onResolved={(resolution) => {
                          setOpen(null);
                          onResolved(resolution);
                        }}
                        className="max-w-2xl"
                      />
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
