"use client";

import { ChevronRightIcon, LoaderCircleIcon, RefreshCwIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { BarChart, ChartEmpty, Legend, SrTable } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { AsOfLabel } from "@/components/freshness/as-of-label";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import { EscalationActions, EscalationStateBadge, EscalationStatusBadge } from "@/components/news/escalation-status-badge";
import { SeverityBadge } from "@/components/news/severity-badge";
import { PageHeader } from "@/components/page-header";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type EscalationOut, type Holding } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { formatCost } from "@/lib/jobs/job-detail";
import {
  ackTimes,
  ageLabel,
  ESCALATION_STATE_LABELS,
  escalationHref,
  formatMinutes,
  formatMovePct,
  parsePriceMove,
  parseStatusTab,
  replaceEscalation,
  thesisLink,
  TRIGGER_LABELS,
  weeklyTotals,
  type StatusTab,
} from "@/lib/news/escalation";
import { eventLabel } from "@/lib/news/labels";
import { shortDay } from "@/lib/news/stats";
import { useNow } from "@/lib/hooks/use-now";
import { cn } from "@/lib/utils";

type EscalationStats = components["schemas"]["EscalationStats"];
type Load = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; items: EscalationOut[] };

const STATS_DAYS = 90;
const TABS: StatusTab[] = ["open", "acknowledged", "resolved", "all"];

