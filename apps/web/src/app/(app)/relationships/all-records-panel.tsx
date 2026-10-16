"use client";

import { useCallback, useMemo, useState } from "react";

import { EmptyState } from "@/components/empty-state";
import { dataOf, Panel, ResourceNotice } from "@/components/oversight/panel";
import { Field, SELECT_CLASS } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { useApiResource, type ApiResult } from "@/lib/hooks/use-api-resource";
import { titleCase } from "@/lib/parse-untyped";
import {
  GRAPH_KINDS,
  PAGE_SIZE,
  pageRange,
  parseGraphPage,
  parseHistory,
  safeUrl,
  shareText,
  statusWord,
  STATUS_FILTERS,
  type GraphKind,
  type GraphListRow,
  type GraphStatus,
  type History,
} from "@/lib/relationships/relationships";

import { Fact, SourceLink, StatusMark } from "./status-mark";

type Page = components["schemas"]["GraphRowsPage"];
type HistoryKind = History["kind"];

const fetchHistory = (kind: HistoryKind, id: number): Promise<ApiResult<components["schemas"]["DecisionHistory"]>> => {
  const c = getBrowserApiClient();
  if (kind === "edge") return c.GET("/company-graph/edges/{edge_id}/decision-history", { params: { path: { edge_id: id } } });
  if (kind === "event") return c.GET("/company-graph/events/{event_id}/decision-history", { params: { path: { event_id: id } } });
  if (kind === "disclosure") return c.GET("/company-graph/disclosures/{disclosure_id}/decision-history", { params: { path: { disclosure_id: id } } });
  return c.GET("/relationship-review/suggestions/{suggestion_id}/decision-history", { params: { path: { suggestion_id: id } } });
};

function HistoryBody({ kind, id }: { kind: HistoryKind; id: number }) {
  const fetcher = useCallback(() => fetchHistory(kind, id), [kind, id]);
  const { state } = useApiResource(fetcher);
  const raw = dataOf(state);
  const h = useMemo(() => (raw ? parseHistory(raw) : null), [raw]);
  return (
    <div className="space-y-2">
      <ResourceNotice state={state} what="the decision history" />
      {h && h.entries.length === 0 ? <p className="text-xs text-text-muted">No owner decision has changed this row; it is still {statusWord(h.current ?? "suggested").toLowerCase()}.</p> : null}
      {h && h.entries.length > 0 ? (
        <ol className="space-y-1.5 border-l border-border pl-3">
          {h.entries.map((e, i) => (
            <li key={`${e.at}-${i}`} className="text-xs">
              <span className="num">{e.at.slice(0, 16).replace("T", " ")}</span>: {e.from ? statusWord(e.from) : "First record"} to {e.to ? statusWord(e.to) : titleCase(e.toWord)}
              {e.by ? <span className="text-text-muted"> by {e.by}</span> : null}
              <p className="text-text-muted">{e.reason ? `Reason: ${e.reason}` : "No reason recorded."}</p>
            </li>
          ))}
        </ol>
      ) : null}
      {h?.limitations.map((l) => (
        <p key={l} className="text-xs text-text-muted">
          {l}
        </p>
      ))}
    </div>
  );
}

/** Every status change of one row, loaded only when it is opened. Shared by the lists and the suggestion cards. */
export function DecisionHistory({ kind, id }: { kind: HistoryKind; id: number }) {
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(e) => setOpen(e.currentTarget.open)} className="text-sm">
      <summary className="cursor-pointer text-xs text-accent-ink hover:underline">Decision history</summary>
      {open ? <HistoryBody kind={kind} id={id} /> : null}
    </details>
  );
}

const ROW_KIND: Record<GraphKind, HistoryKind> = { edges: "edge", events: "event", disclosures: "disclosure" };

function RowCard({ view, r }: { view: GraphKind; r: GraphListRow }) {
  return (
    <li className="space-y-2 border border-border bg-surface-raised p-3">
      <div className="flex flex-wrap items-center gap-2">
        {r.status ? <StatusMark status={r.status} /> : <span className="border border-border px-1.5 py-0.5 text-xs">Unrecognised status: {r.rawStatus}</span>}
        <span className="num text-sm font-semibold">{r.ticker}</span>
        <span className="text-sm">{titleCase(r.kind)}</span>
        {r.counterparty ? <span className="text-sm text-text-muted">{r.counterparty}</span> : null}
        <span className="num ml-auto text-xs text-text-muted">#{r.id}</span>
      </div>
      <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-4">
        <Fact label="Recorded">{r.knownAt.slice(0, 10)}</Fact>
        <Fact label="Last decided">{r.decidedAt?.slice(0, 10) ?? null}</Fact>
        {view === "disclosures" ? <Fact label="Reported share">{shareText(r.reportedShare)}</Fact> : <Fact label="Evidence tier">{r.tier ? titleCase(r.tier) : null}</Fact>}
        {view === "disclosures" ? <Fact label="Fiscal period">{r.fiscalPeriod}</Fact> : <Fact label="Valid">{r.validFrom || r.validTo ? `${r.validFrom ?? "open start"} to ${r.validTo ?? "open end"}` : null}</Fact>}
      </dl>
      {r.passage ? <blockquote className="max-w-[76ch] border-l-2 border-border pl-3 text-sm italic">{r.passage}</blockquote> : null}
      <p className="text-xs text-text-muted">
        <SourceLink href={safeUrl(r.sourceUrl)} /> · {r.source}
      </p>
      <p className="text-xs text-text-muted">{r.decisionReason ? `Last decision: ${r.decisionReason}` : "No owner decision recorded yet."}</p>
      <DecisionHistory kind={ROW_KIND[view]} id={r.id} />
    </li>
  );
}

