"use client";

import { ExternalLinkIcon, LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { NewsFeedback } from "@/components/news/news-feedback";
import { NewsTabs } from "@/components/news/news-tabs";
import { TierBadge } from "@/components/news/severity-badge";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";
import { isExternalUrl } from "@/lib/sources/sources";
import {
  isWireFiltered,
  LIFECYCLE_LABELS,
  parseWireFilters,
  wireFiltersToSearch,
  wireHeadline,
  wireQuery,
  type WireFilters,
  type WireItem,
} from "@/lib/news/analysis";
import { cn } from "@/lib/utils";

const PAGE = 50;
/** Wait this long after the last keystroke before the filter reaches the URL (and the API). */
const TYPING_DELAY_MS = 350;

type TextDraft = Pick<WireFilters, "q" | "region" | "language" | "outlet" | "topic">;

const textDraft = (f: WireFilters): TextDraft => ({ q: f.q, region: f.region, language: f.language, outlet: f.outlet, topic: f.topic });

const CONTROL =
  "w-full rounded-md border border-border bg-surface-raised px-2 py-1.5 text-sm text-text shadow-xs outline-none placeholder:text-text-muted focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30";

type Load =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; items: WireItem[]; next: number | null; loadingMore: boolean; moreError: string | null };

/** The wire: every headline from every enabled source, linked to a holding or not. */
export function WireView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const filters = useMemo(() => parseWireFilters(new URLSearchParams(params.toString())), [params]);
  // What is typed, kept untrimmed so "Nikkei Asia" can be typed; it reaches the URL after a pause.
  const [draft, setDraft] = useState<TextDraft>(() => textDraft(filters));
  const pushed = useRef(wireFiltersToSearch(filters));
  // Bumped by every (re)load so a late response to an older filter is dropped.
  const generation = useRef(0);
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);
  const [showOriginal, setShowOriginal] = useState(false);

  useEffect(() => {
    // The choice is a per-viewer convenience: a blocked or empty store just means the default.
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      if (window.localStorage.getItem("news.wire.original") === "1") setShowOriginal(true);
    } catch {
      /* storage unavailable */
    }
  }, []);

  function chooseOriginal(value: boolean) {
    setShowOriginal(value);
    try {
      window.localStorage.setItem("news.wire.original", value ? "1" : "0");
    } catch {
      /* storage unavailable */
    }
  }

  useEffect(() => {
    let cancelled = false;
    generation.current += 1;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoad({ kind: "loading" });
    getBrowserApiClient()
      .GET("/wire", { params: { query: wireQuery(filters, null, PAGE) } })
      .then(({ data, response }) => {
        if (cancelled) return;
        if (data) setLoad({ kind: "ok", items: data.items as WireItem[], next: data.next_before_id ?? null, loadingMore: false, moreError: null });
        else setLoad({ kind: "error", message: `The API answered HTTP ${response.status}.` });
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoad({ kind: "error", message: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [filters, reloadKey]);

  // Back/forward or a link changed the URL filters: show them in the inputs.
  useEffect(() => {
    const search = wireFiltersToSearch(filters);
    if (search === pushed.current) return;
    pushed.current = search;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDraft(textDraft(filters));
  }, [filters]);

  function push(next: WireFilters) {
    const search = wireFiltersToSearch(next);
    pushed.current = search;
    router.replace(`${pathname}${search}`, { scroll: false });
  }

  useEffect(() => {
    const search = wireFiltersToSearch({ ...filters, ...draft });
    if (search === pushed.current) return;
    const timer = setTimeout(() => push({ ...filters, ...draft }), TYPING_DELAY_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, filters]);

  const set = (change: Partial<WireFilters>) => push({ ...filters, ...draft, ...change });

  async function more() {
    if (load.kind !== "ok" || load.next === null || load.loadingMore) return;
    const gen = generation.current;
    setLoad({ ...load, loadingMore: true, moreError: null });
    try {
      const { data, response } = await getBrowserApiClient().GET("/wire", { params: { query: wireQuery(filters, load.next, PAGE) } });
      if (gen !== generation.current) return;
      setLoad((l) =>
        l.kind !== "ok"
          ? l
          : data
            ? { kind: "ok", items: [...l.items, ...(data.items as WireItem[])], next: data.next_before_id ?? null, loadingMore: false, moreError: null }
            : { ...l, loadingMore: false, moreError: `The API answered HTTP ${response.status}.` },
      );
    } catch (err) {
      if (gen !== generation.current) return;
      const message = err instanceof Error ? err.message : String(err);
      setLoad((l) => (l.kind === "ok" ? { ...l, loadingMore: false, moreError: message } : l));
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="News"
        title="Wire"
        description={
          <span className="block max-w-prose">
            Every headline from every enabled source, in any language, whether or not it mentions a holding. The wire
            never alerts: only a story about your holdings can. Non-English headlines show their English translation
            beside the original.
          </span>
        }
        aside={
          <Button variant="ghost" size="sm" onClick={() => setReloadKey((k) => k + 1)}>
            <RefreshCwIcon aria-hidden="true" />
            Reload
          </Button>
        }
      />
      <NewsTabs active="wire" />

      <form
        className="grid grid-cols-2 items-end gap-3 md:grid-cols-4 lg:grid-cols-8"
        onSubmit={(e) => {
          e.preventDefault();
          set({});
        }}
        aria-label="Wire filters"
      >
        <Field label="Search" className="col-span-2">
          <input className={CONTROL} value={draft.q} onChange={(e) => setDraft((d) => ({ ...d, q: e.target.value }))} placeholder="Words in the headline" />
        </Field>
        <Field label="Region">
          <input className={cn(CONTROL, "uppercase")} maxLength={2} value={draft.region} onChange={(e) => setDraft((d) => ({ ...d, region: e.target.value }))} placeholder="JP" />
        </Field>
        <Field label="Language">
          <input className={CONTROL} maxLength={8} value={draft.language} onChange={(e) => setDraft((d) => ({ ...d, language: e.target.value }))} placeholder="ja" />
        </Field>
        <Field label="Outlet">
          <input className={CONTROL} value={draft.outlet} onChange={(e) => setDraft((d) => ({ ...d, outlet: e.target.value }))} placeholder="reuters.com" />
        </Field>
        <Field label="Tier">
          <select className={CONTROL} value={filters.tier} onChange={(e) => set({ tier: e.target.value })}>
            <option value="">Any</option>
            <option value="1">Tier 1</option>
            <option value="2">Tier 2</option>
            <option value="3">Tier 3</option>
          </select>
        </Field>
        <Field label="Holdings">
          <select className={CONTROL} value={filters.linked} onChange={(e) => set({ linked: e.target.value as WireFilters["linked"] })}>
            <option value="">All items</option>
            <option value="yes">Linked to a holding</option>
            <option value="no">Not linked</option>
          </select>
        </Field>
        <Field label="Paywall">
          <select className={CONTROL} value={filters.paywall} onChange={(e) => set({ paywall: e.target.value })}>
            <option value="">Any</option>
            <option value="open">Open</option>
            <option value="metered">Metered</option>
            <option value="hard">Hard</option>
          </select>
        </Field>
        <Field label="Theme">
          <input className={CONTROL} value={draft.topic} onChange={(e) => setDraft((d) => ({ ...d, topic: e.target.value }))} placeholder="rates" />
        </Field>
        <div className="col-span-2 flex gap-2 md:col-span-1">
          <Button type="submit" size="sm">
            Search
          </Button>
          {isWireFiltered(filters) ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setDraft({ q: "", region: "", language: "", outlet: "", topic: "" });
                pushed.current = "";
                router.replace(pathname, { scroll: false });
              }}
            >
              Clear
            </Button>
          ) : null}
        </div>
      </form>

      {load.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading the wire…
        </p>
      ) : null}
      {load.kind === "error" ? <AlertMessage title="Could not load the wire">{load.message}</AlertMessage> : null}
      {load.kind === "ok" && load.items.length === 0 ? (
        <EmptyState title={isWireFiltered(filters) ? "Nothing matches these filters" : "The wire is empty"}>
          {isWireFiltered(filters)
            ? "Clear a filter, or widen the search words."
            : "Items appear after the next news check. Enable more sources under Config, News sources."}
        </EmptyState>
      ) : null}
      {load.kind === "ok" && load.items.length > 0 ? (
        <>
          <label className="flex items-center gap-2 text-sm text-text-muted">
            <input type="checkbox" checked={showOriginal} onChange={(e) => chooseOriginal(e.target.checked)} />
            Show headlines in their original language
          </label>
          <ul className="m-0 flex list-none flex-col divide-y divide-border border-y border-border p-0" aria-label="Wire items">
            {load.items.map((item) => (
              <WireRow key={item.id} item={item} showOriginal={showOriginal} />
            ))}
          </ul>
          {load.next !== null ? (
            <div>
              <Button variant="outline" onClick={() => void more()} disabled={load.loadingMore}>
                {load.loadingMore ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : null}
                Older items
              </Button>
              {load.moreError ? <AlertMessage title="Could not load older items">{load.moreError}</AlertMessage> : null}
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function Field({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <label className={cn("flex flex-col gap-1 text-xs text-text-muted", className)}>
      {label}
      {children}
    </label>
  );
}

function WireRow({ item, showOriginal }: { item: WireItem; showOriginal: boolean }) {
  // Per-item flip on top of the page-wide choice.
  const [flipped, setFlipped] = useState(false);
  const hasTranslation = Boolean(item.title_en && item.title_en !== item.headline);
  const { text, translated } = wireHeadline(item, showOriginal !== flipped);
  return (
    <li className="space-y-1 py-3">
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-text-muted">
        <TierBadge tier={item.tier as 1 | 2 | 3} />
        <span>{item.outlet_name ?? item.outlet ?? "Unknown outlet"}</span>
        {item.region ? <span className="font-mono">{item.region}</span> : null}
        {item.language ? <span className="font-mono">{item.language}</span> : null}
        <time dateTime={item.published_at} className="num">
          {formatDateTime(item.published_at)}
        </time>
        {item.paywall !== "open" ? <span>{item.paywall === "hard" ? "Paywalled" : "Metered paywall"}</span> : null}
        {item.state_media ? <span>State media</span> : null}
        {item.topics.map((t) => (
          <Link key={t} href={`/news/wire?topic=${encodeURIComponent(t)}`} className="rounded-sm border border-border px-1.5 text-text hover:bg-border-soft">
            {t}
          </Link>
        ))}
      </p>
      {isExternalUrl(item.url) ? (
        <a href={item.url} target="_blank" rel="noreferrer" className="inline-flex items-start gap-1 text-base font-medium hover:text-accent-ink hover:underline">
          <span className="[overflow-wrap:anywhere]">{text}</span>
          <ExternalLinkIcon aria-hidden="true" className="mt-1 size-3 shrink-0 text-text-muted" />
        </a>
      ) : (
        <span className="inline-flex text-base font-medium [overflow-wrap:anywhere]">{text}</span>
      )}
      {translated ? (
        <p lang={item.language ?? undefined} className="text-sm text-text-muted [overflow-wrap:anywhere]">
          Original: {item.headline}
        </p>
      ) : null}
      {item.gloss ? <p className="text-sm text-text-muted">{item.gloss}</p> : null}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-muted">
        {hasTranslation ? (
          <button type="button" onClick={() => setFlipped((f) => !f)} className="text-accent-ink hover:underline">
            {showOriginal !== flipped ? "Show English" : "Show original"}
          </button>
        ) : null}
        {item.story_id ? (
          <Link href={`/news/stories/${item.story_id}`} className="text-accent-ink hover:underline">
            Story about a holding
            {item.story_lifecycle ? `: ${LIFECYCLE_LABELS[item.story_lifecycle] ?? item.story_lifecycle}` : ""}
          </Link>
        ) : (
          <span>Not linked to a holding</span>
        )}
        <NewsFeedback target="item" targetId={item.id} label={`wire item “${text}”`} />
      </div>
    </li>
  );
}
