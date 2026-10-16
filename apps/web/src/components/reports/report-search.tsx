"use client";

import { LoaderCircleIcon, SearchIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useState, type FormEvent } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { BlueprintCorners } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getBrowserApiClient, type ReportHit } from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";
import {
  parseHighlights,
  plainExcerpt,
  QUERY_MAX,
  REPORT_TYPE_LABELS,
  REPORT_TYPES,
  reportHitHref,
  searchHref,
  sortHits,
  type SearchState,
} from "@/lib/reports/search";

/** An excerpt with the API's matches highlighted: text segments only, no HTML inserted. */
export function HighlightedExcerpt({ excerpt }: { excerpt: string }) {
  return (
    <span title={plainExcerpt(excerpt)}>
      {parseHighlights(excerpt).map((segment, i) =>
        segment.highlight ? (
          <mark key={i} className="rounded-sm bg-warning-signal-subtle px-0.5 font-semibold text-text underline decoration-warning-signal underline-offset-2">
            {segment.text}
          </mark>
        ) : (
          <span key={i}>{segment.text}</span>
        ),
      )}
    </span>
  );
}

type Load = { kind: "idle" } | { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; hits: ReportHit[] };

/**
 * Full-text search over every report (digests, monthly reviews, research, impact notes):
 * `GET /reports/search`. The query and the type filters live in the URL.
 */
export function ReportSearch({ state }: { state: SearchState }) {
  const router = useRouter();
  const id = useId();
  const [draft, setDraft] = useState(state.q);
  const [load, setLoad] = useState<Load>({ kind: "idle" });
  const typesKey = state.types.join(",");

  const q = state.q;
  useEffect(() => {
    if (!q) return;
    let cancelled = false;
    const types = typesKey ? (typesKey.split(",") as SearchState["types"]) : [];
    queueMicrotask(() => !cancelled && setLoad({ kind: "loading" }));
    getBrowserApiClient()
      .GET("/reports/search", {
        params: { query: { q, type: types.length > 0 ? types : undefined, limit: 200 } },
      })
      .then(({ data, response }) => {
        if (cancelled) return;
        if (data) setLoad({ kind: "ok", hits: sortHits(data) });
        else setLoad({ kind: "error", message: `The API answered HTTP ${response.status}.` });
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoad({ kind: "error", message: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [q, typesKey]);

  function submit(event: FormEvent) {
    event.preventDefault();
    router.push(searchHref({ q: draft, types: state.types }), { scroll: false });
  }

  function toggleType(type: (typeof REPORT_TYPES)[number]) {
    const types = state.types.includes(type) ? state.types.filter((t) => t !== type) : [...state.types, type];
    router.replace(searchHref({ q: state.q || draft, types }), { scroll: false });
  }

  return (
    <section aria-labelledby={`${id}-heading`} className="blueprint space-y-3 px-5 py-3">
      <BlueprintCorners />
      <h2 id={`${id}-heading`} className="sr-only">
        Search every report
      </h2>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        <form role="search" onSubmit={submit} className="flex min-w-72 flex-1 items-center gap-2">
          <Label htmlFor={`${id}-q`} className="sr-only">
            Search all reports
          </Label>
          <div className="relative min-w-0 flex-1">
            <SearchIcon aria-hidden="true" className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-text-faint" />
            <Input
              id={`${id}-q`}
              type="search"
              value={draft}
              maxLength={QUERY_MAX}
              onChange={(e) => setDraft(e.target.value)}
              placeholder='Search all reports: a ticker, words, or "a phrase" (-word excludes)'
              aria-describedby={`${id}-hint`}
              className="pl-8"
            />
          </div>
          <Button type="submit" size="sm" disabled={!draft.trim()}>
            Search
          </Button>
          {state.q ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setDraft("");
                router.push(searchHref({ q: "", types: state.types }), { scroll: false });
              }}
            >
              <XIcon aria-hidden="true" />
              Clear search
            </Button>
          ) : null}
        </form>
        <fieldset className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px]">
          <legend className="sr-only">Report types</legend>
          <span id={`${id}-hint`} className="text-xs text-text-muted">
            Types (none ticked: all):
          </span>
          {REPORT_TYPES.map((t) => (
            <label key={t} className="inline-flex items-center gap-1.5">
              <input type="checkbox" checked={state.types.includes(t)} onChange={() => toggleType(t)} className="size-4 accent-accent" />
              {REPORT_TYPE_LABELS[t].plural}
            </label>
          ))}
        </fieldset>
      </div>

      {state.q ? (
        <div className="space-y-2" aria-live="polite">
          {load.kind === "loading" || load.kind === "idle" ? (
            <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
              <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
              Searching…
            </p>
          ) : null}
          {load.kind === "error" ? <AlertMessage title="Search failed">{load.message}</AlertMessage> : null}
          {load.kind === "ok" ? (
            <>
              <p className="num text-sm text-text-muted">
                {load.hits.length} report{load.hits.length === 1 ? "" : "s"} contain{load.hits.length === 1 ? "s" : ""}{" "}
                <span className="font-medium text-text">{state.q}</span>
                {state.types.length > 0 ? ` (${state.types.map((t) => REPORT_TYPE_LABELS[t].plural.toLowerCase()).join(", ")})` : ""}, newest
                first{load.hits.length >= 200 ? "; only the 200 newest are shown" : ""}.
              </p>
              {load.hits.length === 0 ? (
                <EmptyState title="No report contains it">Try fewer words, or untick the type filters.</EmptyState>
              ) : (
                <ol className="divide-y divide-border-soft border border-border">
                  {load.hits.map((h) => (
                    <li key={`${h.type}:${h.id}`}>
                      <Link href={reportHitHref(h)} className="block space-y-1 p-3 hover:bg-surface-muted">
                        <span className="flex flex-wrap items-center justify-between gap-2">
                          <span className="font-medium">{h.title}</span>
                          <time dateTime={h.created_at} className="num text-xs text-text-muted">
                            {formatDateTime(h.created_at)}
                          </time>
                        </span>
                        <span className="flex flex-wrap gap-2 text-xs text-text-muted">
                          <span className="rounded-sm border border-border px-1.5">{REPORT_TYPE_LABELS[h.type].label}</span>
                          {h.period ? <span>{h.period}</span> : null}
                          {h.ticker ? <span className="font-mono">{h.ticker}</span> : null}
                          {h.profile ? <span>profile {h.profile}</span> : null}
                        </span>
                        <span className="block text-sm text-text-muted">
                          <HighlightedExcerpt excerpt={h.excerpt} />
                        </span>
                      </Link>
                    </li>
                  ))}
                </ol>
              )}
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