/**
 * Section 6: every graph edge, event and disclosure across all holdings, newest first, with
 * filters and the full decision history of any row. Read-only: decisions are taken in the
 * sections above. Status is told by word and mark together, never by colour.
 */
export function AllRecordsPanel({ ticker }: { ticker: string | null }) {
  const [view, setView] = useState<GraphKind>("edges");
  const [status, setStatus] = useState<GraphStatus | "">("");
  const [tickerFilter, setTickerFilter] = useState("");
  const [kindFilter, setKindFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const effectiveTicker = (ticker ?? tickerFilter.trim()) || undefined;

  const fetcher = useCallback(async (): Promise<ApiResult<Page>> => {
    const params = { query: { status: status || undefined, ticker: effectiveTicker, kind: kindFilter.trim() || undefined, limit: PAGE_SIZE, offset } };
    const c = getBrowserApiClient();
    if (view === "edges") return c.GET("/company-graph/edges", { params });
    if (view === "events") return c.GET("/company-graph/events", { params });
    return c.GET("/company-graph/disclosures", { params });
  }, [view, status, effectiveTicker, kindFilter, offset]);
  const { state } = useApiResource(fetcher);
  const raw = dataOf(state);
  const page = useMemo(() => (raw ? parseGraphPage(raw) : null), [raw]);
  const reset = <T,>(set: (v: T) => void) => (v: T) => {
    set(v);
    setOffset(0);
  };

  return (
    <Panel
      id="all-records"
      title="All recorded relationships"
      description="Every edge, event and disclosure across your holdings, newest first, with its status and the full history of decisions on it. Filters narrow the list; nothing here changes a row."
    >
      <div role="tablist" aria-label="Kind of record" className="flex flex-wrap gap-1">
        {GRAPH_KINDS.map((k) => (
          <Button key={k.id} type="button" role="tab" aria-selected={view === k.id} size="sm" variant={view === k.id ? "default" : "outline"} onClick={() => { setView(k.id); setOffset(0); }}>
            {k.label}
          </Button>
        ))}
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Status">
          <select value={status} onChange={(e) => reset(setStatus)(e.target.value as GraphStatus | "")} className={`${SELECT_CLASS} w-40`}>
            <option value="">Any status</option>
            {STATUS_FILTERS.map((s) => (
              <option key={s} value={s}>
                {statusWord(s)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Holding" hint={ticker ? "Set by the holding chosen above." : undefined}>
          <Input value={ticker ?? tickerFilter} disabled={ticker !== null} onChange={(e) => reset(setTickerFilter)(e.target.value)} className="num w-44" placeholder="Any holding" />
        </Field>
        <Field label={view === "edges" ? "Edge type" : view === "events" ? "Event type" : "Relationship"}>
          <Input value={kindFilter} onChange={(e) => reset(setKindFilter)(e.target.value)} className="w-44" placeholder="Any" />
        </Field>
      </div>
      <ResourceNotice state={state} what="the recorded relationships" />
      {page && page.rows.length === 0 ? <EmptyState title="No rows match">Loosen a filter, or nothing of this kind has been recorded yet.</EmptyState> : null}
      {page && page.rows.length > 0 ? (
        <>
          <ul className="space-y-3" aria-label={`${GRAPH_KINDS.find((k) => k.id === view)?.label} ${pageRange(page)}`}>
            {page.rows.map((r) => (
              <RowCard key={`${view}-${r.id}`} view={view} r={r} />
            ))}
          </ul>
          <div className="flex items-center gap-3 text-xs text-text-muted">
            <Button type="button" size="sm" variant="outline" disabled={page.offset === 0} onClick={() => setOffset(Math.max(0, page.offset - PAGE_SIZE))}>
              Newer
            </Button>
            <span className="num">{pageRange(page)}</span>
            <Button type="button" size="sm" variant="outline" disabled={page.offset + page.rows.length >= page.total} onClick={() => setOffset(page.offset + PAGE_SIZE)}>
              Older
            </Button>
          </div>
        </>
      ) : null}
    </Panel>
  );
}