/** Escalations (impact notes on confirmed severity-2+ stories and large price moves) and how fast they are acknowledged. */
export function EscalationsView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const ticker = params.get("ticker");
  const tab = parseStatusTab(params.get("status"));
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [stats, setStats] = useState<EscalationStats | null>(null);
  const [acked, setAcked] = useState<EscalationOut[] | null>(null);
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [reloadKey, setReloadKey] = useState(0);
  const now = useNow();

  useEffect(() => {
    let cancelled = false;
    getBrowserApiClient()
      .GET("/escalations", {
        params: { query: { ticker: ticker ?? undefined, limit: 100, status: tab === "all" ? undefined : tab } },
      })
      .then(({ data, response }) => {
        if (cancelled) return;
        if (data) setLoad({ kind: "ok", items: data });
        else setLoad({ kind: "error", message: `The API answered HTTP ${response.status}.` });
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoad({ kind: "error", message: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [ticker, tab, reloadKey]);

  const loadStats = useCallback(() => {
    const api = getBrowserApiClient();
    api
      .GET("/escalations/stats", { params: { query: { days: STATS_DAYS } } })
      .then(({ data }) => setStats(data ?? null))
      .catch(() => setStats(null));
    // Every escalation (any status) for the time-to-acknowledge bars.
    api
      .GET("/escalations", { params: { query: { limit: 100 } } })
      .then(({ data }) => setAcked(data ?? []))
      .catch(() => setAcked([]));
  }, []);

  useEffect(() => {
    loadStats();
  }, [loadStats, reloadKey]);

  useEffect(() => {
    getBrowserApiClient()
      .GET("/portfolio/holdings")
      .then((r) => r.data && setHoldings(r.data.holdings))
      .catch(() => undefined);
  }, []);

  const replaceQuery = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(patch)) {
      if (v === null) next.delete(k);
      else next.set(k, v);
    }
    const text = next.toString();
    router.replace(`${pathname}${text ? `?${text}` : ""}`, { scroll: false });
  };

  function onUpdated(updated: EscalationOut) {
    setLoad((l) =>
      l.kind !== "ok"
        ? l
        : {
            ...l,
            // Leaves the tab it no longer belongs to.
            items: tab === "all" || updated.status === tab ? replaceEscalation(l.items, updated) : l.items.filter((e) => e.id !== updated.id),
          },
    );
    loadStats();
  }

  const count = (t: StatusTab) =>
    !stats ? null : t === "all" ? stats.open + stats.acknowledged + stats.resolved : stats[t];

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Escalations"
        description="What needs your attention: an impact note for each confirmed severity-2+ story and each large price move, with the thesis KPIs or exit conditions it touches."
        aside={
          <>
            {load.kind === "ok" && load.items.length > 0 ? (
              <AsOfLabel at={load.items[0].created_at} kind="escalation" prefix="Latest note written" />
            ) : null}
            <Button variant="ghost" size="sm" onClick={() => setReloadKey((k) => k + 1)}>
              <RefreshCwIcon aria-hidden="true" />
              Reload
            </Button>
          </>
        }
      />

      <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-[minmax(0,1.75fr)_minmax(0,1fr)]">
        <Blueprint aria-labelledby="escalations-heading">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
            <h2 id="escalations-heading" className="text-[22px] leading-tight font-semibold">
              Escalations
            </h2>
            <nav aria-label="Status" className="flex flex-wrap border border-border">
              {TABS.map((t) => {
                const n = count(t);
                return (
                  <button
                    key={t}
                    type="button"
                    aria-pressed={tab === t}
                    onClick={() => replaceQuery({ status: t === "open" ? null : t })}
                    className={cn(
                      "border-l border-border px-3 py-1.5 text-xs first:border-l-0",
                      tab === t ? "bg-accent text-accent-foreground" : "text-text hover:bg-surface-muted",
                    )}
                  >
                    {t === "all" ? "All" : ESCALATION_STATE_LABELS[t]}
                    {n !== null ? <span className="num"> · {n}</span> : null}
                  </button>
                );
              })}
            </nav>
          </div>
          <div className="flex flex-wrap items-center gap-3 border-b border-border-soft px-5 py-2.5">
            <label className="flex min-w-0 items-center gap-2 text-sm">
              <span className="kicker">Holding</span>
              <select
                value={ticker ?? ""}
                onChange={(e) => replaceQuery({ ticker: e.target.value || null })}
                className="h-8 min-w-0 max-w-full border border-border bg-surface-raised px-2 text-sm text-text"
              >
                <option value="">All holdings</option>
                {ticker && !holdings.some((h) => h.ticker === ticker) ? <option value={ticker}>{ticker}</option> : null}
                {holdings.map((h) => (
                  <option key={h.ticker} value={h.ticker}>
                    {h.name ?? h.ticker}
                  </option>
                ))}
              </select>
            </label>
            {ticker ? (
              <Button variant="ghost" size="sm" onClick={() => replaceQuery({ ticker: null })}>
                <XIcon aria-hidden="true" />
                All holdings
              </Button>
            ) : null}
          </div>

          {load.kind === "loading" ? (
            <p className="flex items-center gap-2 px-5 py-4 text-sm text-text-muted" role="status">
              <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
              Loading escalations…
            </p>
          ) : null}
          {load.kind === "error" ? (
            <div className="p-5">
              <AlertMessage title="Could not load the escalations">{load.message}</AlertMessage>
            </div>
          ) : null}
          {load.kind === "ok" && load.items.length === 0 ? (
            <div className="p-5">
              <EmptyState
                title={`No ${tab === "all" ? "" : `${ESCALATION_STATE_LABELS[tab].toLowerCase()} `}escalations${ticker ? ` for ${ticker}` : ""}`}
              >
                A note is written when a story of severity 2 or more is confirmed, or when a holding moves beyond the
                price-move threshold in Config: Event rules.
              </EmptyState>
            </div>
          ) : null}
          {load.kind === "ok" && load.items.length > 0 ? (
            <ul className="m-0 list-none p-0">
              {load.items.map((e) => (
                <EscalationRow key={e.id} escalation={e} now={now} onUpdated={onUpdated} />
              ))}
            </ul>
          ) : null}
        </Blueprint>

        <div className="flex flex-col gap-5">
          <AckPanel stats={stats} items={acked} />
          <WeeklyPanel stats={stats} />
        </div>
      </div>
    </div>
  );
}

function EscalationRow({ escalation: e, now, onUpdated }: { escalation: EscalationOut; now: Date; onUpdated: (u: EscalationOut) => void }) {
  const link = thesisLink(e.body);
  const move = parsePriceMove(e.body.price_move);
  const sev = e.body.severity;
  return (
    <li className="border-b border-border-soft last:border-0">
      <div className="grid grid-cols-[3rem_4px_minmax(0,1fr)] items-start gap-x-4 gap-y-1 px-5 py-3.5 hover:bg-surface-muted md:grid-cols-[3.5rem_4px_6.5rem_minmax(0,1fr)_auto_auto]">
        <span className="num pt-0.5 font-mono text-xs text-text-muted">E-{e.id}</span>
        <span
          aria-hidden="true"
          className={cn("h-6 w-1", sev === 3 ? "bg-loss" : sev === 2 ? "bg-accent" : "bg-border")}
        />
        <span className="hidden pt-0.5 font-mono text-xs font-semibold md:block">{e.ticker ?? "—"}</span>
        <div className="min-w-0">
          <Link href={escalationHref(e.id)} className="text-[15px] font-medium text-text hover:text-accent-ink hover:underline">
            {e.body.headline || e.title}
          </Link>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-text-muted">
            {e.ticker ? <span className="font-mono md:hidden">{e.ticker}</span> : null}
            {sev !== null ? <SeverityBadge severity={sev} /> : null}
            <EscalationStatusBadge body={e.body} />
            <span>
              {TRIGGER_LABELS[e.body.trigger]}
              {e.body.trigger === "story" ? ` · ${eventLabel(e.body.event_type)}` : ""}
            </span>
            {move ? <span className="num">{formatMovePct(move.movePct)} on {move.date}</span> : null}
          </p>
          <details className="group mt-1 text-xs text-text-muted">
            <summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden">
              <span className="group-open:hidden">
                {link.kind === "none"
                  ? link.text
                  : `Touches ${[...link.kpis.map((k) => `KPI ${k.id}`), ...link.exitConditions.map(() => "an exit condition")].join(", ")}`}
                {" · "}
                <span className="num">{e.cost_usd === null ? "no cost recorded" : formatCost(e.cost_usd)}</span>
                <span className="text-accent-ink"> · run details</span>
              </span>
              <span className="hidden text-accent-ink group-open:inline">Hide run details</span>
            </summary>
            <p className="mt-1">
              {link.kind === "none"
                ? link.text
                : `Touches ${[...link.kpis.map((k) => `KPI ${k.id}`), ...link.exitConditions.map(() => "an exit condition")].join(", ")}`}
            </p>
            <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
              {e.job_status ? (
                <span className="inline-flex items-center gap-1">
                  run <span className="num">#{e.job_id}</span> <JobStatusBadge status={e.job_status} />
                </span>
              ) : null}
              <span className="num">cost {e.cost_usd === null ? "none recorded" : formatCost(e.cost_usd)}</span>
              <span className="font-mono text-xs">{e.models.length > 0 ? e.models.join(", ") : "no model called"}</span>
            </p>
          </details>
          <EscalationActions escalation={e} onUpdated={onUpdated} className="mt-2 md:hidden" />
        </div>
        <span className="hidden items-center gap-3 md:flex">
          <EscalationActions escalation={e} onUpdated={onUpdated} className="justify-end" />
          <EscalationStateBadge status={e.status} />
        </span>
        <span className="col-start-3 flex items-center gap-1 pt-0.5 md:col-start-auto">
          <span className="md:hidden">
            <EscalationStateBadge status={e.status} />
          </span>
          <time dateTime={e.created_at} className="num text-xs text-text-muted" title={new Date(e.created_at).toLocaleString()}>
            {ageLabel(e.created_at, now)}
          </time>
          <Link href={escalationHref(e.id)} aria-label={`Open escalation E-${e.id}`} className="text-text-muted hover:text-text">
            <ChevronRightIcon aria-hidden="true" className="size-4" />
          </Link>
        </span>
      </div>
    </li>
  );
}

function WeeklyPanel({ stats }: { stats: EscalationStats | null }) {
  const weeks = stats ? weeklyTotals(stats.per_day) : [];
  return (
    <Blueprint aria-labelledby="weekly-heading" className="flex flex-col gap-3 px-5 py-5">
      <PanelHeading kicker="Per week" title={<span id="weekly-heading">Opened and acknowledged</span>} />
      {weeks.length === 0 ? (
        <ChartEmpty label={stats ? "Not a full week of data yet" : "Loading…"} minHeight={90} />
      ) : (
        <>
          <Legend
            items={[
              { label: "Opened", className: "bg-accent" },
              { label: "Acknowledged", className: "bg-accent-300" },
            ]}
          />
          <div aria-hidden="true" className="flex h-24 items-end gap-1">
            {weeks.map((w) => {
              const max = Math.max(1, ...weeks.map((x) => Math.max(x.opened, x.acknowledged)));
              return (
                <div key={w.start} className="flex h-full flex-1 items-end gap-px" title={`Week of ${w.start}: ${w.opened} opened, ${w.acknowledged} acknowledged`}>
                  <span className="flex-1 bg-accent" style={{ height: `${(w.opened / max) * 100}%` }} />
                  <span className="flex-1 bg-accent-300" style={{ height: `${(w.acknowledged / max) * 100}%` }} />
                </div>
              );
            })}
          </div>
          <div aria-hidden="true" className="flex justify-between font-mono text-xs text-text-faint">
            <span>{shortDay(new Date(`${weeks[0].start}T12:00:00Z`))}</span>
            <span>{shortDay(new Date(`${weeks[weeks.length - 1].start}T12:00:00Z`))}</span>
          </div>
          <div className="relative overflow-hidden">
            <SrTable
              caption="Escalations opened and acknowledged per week"
            columns={["Week of", "Opened", "Acknowledged"]}
              rows={weeks.map((w) => [w.start, w.opened, w.acknowledged])}
            />
          </div>
        </>
      )}
    </Blueprint>
  );
}

function AckPanel({ stats, items }: { stats: EscalationStats | null; items: EscalationOut[] | null }) {
  const times = items ? ackTimes(items) : [];
  const opened = stats ? stats.per_day.reduce((n, d) => n + d.opened, 0) : null;
  return (
    <Blueprint aria-labelledby="ack-heading" className="flex flex-col gap-4 px-5 py-5">
      <PanelHeading
        kicker={`Time to acknowledge, ${STATS_DAYS} days`}
        title={
          <span id="ack-heading" className="num">
            {stats?.median_minutes_to_ack != null ? `median ${formatMinutes(stats.median_minutes_to_ack)}` : "No acknowledgements yet"}
          </span>
        }
      />
      {times.length > 0 ? (
        <BarChart
          data={times.map((t, i) => ({ label: shortDay(new Date(t.createdAt)), value: t.minutes / 60, highlight: i === times.length - 1 }))}
          threshold={
            stats?.median_minutes_to_ack != null
              ? { value: stats.median_minutes_to_ack / 60, label: `Median ${formatMinutes(stats.median_minutes_to_ack)}` }
              : undefined
          }
          valueLabels={false}
          xLabels={times.length <= 12}
          height={140}
          formatValue={(v) => `${v.toFixed(1)} h`}
          label="Hours from written to acknowledged, per escalation, oldest first"
        />
      ) : (
        <ChartEmpty label="Acknowledge an escalation to start this chart" minHeight={110} />
      )}
      <p className="text-xs text-text-muted">
        One bar per acknowledged escalation (of the latest 100), in hours from written to first acknowledged
        {stats ? `; the median covers those acknowledged in the last ${stats.days} days (${stats.timezone})` : ""}.
        {opened !== null ? (
          <>
            {" "}
            <span className="num">{opened}</span> opened in the window.
          </>
        ) : null}
      </p>
    </Blueprint>
  );
}
