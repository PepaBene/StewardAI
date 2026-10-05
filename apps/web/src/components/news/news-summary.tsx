import { CircleAlertIcon } from "lucide-react";
import Link from "next/link";

import { ChartEmpty, HBar } from "@/components/charts";
import { SeverityTimeline } from "@/components/news/severity-timeline";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import type { components } from "@/lib/api/schema";
import type { SourceTrustOut } from "@/lib/api/client";
import { formatRelative } from "@/lib/format";
import { categorySegments, healthSummary, severityColumns, timelineTitle, trustBars } from "@/lib/news/stats";
import { cn } from "@/lib/utils";

type NewsStats = components["schemas"]["NewsStats"];
type SourcesHealth = components["schemas"]["SourcesHealth"];

export type Loaded<T> = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; data: T };

function Pending({ state, label }: { state: Loaded<unknown>; label: string }) {
  if (state.kind === "loading") return <ChartEmpty label={`Loading ${label}…`} minHeight={150} />;
  if (state.kind === "error") return <ChartEmpty label={`Could not load ${label}: ${state.message}`} minHeight={150} />;
  return null;
}

/** The three panels above the feed: severity timeline, categories, and source trust + feed health. */
export function NewsSummary({
  stats,
  health,
  trust,
  now,
}: {
  stats: Loaded<NewsStats>;
  health: Loaded<SourcesHealth>;
  trust: Loaded<SourceTrustOut[]>;
  now: Date;
}) {
  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2 xl:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)_minmax(0,1fr)]">
      <Blueprint aria-labelledby="news-timeline-heading" className="flex flex-col gap-4 px-5 py-5 lg:col-span-2 xl:col-span-1">
        <PanelHeading
          kicker="Severity timeline"
          title={<span id="news-timeline-heading">{stats.kind === "ok" ? timelineTitle(stats.data) : "Stories per day"}</span>}
        />
        {stats.kind === "ok" ? (
          <>
            <SeverityTimeline columns={severityColumns(stats.data)} />
            {stats.data.unclassified > 0 ? (
              <p className="text-xs text-text-muted">{stats.data.unclassified} not classified yet.</p>
            ) : null}
          </>
        ) : (
          <Pending state={stats} label="the timeline" />
        )}
      </Blueprint>

      <Blueprint aria-labelledby="news-categories-heading" className="flex flex-col gap-4 px-5 py-5">
        <PanelHeading kicker="By category" title={<span id="news-categories-heading">What the stories are about</span>} />
        {stats.kind === "ok" ? (
          (() => {
            const segments = categorySegments(stats.data);
            const total = segments.reduce((s, x) => s + x.value, 0);
            return segments.length === 0 ? (
              <ChartEmpty label="No classified stories in this window" minHeight={130} />
            ) : (
              <div className="flex flex-col gap-2">
                <ul className="m-0 flex list-none flex-col gap-2 p-0" aria-label="Stories by event type">
                  {segments.map((s) => (
                    <li key={s.label} className="grid grid-cols-[minmax(0,8.5rem)_1fr_4.5rem] items-center gap-2 text-xs">
                      <span className="truncate" title={s.label}>
                        {s.label}
                      </span>
                      <HBar value={s.value} max={segments[0].value} label={`${s.label} stories`} formatValue={(v) => String(v)} />
                      <span className="num text-right text-text-muted">
                        {s.value} · {Math.round((s.value / total) * 100)}%
                      </span>
                    </li>
                  ))}
                </ul>
                <p className="text-xs text-text-muted">Share of the {total} classified stories in this window.</p>
              </div>
            );
          })()
        ) : (
          <Pending state={stats} label="the categories" />
        )}
      </Blueprint>

      <Blueprint aria-labelledby="news-trust-heading" className="flex flex-col gap-3 px-5 py-5">
        <PanelHeading kicker="Source trust" title={<span id="news-trust-heading">From your feedback</span>} />
        {trust.kind === "ok" ? (
          trust.data.length === 0 ? (
            <p className="text-sm text-text-muted">No feedback has moved any source yet: every source is at 1.</p>
          ) : (
            <ul className="m-0 flex list-none flex-col gap-2 p-0" aria-label="Trust score by source">
              {trustBars(trust.data).map((b) => (
                <li key={b.host} className="grid grid-cols-[minmax(0,7.5rem)_1fr_2.25rem] items-center gap-2 text-xs">
                  <span className="truncate" title={b.host}>
                    {b.host}
                  </span>
                  <span
                    role="meter"
                    aria-label={`${b.host} trust`}
                    aria-valuemin={0}
                    aria-valuemax={1}
                    aria-valuenow={b.score}
                    aria-valuetext={`${b.score.toFixed(2)}${b.demoted ? ", demoted one tier" : ""}`}
                    className="h-2 bg-border-soft"
                    title={`${b.wrong} marked wrong · ${b.useful} useful${b.demoted ? " · demoted one tier" : ""}`}
                  >
                    <span className={cn("block h-full", b.demoted ? "bg-accent-300" : "bg-accent")} style={{ width: `${Math.max(0, Math.min(1, b.score)) * 100}%` }} />
                  </span>
                  <span className="num text-right text-text-muted">
                    {b.score.toFixed(2)}
                    {b.demoted ? <span className="sr-only"> (demoted)</span> : null}
                  </span>
                </li>
              ))}
            </ul>
          )
        ) : (
          <Pending state={trust} label="source trust" />
        )}
        {health.kind === "ok" ? <FeedHealth health={health.data} now={now} /> : null}
        {health.kind === "error" ? <p className="text-xs text-text-muted">Feed health unavailable: {health.message}</p> : null}
        <p className="mt-auto text-xs">
          <Link href="/activity#source-trust" className="text-accent-ink hover:underline">
            Every judged source
          </Link>
          {" · "}
          <Link href="/config/news-sources" className="text-accent-ink hover:underline">
            Feeds
          </Link>
        </p>
      </Blueprint>
    </div>
  );
}

function FeedHealth({ health, now }: { health: SourcesHealth; now: Date }) {
  const s = healthSummary(health);
  return (
    <details className="border-t border-border-soft pt-2 text-xs">
      <summary className="cursor-pointer text-text-muted">
        <span className="num font-medium text-text">
          {s.healthy} of {s.enabled}
        </span>{" "}
        enabled feeds healthy
        {s.failing.length > 0 ? (
          <span className="ml-1 inline-flex items-center gap-1 text-text">
            <CircleAlertIcon aria-hidden="true" className="size-3.5 text-warning-signal" />
            {s.failing.length} need attention
          </span>
        ) : null}
      </summary>
      <p className="mt-1 text-text-muted">{health.rule} ({health.window_hours} h).</p>
      <ul className="mt-1.5 space-y-1">
        {health.sources.map((src) => (
          <li key={src.id} className="flex flex-wrap items-baseline justify-between gap-x-2">
            <span className={cn(!src.enabled && "text-text-faint")}>
              {src.name} <span className="font-mono text-[10px] text-text-faint">T{src.tier}</span>
            </span>
            <span className="num text-text-muted">
              {!src.enabled
                ? "disabled"
                : src.healthy
                  ? `ok · ${src.last_fetched_at ? formatRelative(new Date(src.last_fetched_at), now) : "never"}`
                  : src.last_error
                    ? `failing: ${src.last_error}`
                    : src.last_fetched_at
                      ? `stale · ${formatRelative(new Date(src.last_fetched_at), now)}`
                      : "never fetched"}
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}
