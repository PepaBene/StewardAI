"use client";

import { LayersIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EntityName } from "@/components/entity-name";
import { KpiStrip } from "@/components/insight/kpi-tile";
import { SideDrawer } from "@/components/insight/side-drawer";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FilterChips } from "@/components/ui/filter-chips";
import { getBrowserApiClient } from "@/lib/api/client";
import {
  ageTone,
  countByKind,
  groupQueue,
  INBOX_KINDS,
  isInboxKind,
  KIND_LABELS,
  toQueueRows,
  type InboxKind,
  type QueueEntry,
  type QueueRow,
  type QueueSources,
} from "@/lib/inbox-queue";
import { parseAiDecisions } from "@/lib/reference/ai-decisions";
import { cn } from "@/lib/utils";

import { AiDecisionsView } from "../ai-decisions/ai-decisions-view";
import { AdjudicationPageView } from "../disclosures/adjudication/adjudication-view";
import { AlertsPageView } from "../disclosures/alerts/alerts-view";
import { PartiesPageView } from "../disclosures/deals/parties/parties-view";
import { EscalationView } from "../escalations/[id]/escalation-view";
import { EscalationsView } from "../escalations/escalations-view";
import { DisputedView } from "../news/disputed/disputed-view";
import { RelationshipsView } from "../relationships/relationships-view";
import { ReviewView } from "../review/review-view";
import { InboxView } from "./inbox-view";
import { ReviewMode } from "./review-mode";
import { StoppedJobsView } from "./stopped-jobs-view";

/*
 * The Inbox (plan §5.6, R3): one place for everything that waits on the owner. "All" reads
 * every queue at once and lists it by company; picking a queue shows that queue's own full
 * view underneath, so nothing the old pages could do is lost.
 */

type Filter = "all" | InboxKind;

const FULL_VIEWS: Record<InboxKind, () => ReactNode> = {
  proposals: () => <InboxView />,
  "needs-you": () => null,
  escalations: () => <EscalationsView />,
  figures: () => <ReviewView />,
  relationships: () => <RelationshipsView />,
  disputed: () => <DisputedView />,
  "disclosure-alerts": () => <AlertsPageView />,
  "deal-parties": () => <PartiesPageView />,
  "ai-decisions": () => <AiDecisionsView />,
  "stopped-jobs": () => <StoppedJobsView />,
  accuracy: () => <AdjudicationPageView />,
};

type SourceName = keyof QueueSources | "summary";
const SOURCE_LABELS: Record<SourceName, string> = {
  proposals: "proposals",
  needsYou: "autopilot asks",
  escalations: "impact notes",
  figures: "figures to check",
  relationships: "relationships",
  disputed: "disputed stories",
  alerts: "filing alerts",
  parties: "deal parties",
  aiDecisions: "AI decisions",
  failedJobs: "stopped jobs",
  summary: "autopilot summary",
};

type QueueLoad = {
  loading: boolean;
  sources: QueueSources;
  failed: SourceName[];
  handled: number | null;
  handledDays: number | null;
};

