"use client";

import { FileTextIcon, FilterIcon, LoaderCircleIcon, RefreshCwIcon, TriangleAlertIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useId, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { BarChart, Gauge, Histogram, Legend, Meter, Ring, SparkArea } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { ConfidenceBadge } from "@/components/evidence/confidence-badge";
import { EvidenceLinks } from "@/components/evidence/evidence-links";
import { ProvenanceTag } from "@/components/evidence/provenance-tag";
import { isTypingTarget, ReviewActionBar } from "@/components/evidence/review-action-bar";
import { FeedbackControl } from "@/components/feedback/feedback-control";
import { PageHeader } from "@/components/page-header";
import { Blueprint, BlueprintCorners } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { getBrowserApiClient, type HoldingDetail, type ReviewItem } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { formatNumber } from "@/lib/charts/format";
import { shortTicker } from "@/lib/evidence/board";
import { formatFactValue, formatTarget, humanizeMetric } from "@/lib/evidence/confidence";
import { factProvenance } from "@/lib/evidence/provenance";
import {
  feedsKpis,
  filedCorrection,
  formatInFactScale,
  highlightValue,
  historyAgainstClaim,
  matchesTier,
  moveSelection,
  parseReviewTicker,
  parseTierFilter,
  reviewQueueHref,
  scaleHint,
  sortOldestFirst,
  tierFilterLabel,
  tierFilterOptions,
  type TierFilter,
} from "@/lib/evidence/review";
import { formatDate, formatDateTime } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

import { FailedChecks, HeldBadge, Reconciliation, ResolutionCard, Restatement, type Resolved } from "./review-parts";

const LINK = "text-accent-ink underline-offset-4 hover:underline";
const SELECT =
  "h-8 border border-border bg-surface-raised px-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-focus";

const fetchStats = () => getBrowserApiClient().GET("/facts/review-stats", { params: { query: { days: 30 } } });

export function ReviewView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const ticker = parseReviewTicker(new URLSearchParams(params.toString()));
  const tier = parseTierFilter(params.get("tier"));
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/facts/review-queue", { params: { query: { ticker: ticker ?? undefined } } }),
    [ticker],
  );
  const { state, reload } = useApiResource(fetcher);
  const stats = useApiResource(fetchStats);
  const [resolved, setResolved] = useState<Resolved[]>([]);
  // Facts decided here leave the list at once (and stay out after their result is dismissed).
  const [removed, setRemoved] = useState<ReadonlySet<number>>(() => new Set());
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;

  const pending = useMemo(
    () => sortOldestFirst((data ?? []).filter((i) => !removed.has(i.fact.id) && (!ticker || i.ticker === ticker))),
    [data, removed, ticker],
  );
  const items = useMemo(() => pending.filter((i) => matchesTier(i, tier)), [pending, tier]);
  const ids = useMemo(() => items.map((i) => i.fact.id), [items]);
  const selected = items.find((i) => i.fact.id === selectedId) ?? items[0] ?? null;

  const setQuery = (next: { ticker?: string | null; tier?: TierFilter }) => {
    const q = new URLSearchParams();
    const t = next.ticker === undefined ? ticker : next.ticker;
    const f = next.tier === undefined ? tier : next.tier;
    if (t) q.set("ticker", t);
    if (f !== "all") q.set("tier", String(f));
    router.replace(q.size ? `${pathname}?${q}` : pathname, { scroll: false });
  };

  // J / K move through the list.
  const current = selected?.fact.id ?? null;
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.key !== "j" && e.key !== "k") || e.metaKey || e.ctrlKey || e.altKey || isTypingTarget(e.target)) return;
      const next = moveSelection(ids, current, e.key === "j" ? 1 : -1);
      if (next === null) return;
      e.preventDefault();
      setSelectedId(next);
      document.getElementById(`queue-row-${next}`)?.scrollIntoView({ block: "nearest" });
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [ids, current]);

  const onResolved = (item: ReviewItem, resolution: Resolved) => {
    setSelectedId(nextAfter(ids, item.fact.id));
    setRemoved((all) => new Set(all).add(resolution.fact.id));
    setResolved((all) => [resolution, ...all]);
    // A correction or restatement can change other items; refresh the queue and the tiles quietly.
    reload();
    stats.reload();
  };

  return (
    <div className="flex flex-col gap-[22px]">
      <PageHeader
        kicker="Research & Verification"
        title="Review queue"
        description="Figures the model extracted that need your eyes before they count toward a verdict."
        aside={
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              reload();
              stats.reload();
            }}
          >
            <RefreshCwIcon aria-hidden="true" />
            Reload
          </Button>
        }
      />
      <p className="-mt-2 max-w-prose text-sm text-text-muted">
        Held by the validator or quarantined by reconciliation, none of these is used in a verdict until you accept or
        correct it. Every decision needs a reason and re-judges the holding&rsquo;s verdicts.
      </p>

      <StatTiles stats={stats.state} queue={data ?? []} ticker={ticker} />

      {resolved.length > 0 ? (
        <section aria-labelledby="resolved-heading" className="space-y-3">
          <h2 id="resolved-heading" className="text-[22px] leading-tight font-semibold">
            Resolved just now
          </h2>
          <ul className="grid gap-5 lg:grid-cols-2">
            {resolved.map((r) => (
              <li key={r.fact.id}>
                <ResolutionCard
                  resolution={r}
                  onDismiss={() => setResolved((all) => all.filter((x) => x.fact.id !== r.fact.id))}
                />
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <QueueFilter ticker={ticker} items={data ?? []} onChange={(t) => setQuery({ ticker: t })} />

      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading the review queue…
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

      {data ? (
        pending.length === 0 ? (
          <EmptyState title={ticker ? `Nothing waits for review for ${ticker}` : "Nothing waits for review"}>
            Held and quarantined figures appear here after an earnings check.{" "}
            {ticker ? (
              <Link href="/review" className={LINK}>
                Show every holding
              </Link>
            ) : (
              <Link href="/theses" className={LINK}>
                Open the thesis board
              </Link>
            )}
            .
          </EmptyState>
        ) : (
          <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
            <QueueList
              all={pending}
              items={items}
              tier={tier}
              selectedId={selected?.fact.id ?? null}
              onSelect={setSelectedId}
              onTier={(f) => setQuery({ tier: f })}
            />
            {selected ? (
              <FactDetail
                key={selected.fact.id}
                item={selected}
                onResolved={(r) => onResolved(selected, { ...r, ticker: selected.ticker, name: selected.name })}
                onFeedback={() => {
                  reload();
                  stats.reload();
                }}
              />
            ) : (
              <Blueprint className="p-6 text-sm text-text-muted">No figure from {tierFilterLabel(tier).toLowerCase()} waits.</Blueprint>
            )}
          </div>
        )
      ) : null}
    </div>
  );
}

/** The id after `id` in the list (or before it, at the end): where the selection goes once it is decided. */
function nextAfter(ids: readonly number[], id: number): number | null {
  const at = ids.indexOf(id);
  const rest = ids.filter((x) => x !== id);
  if (rest.length === 0) return null;
  return rest[Math.min(Math.max(at, 0), rest.length - 1)];
}

type ReviewStats = components["schemas"]["ReviewStats"];
type StatsState = ReturnType<typeof useApiResource<ReviewStats>>["state"];

function StatTiles({ stats, queue, ticker }: { stats: StatsState; queue: readonly ReviewItem[]; ticker: string | null }) {
  const s = stats.kind === "ok" ? stats.data : stats.kind === "error" ? stats.previous : null;
  const scores = queue.map((i) => i.fact.check_score).filter((v): v is number => v !== null);
  const perDay = s?.reviewed_per_day.map((d) => d.count) ?? [];
  const golden = s?.golden_accuracy ?? null;
  return (
    <section aria-label="Review statistics, last 30 days" className="blueprint grid sm:grid-cols-2 xl:grid-cols-4">
      <BlueprintCorners />
      <div className="px-5 py-4">
        <h2 className="font-sans text-xs font-normal text-text-muted">Awaiting review</h2>
        <p className="num font-heading text-[34px] leading-tight font-semibold text-accent-ink">{s ? s.pending : "—"}</p>
        <p className="text-xs text-text-muted">
          {s ? `${s.blocking_verdict} block${s.blocking_verdict === 1 ? "s" : ""} a verdict` : stats.kind === "error" ? "Statistics unavailable" : "Loading…"}
        </p>
      </div>
      <div className="border-t border-border px-5 py-4 sm:border-t-0 sm:border-l">
        <h2 className="font-sans text-xs font-normal text-text-muted" title="Share of the validator's checks each pending figure passed">
          Checks passed, pending figures{ticker ? ` of ${shortTicker(ticker)}` : ""}
        </h2>
        {scores.length > 0 ? (
          <Histogram
            values={scores}
            thresholds={[0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1]}
            height={70}
            width={300}
            formatEdge={(v) => `${Math.round(v * 100)}%`}
            label="Pending figures by share of checks passed"
            className="mt-1.5"
          />
        ) : (
          <p className="mt-2 text-xs text-text-faint">No pending figure has checks recorded.</p>
        )}
      </div>
      <div className="flex items-end justify-between gap-3 border-t border-border px-5 py-4 xl:border-t-0 xl:border-l">
        <div>
          <h2 className="font-sans text-xs font-normal text-text-muted">Reviewed, 30 days</h2>
          <p className="num font-heading text-[34px] leading-tight font-semibold">{s ? s.reviewed_count : "—"}</p>
          <p className="text-xs text-text-muted">{s ? `${s.marked_wrong} marked wrong` : " "}</p>
        </div>
        <div className="w-[110px] shrink-0">
          {perDay.some((v) => v > 0) ? (
            <SparkArea values={perDay} width={110} height={40} className="h-10" label="Decisions per day, 30 days" formatValue={(v) => String(v)} />
          ) : (
            <p className="text-right text-[11px] text-text-faint">No decision in 30 days</p>
          )}
        </div>
      </div>
      <div className="flex items-center gap-3.5 border-t border-border px-5 py-4 sm:border-l">
        <Ring
          value={golden ? golden.value * 100 : null}
          size={58}
          thickness={6}
          label=""
          ariaLabel="Extraction accuracy on the golden set"
          formatValue={(v) => `${formatNumber(v, 1)}%`}
        />
        <div>
          <h2 className="font-sans text-xs font-normal text-text-muted">Extraction accuracy</h2>
          {golden ? (
            <>
              <p className="num font-heading text-[28px] leading-tight font-semibold">{formatNumber(golden.value * 100, 1)}%</p>
              <p className="text-xs text-text-muted">
                Golden set · {formatNumber(golden.target * 100, 0)}% target
                {golden.evaluated_at ? ` · ${formatDate(new Date(golden.evaluated_at))}` : ""}
              </p>
            </>
          ) : (
            <>
              <p className="font-heading text-[22px] leading-tight font-semibold text-text-muted">Not evaluated yet</p>
              <p className="text-xs text-text-muted">No golden-set run of the extraction is recorded.</p>
            </>
          )}
        </div>
      </div>
    </section>
  );
}

function QueueFilter({
  ticker,
  items,
  onChange,
}: {
  ticker: string | null;
  items: ReviewItem[];
  onChange: (ticker: string | null) => void;
}) {
  const id = useId();
  const tickers = useMemo(() => {
    const names = new Map<string, string | null>();
    for (const i of items) if (!names.has(i.ticker)) names.set(i.ticker, i.name);
    return [...names.entries()];
  }, [items]);

  if (ticker) {
    return (
      <p className="flex flex-wrap items-center gap-2 text-sm">
        Showing <span className="font-mono">{ticker}</span> only.
        <Button variant="outline" size="sm" onClick={() => onChange(null)}>
          <XIcon aria-hidden="true" />
          Show every holding
        </Button>
        <Link href={`/holdings/${encodeURIComponent(ticker)}`} className={LINK}>
          Open the holding page
        </Link>
      </p>
    );
  }
  if (tickers.length < 2) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Label htmlFor={id}>Holding</Label>
      <select id={id} value="" onChange={(e) => onChange(e.target.value || null)} className={SELECT}>
        <option value="">Every holding</option>
        {tickers.map(([t, name]) => (
          <option key={t} value={t}>
            {name ? `${name} (${t})` : t}
          </option>
        ))}
      </select>
    </div>
  );
}

function QueueList({
  all,
  items,
  tier,
  selectedId,
  onSelect,
  onTier,
}: {
  all: ReviewItem[];
  items: ReviewItem[];
  tier: TierFilter;
  selectedId: number | null;
  onSelect: (id: number) => void;
  onTier: (tier: TierFilter) => void;
}) {
  const id = useId();
  const holdings = new Set(items.map((i) => i.ticker)).size;
  return (
    <section aria-labelledby={`${id}-heading`} className="blueprint">
      <BlueprintCorners />
      <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3 text-[13px]">
        <h2 id={`${id}-heading`} className="font-sans text-[13px] font-semibold">
          Oldest first
        </h2>
        <span className="flex items-center gap-1.5 text-text-muted">
          <FilterIcon aria-hidden="true" className="size-3.5" />
          <label htmlFor={`${id}-tier`} className="sr-only">
            Source tier
          </label>
          <select
            id={`${id}-tier`}
            value={String(tier)}
            onChange={(e) => onTier(parseTierFilter(e.target.value))}
            className="border-0 bg-transparent text-[13px] text-text-muted focus-visible:outline-2 focus-visible:outline-focus"
          >
            {tierFilterOptions(all).map((o) => (
              <option key={String(o.filter)} value={String(o.filter)}>
                {tierFilterLabel(o.filter)} ({o.count})
              </option>
            ))}
          </select>
        </span>
      </div>
      <p className="num border-b border-border-soft px-4 py-2 text-xs text-text-muted" role="status">
        {items.length} figure{items.length === 1 ? "" : "s"} wait{items.length === 1 ? "s" : ""} for review across {holdings} holding
        {holdings === 1 ? "" : "s"}
        {tier !== "all" ? ` (${tierFilterLabel(tier).toLowerCase()} of ${all.length})` : ""}.
      </p>
      {items.length === 0 ? (
        <p className="px-4 py-6 text-center text-xs text-text-faint">Nothing in {tierFilterLabel(tier).toLowerCase()}.</p>
      ) : (
        <ul aria-label="Figures to review; J and K move">
          {items.map((item) => {
            const f = item.fact;
            const active = f.id === selectedId;
            return (
              <li key={f.id}>
                <button
                  id={`queue-row-${f.id}`}
                  type="button"
                  aria-current={active ? "true" : undefined}
                  onClick={() => onSelect(f.id)}
                  className={cn(
                    "flex w-full flex-col gap-2 border-b border-l-[3px] border-b-border-soft px-4 py-3.5 text-left hover:bg-border-soft focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-focus",
                    active ? "border-l-accent bg-accent-soft" : "border-l-transparent",
                  )}
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="font-mono text-xs font-medium" title={item.ticker}>
                      {shortTicker(item.ticker)} · {f.period}
                    </span>
                    <ConfidenceBadge confidence={f.confidence} />
                  </span>
                  <span className="flex items-baseline justify-between gap-3">
                    <span className="min-w-0 truncate text-[15px] font-medium">
                      {humanizeMetric(f.metric)}
                      {f.basis === "adjusted" ? <span className="font-normal text-text-muted"> (adj.)</span> : null}
                    </span>
                    <span className="num shrink-0 font-heading text-lg font-semibold">{formatFactValue(f)}</span>
                  </span>
                  <span className="grid grid-cols-[1fr_auto] items-center gap-2">
                    <Meter value={f.check_score} size="sm" label={`${humanizeMetric(f.metric)}: checks passed`} formatValue={(v) => `${Math.round(v * 100)}% of checks passed`} />
                    <span className="num text-[11px] text-text-muted" aria-hidden="true">
                      {f.check_score === null ? "no checks" : `${Math.round(f.check_score * 100)}% checks`}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function useHolding(ticker: string): HoldingDetail | null {
  const [detail, setDetail] = useState<{ ticker: string; data: HoldingDetail | null } | null>(null);
  useEffect(() => {
    let cancelled = false;
    getBrowserApiClient()
      .GET("/holdings/{ticker}", { params: { path: { ticker } } })
      .then(({ data }) => {
        if (!cancelled) setDetail({ ticker, data: data ?? null });
      })
      .catch(() => {
        if (!cancelled) setDetail({ ticker, data: null });
      });
    return () => {
      cancelled = true;
    };
  }, [ticker]);
  return detail?.ticker === ticker ? detail.data : null;
}

function FactDetail({
  item,
  onResolved,
  onFeedback,
}: {
  item: ReviewItem;
  onResolved: Parameters<typeof ReviewActionBar>[0]["onResolved"];
  onFeedback: () => void;
}) {
  const { fact } = item;
  const holding = useHolding(item.ticker);
  const feeds = holding ? feedsKpis(fact, holding.kpis.map((k) => k.kpi)) : [];
  const history = holding ? historyAgainstClaim(fact, holding.facts) : [];
  const filed = filedCorrection(fact, item.reconciliation);
  const mismatch = item.reconciliation.find((r) => r.outcome === "mismatch" && r.xbrl_value !== null);
  const passed = fact.checks.filter((c) => c.ok).length;
  const titleId = `fact-${fact.id}-title`;
  const name = item.name ?? item.ticker;

  return (
    <article aria-labelledby={titleId} className="blueprint flex min-w-0 flex-col gap-5 px-5 py-6 md:px-[26px]">
      <BlueprintCorners />
      <header className="flex flex-wrap items-start justify-between gap-5">
        <div className="min-w-0 flex-1 basis-[20rem] space-y-1">
          <p className="font-mono text-[13px] text-text-muted">
            <Link href={`/holdings/${encodeURIComponent(item.ticker)}`} className="hover:text-text hover:underline" title={name}>
              {shortTicker(item.ticker)}
            </Link>{" "}
            · {fact.period} · fact #{fact.id}
          </p>
          <h2 id={titleId} className="text-[28px] leading-tight font-semibold md:text-[34px]">
            {humanizeMetric(fact.metric)}
            {fact.basis === "adjusted" ? " (adjusted)" : ""}: <span className="num">{formatFactValue(fact)}</span>
          </h2>
          <p className="text-sm text-text-muted">
            {holding === null ? (
              "Looking up the thesis KPIs it feeds…"
            ) : feeds.length > 0 ? (
              <>
                Feeds KPI{feeds.length === 1 ? "" : "s"}{" "}
                {feeds.map((k, i) => (
                  <span key={k.id}>
                    {i > 0 ? ", " : ""}&ldquo;{humanizeMetric(k.metric)} {formatTarget(k.operator, k.threshold, k.unit)}&rdquo;
                  </span>
                ))}{" "}
                in the {shortTicker(item.ticker)} thesis.
              </>
            ) : (
              <>Feeds no KPI of the {shortTicker(item.ticker)} thesis directly.</>
            )}
          </p>
          <p className="flex flex-wrap items-center gap-2 pt-1 text-xs text-text-muted">
            <HeldBadge status={fact.status} />
            <span>{item.why}</span>
            <span>· extracted {formatDate(new Date(fact.extracted_at))}</span>
            <ConfidenceBadge confidence={fact.confidence} />
            <ProvenanceTag provenance={factProvenance(fact)} />
            <FeedbackControl
              target={{ kind: "fact", id: fact.id }}
              label={`figure ${humanizeMetric(fact.metric)} ${fact.period}`}
              valueHint={scaleHint(fact)}
            />
          </p>
          <p className="text-xs text-text-muted">
            As printed <span className="num font-mono text-text">{fact.value_as_written}</span>
            {fact.scale !== "units" ? ` · in ${fact.scale}` : ""}
            {fact.currency ? ` · ${fact.currency}` : ""}
          </p>
        </div>
        <div className="w-[160px] shrink-0 text-center">
          <Gauge
            value={fact.check_score}
            max={1}
            formatValue={(v) => `${Math.round(v * 100)}%`}
            tone={fact.check_score !== null && fact.check_score < 1 ? "warning" : "accent"}
            ariaLabel="Checks passed"
          />
          <p className="text-[11px] text-text-muted">
            Checks passed
            <span className="num block">
              {fact.checks.length > 0 ? `${passed} of ${fact.checks.length} validator checks` : "No check ran"}
            </span>
          </p>
        </div>
      </header>

      <div className="grid gap-5 xl:grid-cols-2">
        <section aria-label="Evidence" className="flex min-w-0 flex-col gap-2.5">
          <p className="kicker">Evidence</p>
          <Quote snippet={fact.snippet} value={fact.value_as_written} />
          {fact.unit_snippet ? <p className="text-xs text-text-muted">Unit: {fact.unit_snippet}</p> : null}
          <p className="flex flex-wrap items-center gap-2 text-[13px]">
            <FileTextIcon aria-hidden="true" className="size-4 text-text-muted" />
            <EvidenceLinks fact={fact} showHost />
            <span className="text-text-muted">
              · {fact.source_tier !== null ? `tier ${fact.source_tier} source` : "source host has no tier"}
            </span>
          </p>
          {mismatch ? (
            <p className="flex gap-2 bg-warning-signal-subtle px-3 py-2.5 text-[13px] text-text">
              <TriangleAlertIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warning-signal" />
              <span>
                Differs from the filing{mismatch.form ? ` (${mismatch.form})` : ""}, which has{" "}
                <span className="num font-medium">{formatInFactScale(mismatch.xbrl_value, fact)}</span>: this figure is{" "}
                <span className="num">{formatInFactScale(mismatch.difference, fact, { signed: true })}</span> off.
                {mismatch.detail ? ` ${mismatch.detail}` : ""}
              </span>
            </p>
          ) : null}
          <FailedChecks item={item} />
        </section>

        <section aria-label="Against history" className="flex min-w-0 flex-col gap-2.5">
          <p className="kicker">Against history</p>
          {holding === null ? (
            <p className="text-xs text-text-faint">Loading the stored figures…</p>
          ) : history.length === 0 ? (
            <p className="border border-dashed border-border px-4 py-8 text-center text-xs text-text-faint">
              No other stored figure of {humanizeMetric(fact.metric).toLowerCase()} to compare with.
            </p>
          ) : (
            <>
              <BarChart
                data={history.map((b) => ({ label: b.label, value: b.value, claim: b.claim, highlight: !b.claim && b === history[history.length - 2] }))}
                height={190}
                formatValue={(v) => formatNumber(v, Math.abs(v) < 100 ? 1 : 0)}
                label={`${humanizeMetric(fact.metric)} by period, in ${fact.scale === "units" ? "the printed unit" : fact.scale}, this claim last`}
              />
              <Legend
                items={[
                  { label: "Stored figures", className: "bg-accent-300" },
                  { label: "This claim", swatch: "dashed", className: "border-accent" },
                ]}
              />
            </>
          )}
        </section>
      </div>

      {fact.restates_fact_id !== null ? <Restatement fact={fact} restates={item.restates} /> : null}
      {item.reconciliation.length > 0 ? <Reconciliation fact={fact} rows={item.reconciliation} /> : null}

      <ReviewActionBar fact={fact} filed={filed} hotkeys onResolved={onResolved} onFeedback={onFeedback} />
      <p className="text-[11px] text-text-faint">
        {fact.reviewed_at ? `Last reviewed ${formatDateTime(fact.reviewed_at)}. ` : ""}Held figures stay in the{" "}
        <Link href={reviewQueueHref(item.ticker)} className={LINK}>
          {shortTicker(item.ticker)} queue
        </Link>{" "}
        until decided.
      </p>
    </article>
  );
}

/** The source quote in serif with the printed value highlighted where it appears. */
function Quote({ snippet, value }: { snippet: string; value: string }) {
  const parts = highlightValue(snippet, value);
  return (
    <blockquote className="border-l-[3px] border-accent bg-accent-soft px-3.5 py-2.5 font-serif text-base leading-normal break-words">
      &ldquo;
      {parts ? (
        <>
          {parts.before}
          <mark className="bg-accent px-0.5 text-accent-foreground">{parts.match}</mark>
          {parts.after}
        </>
      ) : (
        snippet
      )}
      &rdquo;
    </blockquote>
  );
}
