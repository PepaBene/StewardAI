"use client";

import { ExternalLinkIcon, LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { NewsFeedback } from "@/components/news/news-feedback";
import { NewsTabs } from "@/components/news/news-tabs";
import { TierBadge } from "@/components/news/severity-badge";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";
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

const CONTROL =
  "w-full rounded-md border border-border bg-surface-raised px-2 py-1.5 text-sm text-text shadow-xs outline-none placeholder:text-text-muted focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30";

type Load =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; items: WireItem[]; next: number | null; loadingMore: boolean };

/** The wire: every headline from every enabled source, linked to a holding or not. */
export function WireView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const filters = useMemo(() => parseWireFilters(new URLSearchParams(params.toString())), [params]);
  const [draftQ, setDraftQ] = useState(filters.q);
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
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoad({ kind: "loading" });
    getBrowserApiClient()
      .GET("/wire", { params: { query: wireQuery(filters, null, PAGE) } })
      .then(({ data, response }) => {
        if (cancelled) return;
        if (data) setLoad({ kind: "ok", items: data.items as WireItem[], next: data.next_before_id ?? null, loadingMore: false });
        else setLoad({ kind: "error", message: `The API answered HTTP ${response.status}.` });
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoad({ kind: "error", message: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [filters, reloadKey]);

  const set = (change: Partial<WireFilters>) =>
    router.replace(`${pathname}${wireFiltersToSearch({ ...filters, ...change })}`, { scroll: false });

  async function more() {
    if (load.kind !== "ok" || load.next === null) return;
    setLoad({ ...load, loadingMore: true });
    const { data } = await getBrowserApiClient().GET("/wire", { params: { query: wireQuery(filters, load.next, PAGE) } });
    setLoad((l) =>
      l.kind === "ok"
        ? data
          ? { kind: "ok", items: [...l.items, ...(data.items as WireItem[])], next: data.next_before_id ?? null, loadingMore: false }
          : { ...l, loadingMore: false }
        : l,
    );
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
          set({ q: draftQ });
        }}
        aria-label="Wire filters"
      >
        <Field label="Search" className="col-span-2">
          <input className={CONTROL} value={draftQ} onChange={(e) => setDraftQ(e.target.value)} placeholder="Words in the headline" />
        </Field>
        <Field label="Region">
          <input className={cn(CONTROL, "uppercase")} maxLength={2} value={filters.region} onChange={(e) => set({ region: e.target.value })} placeholder="JP" />
        </Field>
        <Field label="Language">
          <input className={CONTROL} maxLength={8} value={filters.language} onChange={(e) => set({ language: e.target.value })} placeholder="ja" />
        </Field>
        <Field label="Outlet">
          <input className={CONTROL} value={filters.outlet} onChange={(e) => set({ outlet: e.target.value })} placeholder="reuters.com" />
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
          <input className={CONTROL} value={filters.topic} onChange={(e) => set({ topic: e.target.value })} placeholder="rates" />
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
                setDraftQ("");
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
      <a href={item.url} target="_blank" rel="noreferrer" className="inline-flex items-start gap-1 text-base font-medium hover:text-accent-ink hover:underline">
        <span className="[overflow-wrap:anywhere]">{text}</span>
        <ExternalLinkIcon aria-hidden="true" className="mt-1 size-3 shrink-0 text-text-muted" />
      </a>
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