function useQueues(reloadKey: number): QueueLoad {
  const [state, setState] = useState<QueueLoad>({ loading: true, sources: {}, failed: [], handled: null, handledDays: null });
  useEffect(() => {
    let cancelled = false;
    const api = getBrowserApiClient();
    // Each queue is read on its own: one failing never hides the others.
    const calls = {
      proposals: api.GET("/inbox", { params: { query: { status: "new", limit: 100 } } }).then((r) => r.data?.items),
      needsYou: api.GET("/autopilot/needs-you").then((r) => r.data),
      escalations: api.GET("/escalations", { params: { query: { status: "open", limit: 100 } } }).then((r) => r.data),
      figures: api.GET("/facts/review-queue").then((r) => r.data),
      relationships: api.GET("/relationship-review/queue").then((r) => r.data),
      disputed: api.GET("/stories/disputed", { params: { query: { include_resolved: false } } }).then((r) => r.data),
      alerts: api.GET("/disclosures/alerts", { params: { query: { include_acknowledged: false } } }).then((r) => r.data),
      parties: api.GET("/disclosures/deals/parties/pending").then((r) => r.data),
      aiDecisions: api
        .GET("/approvals/ai-summary", { params: { query: { days: 7 } } })
        .then((r) => (r.data === undefined ? undefined : parseAiDecisions(r.data))),
      failedJobs: api.GET("/jobs", { params: { query: { status: ["failed"], unresolved: true, limit: 100 } } }).then((r) => r.data?.jobs),
      summary: api.GET("/autopilot/summary").then((r) => r.data),
    };
    const names = Object.keys(calls) as SourceName[];
    Promise.allSettled(Object.values(calls)).then((results) => {
      if (cancelled) return;
      const sources: Record<string, unknown> = {};
      const failed: SourceName[] = [];
      results.forEach((res, i) => {
        if (res.status === "fulfilled" && res.value != null) sources[names[i]] = res.value;
        else failed.push(names[i]);
      });
      const summary = sources.summary as { days: number; kinds: { applied: number }[] } | undefined;
      delete sources.summary;
      setState({
        loading: false,
        sources: sources as QueueSources,
        failed,
        handled: summary ? summary.kinds.reduce((n, k) => n + k.applied, 0) : null,
        handledDays: summary?.days ?? null,
      });
    });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);
  return state;
}

export function QueueView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const kindParam = params.get("kind");
  const filter: Filter = isInboxKind(kindParam) && kindParam !== "needs-you" ? kindParam : "all";
  const escalationId = filter === "escalations" ? Number(params.get("item")) : NaN;
  const [reloadKey, setReloadKey] = useState(0);
  const [reviewing, setReviewing] = useState(false);
  const load = useQueues(reloadKey);
  // Fixed at load time so ages do not tick while the page is open.
  const [now] = useState(() => Date.now());

  const allRows = useMemo(() => toQueueRows(load.sources, now), [load.sources, now]);
  const counts = useMemo(() => countByKind(allRows), [allRows]);
  // The AI's decisions are already applied: open to overturn, but not waiting on anyone.
  const rows = useMemo(() => allRows.filter((r) => r.kind !== "ai-decisions"), [allRows]);
  const oldest = rows.reduce<number | null>((m, r) => (r.ageDays !== null && (m === null || r.ageDays > m) ? r.ageDays : m), null);

  const go = (next: Filter, extra?: string) => {
    const q = next === "all" ? "" : `?kind=${next}${extra ?? ""}`;
    router.replace(`${pathname}${q}`, { scroll: false });
  };

  // Chips for queues with something waiting (and the open one); accuracy is per dataset, so it
  // has no count and always shows.
  const chipKinds = INBOX_KINDS.filter((k) => k !== "needs-you" && (k === "accuracy" || counts[k] > 0 || k === filter));
  const emptyQueues = INBOX_KINDS.filter((k) => k !== "accuracy" && !chipKinds.includes(k) && counts[k] === 0);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Inbox"
        description="Everything waiting for your decision, oldest first. Nothing here runs until you confirm it."
        aside={
          <>
            {rows.length > 0 ? (
              <Button variant="outline" size="sm" onClick={() => setReviewing(true)}>
                <LayersIcon aria-hidden="true" />
                Review one at a time
              </Button>
            ) : null}
            <Button variant="ghost" size="sm" onClick={() => setReloadKey((k) => k + 1)}>
              <RefreshCwIcon aria-hidden="true" />
              Reload
            </Button>
          </>
        }
      />
      <ReviewMode rows={rows} open={reviewing} onClose={() => setReviewing(false)} />

      <KpiStrip
        label="Inbox summary"
        tiles={[
          { label: "Waiting for you", value: load.loading ? "–" : rows.length },
          {
            label: "Oldest item",
            value: load.loading || oldest === null ? "–" : `${oldest} d`,
            signed: ageTone(oldest) === "loss" ? "loss" : undefined,
            note: oldest !== null && oldest >= 7 ? "Waiting more than a week" : undefined,
          },
          { label: "Open impact notes", value: load.loading ? "–" : counts.escalations },
          {
            label: "Handled automatically",
            value: load.handled ?? "–",
            note: (
              <Link href="/engine-ledger" className="underline-offset-2 hover:underline">
                {load.handledDays ? `Last ${load.handledDays} days, see Autonomy` : "See Autonomy"}
              </Link>
            ),
          },
        ]}
      />

      <div className="flex flex-col gap-2">
        <FilterChips<Filter>
          label="Queues"
          value={filter}
          onChange={(k) => go(k)}
          chips={[
            { key: "all", label: "All", count: load.loading ? undefined : rows.length },
            ...chipKinds.map((k) => ({
              key: k,
              label: KIND_LABELS[k],
              count: k === "accuracy" || load.loading ? undefined : counts[k],
            })),
          ]}
        />
        {!load.loading && emptyQueues.length > 0 ? (
          <p className="text-[13px] text-text-muted" title={emptyQueues.map((k) => KIND_LABELS[k]).join(", ")}>
            Nothing waiting in {emptyQueues.length} other {emptyQueues.length === 1 ? "queue" : "queues"}.
          </p>
        ) : null}
      </div>

      {load.failed.length > 0 ? (
        <AlertMessage title="Some queues could not be read">
          Not shown: {load.failed.map((s) => SOURCE_LABELS[s]).join(", ")}. Reload to try again.
        </AlertMessage>
      ) : null}

      {filter === "all" ? (
        <AllQueues rows={rows} loading={load.loading} />
      ) : (
        <div className="embedded-view">{FULL_VIEWS[filter]()}</div>
      )}

      <SideDrawer
        open={Number.isInteger(escalationId) && escalationId > 0}
        onClose={() => go("escalations")}
        title="Impact note"
        fullPageHref={undefined}
        tabs={[
          {
            key: "note",
            label: "Note",
            content: Number.isInteger(escalationId) && escalationId > 0 ? <EscalationView id={escalationId} /> : null,
          },
        ]}
        tab="note"
        onTab={() => undefined}
      />
    </div>
  );
}

