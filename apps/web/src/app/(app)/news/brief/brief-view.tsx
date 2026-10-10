"use client";

import { LoaderCircleIcon, PlayIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { NewsFeedback } from "@/components/news/news-feedback";
import { NewsTabs } from "@/components/news/news-tabs";
import { SeverityDot } from "@/components/news/severity-timeline";
import { PageHeader } from "@/components/page-header";
import { Blueprint } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";
import {
  BRIEF_KIND_LABELS,
  FLAG_LABELS,
  LIFECYCLE_LABELS,
  NOVELTY_LABELS,
  toneLabel,
  type BriefOut,
  type ThemeDay,
} from "@/lib/news/analysis";

function latestPerTicker(briefs: BriefOut[]): BriefOut[] {
  const seen = new Set<string>();
  return briefs.filter((b) => (seen.has(b.ticker) ? false : (seen.add(b.ticker), true)));
}
import { cn } from "@/lib/utils";

const KINDS = ["morning", "region", "weekly", "holding", "theme"] as const;
type Kind = (typeof KINDS)[number];

type Load = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; briefs: BriefOut[]; themes: ThemeDay[] };

/** The briefs: what happened by region in the order the markets open, every line tied to its story. */
export function BriefView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const wanted = params.get("kind");
  const kind: Kind = KINDS.includes(wanted as Kind) ? (wanted as Kind) : "morning";
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [running, setRunning] = useState<"idle" | "queued" | "error">("idle");

  const refresh = useCallback(() => {
    const api = getBrowserApiClient();
    Promise.all([
      api.GET("/news/briefs", { params: { query: { kind, limit: 10 } } }),
      api.GET("/news/themes", { params: { query: { days: 7 } } }),
    ])
      .then(([briefs, themes]) => {
        // Defaulted response fields are always sent (see `Sent` in lib/news/analysis.ts).
        if (briefs.data) setLoad({ kind: "ok", briefs: briefs.data, themes: (themes.data ?? []) as ThemeDay[] });
        else setLoad({ kind: "error", message: `The API answered HTTP ${briefs.response.status}.` });
      })
      .catch((err: unknown) => setLoad({ kind: "error", message: err instanceof Error ? err.message : String(err) }));
  }, [kind]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoad({ kind: "loading" });
    refresh();
  }, [refresh]);

  async function runNow() {
    const { response } = await getBrowserApiClient().POST("/jobs/news-brief");
    setRunning(response.ok ? "queued" : "error");
  }

  const all = load.kind === "ok" ? load.briefs : [];
  // A theme digest is one brief per theme; every other kind shows its newest brief only.
  const shown = kind === "theme" ? latestPerTicker(all) : all.slice(0, 1);
  const latest = shown[0];
  const flagged = load.kind === "ok" ? load.themes.filter((t) => t.flag || (t.tone !== null && t.tone !== undefined)) : [];
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="News"
        title="Brief"
        description={
          <span className="block max-w-prose">
            What happened, ranked by how much of your portfolio it reaches, how serious it is and whether it is new. Repeats
            are left out. Every line is tied to a story and its stored articles; a line the model wrote is checked against
            them, and a plain headline replaces any that fails.
          </span>
        }
        aside={
          <Button variant="outline" size="sm" onClick={() => void runNow()} disabled={running === "queued"}>
            <PlayIcon aria-hidden="true" />
            {running === "queued" ? "Queued" : "Write now"}
          </Button>
        }
      />
      <NewsTabs active="brief" />
      <div role="tablist" aria-label="Brief type" className="flex gap-2">
        {KINDS.map((k) => (
          <button
            key={k}
            role="tab"
            aria-selected={k === kind}
            onClick={() => router.replace(`${pathname}?kind=${k}`, { scroll: false })}
            className={cn(
              "rounded-md border px-3 py-1 text-sm",
              k === kind ? "border-accent bg-accent-soft font-medium" : "border-border text-text-muted hover:text-text",
            )}
          >
            {BRIEF_KIND_LABELS[k]}
          </button>
        ))}
      </div>
      {running === "error" ? <AlertMessage title="Could not queue the brief">The API refused the request. Try again.</AlertMessage> : null}
      {load.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading…
        </p>
      ) : null}
      {load.kind === "error" ? <AlertMessage title="Could not load the briefs">{load.message}</AlertMessage> : null}
      {load.kind === "ok" && !latest ? (
        <EmptyState title={`No ${BRIEF_KIND_LABELS[kind].toLowerCase()} yet`}>
          The brief job is off by default because it can spend on a model; it writes plain headline lines without one. Turn it
          on under Config, Jobs, or press Write now.
        </EmptyState>
      ) : null}
      {shown.map((b) => (
        <Brief key={b.id} brief={b} />
      ))}
      {flagged.length > 0 ? (
        <section aria-labelledby="themes-heading" className="space-y-2">
          <h2 id="themes-heading" className="text-[22px] leading-tight font-semibold">
            Themes this week
          </h2>
          <ul className="m-0 list-none space-y-1 p-0 text-sm">
            {flagged.map((t) => (
              <li key={`${t.theme}${t.day}`}>
                <Link href={`/news/wire?topic=${encodeURIComponent(t.theme)}`} className="font-medium text-accent-ink hover:underline">
                  {t.theme}
                </Link>{" "}
                {t.flag ? `${FLAG_LABELS[t.flag]} on ${t.day}: ` : `${t.day}: `}
                {t.wire_items} items{t.regions.length ? ` in ${t.regions.join(", ")}` : ""}. Worldwide coverage: {toneLabel(t.tone)}
                {t.attention !== null && t.attention !== undefined ? `, ${t.attention.toFixed(3)}% of monitored coverage` : ""}.
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function Brief({ brief }: { brief: BriefOut }) {
  return (
    <div className="space-y-4">
      <p className="text-xs text-text-muted">
        {brief.ticker ? <span className="font-mono">{brief.ticker}</span> : null} <time dateTime={brief.period_start}>{formatDateTime(brief.period_start)}</time>{" "}
        to <time dateTime={brief.period_end}>{formatDateTime(brief.period_end)}</time>.{" "}
        {brief.body.generated_without_model ? "Plain headline lines, no model." : `Written by ${brief.model}.`}
        {brief.body.suppressed > 0 ? ` ${brief.body.suppressed} repeat${brief.body.suppressed === 1 ? "" : "s"} left out.` : ""}
      </p>
      {brief.body.sections.map((section) => (
        <Blueprint as="section" key={section.title} aria-label={section.title} className="space-y-3 px-5 py-4">
          <h2 className="text-[20px] leading-tight font-semibold">{section.title}</h2>
          <ul className="m-0 list-none divide-y divide-border p-0">
            {section.lines.map((line) => (
              <li key={line.story_id} className="space-y-1 py-2.5">
                <p className="flex items-start gap-2 text-base">
                  <SeverityDot severity={line.severity} className="mt-2" />
                  <span>
                    {line.ticker ? <span className="mr-1.5 font-mono text-xs text-accent-ink">{line.ticker}</span> : null}
                    {line.text}
                  </span>
                </p>
                {line.why ? <p className="pl-5 text-sm text-text-muted">{line.why}</p> : null}
                {line.watch ? <p className="pl-5 text-sm text-text-muted">Watch: {line.watch}</p> : null}
                <p className="flex flex-wrap items-center gap-x-3 pl-5 text-xs text-text-muted">
                  <Link href={`/news/stories/${line.story_id}`} className="text-accent-ink hover:underline">
                    Open the story
                  </Link>
                  <span>{LIFECYCLE_LABELS[line.lifecycle] ?? line.lifecycle}</span>
                  {line.novelty ? <span>{NOVELTY_LABELS[line.novelty as keyof typeof NOVELTY_LABELS] ?? line.novelty}</span> : null}
                  <NewsFeedback target="brief_line" targetId={`${brief.id}:${line.story_id}`} label={`brief line “${line.text}”`} />
                </p>
              </li>
            ))}
          </ul>
        </Blueprint>
      ))}
    </div>
  );
}
