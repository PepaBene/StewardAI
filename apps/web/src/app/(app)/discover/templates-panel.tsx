"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ScoreMark } from "@/components/insight/score-mark";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import type { MetricInfo, ScreenSummary } from "@/lib/discover/discover";
import { chipText, type TemplateOut, type TemplatesOut } from "@/lib/discover/screen-draft";
import { petalsFromMap } from "@/lib/insight/insight";
import { cn } from "@/lib/utils";

/*
 * Starter screens (insight I10.7): curated, owner-editable screen definitions with how many
 * covered stocks pass today and the mean score mark of those matches. Using one fills the
 * builder; it is a starting point, not a recommendation.
 */

type CurrentScreen = { filters: { metric: string; op: "gte" | "lte" | "in"; value: number | string[] }[]; sortBy: string | null };

export function TemplatesPanel({
  catalogue,
  selected,
  onUse,
  current,
  saved = [],
  onRunSaved,
}: {
  saved?: readonly ScreenSummary[];
  onRunSaved?: (id: number) => void;
  catalogue: readonly MetricInfo[];
  selected: string | null;
  onUse: (t: TemplateOut) => void;
  /** The builder's conditions in API units, or null when they do not validate. */
  current: () => CurrentScreen | null;
}) {
  const [data, setData] = useState<TemplatesOut | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  const [manage, setManage] = useState(false);
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState("");
  const [rationale, setRationale] = useState("");
  const [confirm, setConfirm] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let live = true;
    getBrowserApiClient()
      .GET("/discover/templates")
      .then(({ data: d }) => {
        if (!live) return;
        if (d) setData(d);
        else setFailed(true);
      })
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [reload]);

  /** Read the saved document, change it, save it as a new version. */
  async function change(edit: (ts: TemplateBody[]) => TemplateBody[] | string): Promise<boolean> {
    const client = getBrowserApiClient();
    const got = await client.GET("/config/screen-templates");
    if (!got.data) {
      setMessage("The starter screens could not be read.");
      return false;
    }
    const next = edit(got.data.body.templates ?? []);
    if (typeof next === "string") {
      setMessage(next);
      return false;
    }
    const saved = await client.PUT("/config/screen-templates", {
      params: { query: got.data.id ? { base_version_id: got.data.id } : {} },
      body: { schema_version: 1, templates: next },
    });
    if (!saved.response.ok) {
      const d = saved.error && typeof saved.error === "object" && "detail" in saved.error ? JSON.stringify((saved.error as { detail: unknown }).detail) : "";
      setMessage(`Not saved (HTTP ${saved.response.status}). ${d}`.slice(0, 300));
      return false;
    }
    setMessage("Saved as a new version of the starter screens.");
    setReload((n) => n + 1);
    return true;
  }

  // A link such as /discover?template=quality-payers opens the list on that template.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- a linked template opens the full list
    if (selected) setOpen(true);
  }, [selected]);

  const shown = data ? (open ? data.templates : data.templates.slice(0, 4)) : [];
  return (
    <section aria-labelledby="templates-heading" className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="templates-heading" className="text-[22px] leading-tight font-semibold">
          Ideas to start from
        </h2>
        <span className="text-xs text-text-muted">
          {data?.note}{" "}
          <Link href="/config/history/screen-templates" className="text-accent-ink underline underline-offset-2">
            History
          </Link>
        </span>
      </div>
      {failed ? <p className="text-sm text-text-muted">The starter screens could not be loaded.</p> : null}
      {!data && !failed ? (
        <p className="text-sm text-text-muted" role="status">
          Counting matches for each starter screen…
        </p>
      ) : null}
      <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        {shown.map((t) => (
          <li
            key={t.id}
            className={cn("flex flex-col gap-2 rounded-xl border bg-surface-raised p-4", selected === t.id ? "border-accent" : "border-border")}
          >
            <div className="flex items-start justify-between gap-3">
              <h3 className="font-heading text-base leading-snug font-semibold">{t.title}</h3>
              <ScoreMark petals={petalsFromMap(t.mark)} size="header" label={`${t.title}, ${t.mark_basis}`} className="shrink-0" />
            </div>
            <p className="line-clamp-2 text-xs text-text-muted" title={t.rationale}>
              {t.rationale}
            </p>
            <ul className="flex flex-wrap gap-1" aria-label="Conditions">
              {t.filters.map((f, i) => (
                <li key={i} className="bg-surface-muted px-1.5 py-0.5 text-xs">
                  {chipText(f as never, catalogue)}
                </li>
              ))}
            </ul>
            <div className="mt-auto flex items-end justify-between gap-2 pt-1">
              <span className="text-xs">
                {t.matched !== null && t.matched !== undefined ? (
                  <>
                    <span className="num font-semibold">{t.matched}</span> pass today
                    <span className="block text-xs text-text-faint">{t.mark_basis}</span>
                  </>
                ) : (
                  <span className="text-text-muted" title={(t.disabled ?? []).join("; ")}>
                    Not ready yet. {(t.disabled ?? [])[0]}
                  </span>
                )}
              </span>
              <span className="flex shrink-0 gap-1">
                {!manage ? null : confirm === t.id ? (
                  <button
                    type="button"
                    onClick={() => void change((ts) => ts.filter((x) => x.id !== t.id)).then(() => setConfirm(null))}
                    className="border border-loss px-2 py-1 text-xs text-loss"
                  >
                    Remove for good
                  </button>
                ) : (
                  <button type="button" onClick={() => setConfirm(t.id)} className="px-1.5 py-1 text-xs text-text-muted hover:text-loss" aria-label={`Remove ${t.title}`}>
                    Remove
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => onUse(t)}
                  className="rounded-md border border-border px-2.5 py-1 text-xs whitespace-nowrap hover:border-accent hover:text-accent-ink focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none"
                >
                  Show matches
                </button>
              </span>
            </div>
          </li>
        ))}
      </ul>
      {saved.length > 0 ? (
        <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4" aria-label="Your saved screens">
          {saved.map((s) => (
            <li key={s.id} className="flex flex-col gap-2 rounded-xl border border-border bg-surface-raised p-4">
              <div className="flex items-start justify-between gap-3">
                <h3 className="font-heading text-base leading-snug font-semibold">{s.name}</h3>
                {s.mark ? <ScoreMark petals={petalsFromMap(s.mark)} size="header" label={`${s.name}, average of ${s.last_matched ?? 0} matches`} className="shrink-0" /> : null}
              </div>
              <p className="text-xs text-text-muted">
                {s.last_run_at ? (
                  <>
                    <span className="num font-semibold text-text">{s.last_matched ?? 0}</span> matches
                    {s.new_last_7d !== null && s.new_last_7d !== undefined ? (
                      <>
                        , <span className={cn("num font-semibold", s.new_last_7d > 0 && "text-accent-ink")}>{s.new_last_7d}</span> new in 7 days
                      </>
                    ) : null}
                  </>
                ) : (
                  "Not run yet."
                )}
              </p>
              <div className="mt-auto pt-1">
                <button
                  type="button"
                  onClick={() => onRunSaved?.(s.id)}
                  className="rounded-md border border-border px-2.5 py-1 text-xs whitespace-nowrap hover:border-accent hover:text-accent-ink focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none"
                >
                  Show matches
                </button>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={() => setManage((m) => !m)} aria-pressed={manage} className="text-sm text-accent-ink underline underline-offset-2">
          {manage ? "Done managing" : "Manage ideas"}
        </button>
        {data && data.templates.length > 4 ? (
          <button type="button" onClick={() => setOpen((o) => !o)} className="text-sm text-accent-ink underline underline-offset-2">
            {open ? "Show fewer" : `Show all ${data.templates.length}`}
          </button>
        ) : null}
        {manage ? (
          <button type="button" onClick={() => setAdding((a) => !a)} aria-expanded={adding} className="text-sm text-accent-ink underline underline-offset-2">
            Save the current filters as an idea
          </button>
        ) : null}
      </div>
      {manage && adding ? (
        <form
          className="grid max-w-2xl gap-2 border border-border p-3"
          onSubmit={async (e) => {
            e.preventDefault();
            const cur = current();
            if (!cur || cur.filters.length === 0) return setMessage("Add at least one valid condition first.");
            const id = title.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 40) || "screen";
            const ok = await change((ts) =>
              ts.some((x) => x.id === id)
                ? `A starter screen called "${title}" exists already.`
                : [...ts, { id, title: title.trim(), rationale: rationale.trim(), filters: cur.filters, sort_by: cur.sortBy, descending: true }],
            );
            if (ok) {
              setAdding(false);
              setTitle("");
              setRationale("");
            }
          }}
        >
          <label className="text-sm">
            Title
            <input value={title} onChange={(e) => setTitle(e.target.value)} required minLength={2} maxLength={80} className="mt-1 block w-full border border-border bg-surface-raised px-2 py-1 text-sm" />
          </label>
          <label className="text-sm">
            Why these conditions (one paragraph)
            <textarea value={rationale} onChange={(e) => setRationale(e.target.value)} required minLength={10} maxLength={800} rows={3} className="mt-1 block w-full border border-border bg-surface-raised px-2 py-1 text-sm" />
          </label>
          <button type="submit" className="justify-self-start border border-border px-3 py-1 text-sm hover:border-accent">
            Save starter screen
          </button>
        </form>
      ) : null}
      {message ? (
        <p role="status" className="text-sm text-text-muted">
          {message}
        </p>
      ) : null}
    </section>
  );
}

type TemplateBody = NonNullable<TemplatesBody["templates"]>[number];
type TemplatesBody = components["schemas"]["ScreenTemplatesConfig"];