function AllQueues({ rows, loading }: { rows: readonly QueueRow[]; loading: boolean }) {
  const { batches, companies } = useMemo(() => groupQueue(rows), [rows]);
  if (loading) {
    return (
      <div aria-busy="true" className="flex flex-col gap-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-24 animate-pulse rounded-card bg-surface" />
        ))}
      </div>
    );
  }
  if (rows.length === 0) {
    return (
      <section className="blueprint px-6 py-10 text-center">
        <h2 className="font-heading text-[22px] font-semibold">Nothing is waiting for you</h2>
        <p className="mx-auto mt-1.5 max-w-prose text-sm text-text-muted">
          Every queue is clear. New proposals, impact notes and figures to check land here as they come in.
        </p>
      </section>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      {batches.map((b) => (b.type === "batch" ? <BatchCard key={b.key} entry={b} /> : null))}
      {companies.map((g) => (
        <section key={g.ticker ?? "none"} className="blueprint overflow-hidden" aria-label={g.ticker ?? "Not about one company"}>
          <header className="flex items-baseline justify-between gap-3 px-5 pt-4 pb-2">
            <h2 className="panel-title">
              {g.ticker ? <EntityName id={g.ticker} name={g.name} chip={false} /> : "Not about one company"}
            </h2>
            {g.ticker ? (
              <Link href={`/companies/${encodeURIComponent(g.ticker)}`} className="text-[13px] text-text-muted hover:text-text">
                Company page
              </Link>
            ) : null}
          </header>
          <ul>
            {g.entries.map((e) => (e.type === "row" ? <QueueRowItem key={e.row.id} row={e.row} /> : null))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function AgeLabel({ days }: { days: number | null }) {
  if (days === null) return null;
  const tone = ageTone(days);
  return (
    <span className={cn("num text-xs whitespace-nowrap", tone === "loss" ? "text-loss" : tone === "warning" ? "text-warning-signal" : "text-text-faint")}>
      {days === 0 ? "today" : `${days} d`}
    </span>
  );
}

function QueueRowItem({ row }: { row: QueueRow }) {
  const tone = ageTone(row.ageDays);
  return (
    <li className="relative border-t border-border-soft first:border-t-0">
      {/* The ageing edge: only rows that have waited a week or more carry one. */}
      {tone !== "none" ? (
        <span
          aria-hidden="true"
          className={cn("absolute top-3 bottom-3 left-0 w-[3px] rounded-r-sm", tone === "loss" ? "bg-loss" : "bg-warning-signal")}
        />
      ) : null}
      <Link href={row.href} className="group grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-4 px-5 py-3 hover:bg-surface-muted">
        <div className="min-w-0">
          <p className="text-[15px] leading-snug text-text">{row.headline}</p>
          {row.why ? <p className="mt-0.5 line-clamp-2 text-[13px] text-text-muted">{row.why}</p> : null}
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <Badge variant="kind">{KIND_LABELS[row.kind]}</Badge>
          <AgeLabel days={row.ageDays} />
        </div>
        {tone !== "none" ? <span className="sr-only">Waiting {row.ageDays} days</span> : null}
      </Link>
    </li>
  );
}

function BatchCard({ entry }: { entry: Extract<QueueEntry, { type: "batch" }> }) {
  const [open, setOpen] = useState(false);
  const first = entry.rows[0];
  const oldest = Math.max(...entry.rows.map((r) => r.ageDays ?? 0));
  const tickers = [...new Set(entry.rows.map((r) => r.ticker).filter((t): t is string => !!t))];
  return (
    <section className="blueprint relative overflow-hidden" aria-label={`${entry.rows.length} similar items`}>
      <span aria-hidden="true" className="absolute top-4 bottom-4 left-0 w-[3px] rounded-r-sm bg-accent" />
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 py-4 pr-5 pl-6">
        <div className="min-w-0 flex-1">
          <h2 className="font-sans text-[15px] font-medium text-text">
            {first.headline}
            <span className="text-text-muted"> and {entry.rows.length - 1} more like it</span>
          </h2>
          <p className="mt-0.5 text-[13px] text-text-muted">
            {KIND_LABELS[entry.kind]}
            {tickers.length > 0 ? (
              <>
                {" for "}
                {tickers.slice(0, 4).map((t, i) => (
                  <span key={t}>
                    {i > 0 ? ", " : ""}
                    <EntityName id={t} chip={false} />
                  </span>
                ))}
                {tickers.length > 4 ? ` and ${tickers.length - 4} other ${tickers.length - 4 === 1 ? "company" : "companies"}` : ""}
              </>
            ) : null}
            {oldest > 0 ? `. The oldest has waited ${oldest} days.` : "."}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button asChild size="sm">
            <Link href={first.href}>Start with the oldest</Link>
          </Button>
          <Button variant="ghost" size="sm" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            {open ? "Hide list" : "Show all"}
          </Button>
        </div>
      </div>
      {open ? (
        <ul className="border-t border-border-soft">
          {entry.rows.map((r) => (
            <QueueRowItem key={r.id} row={r} />
          ))}
        </ul>
      ) : null}
    </section>
  );
}
