"use client";

import Link from "next/link";
import { useCallback, useMemo } from "react";

import { MacroExposureEditor } from "@/components/reference/macro-exposure-editor";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPct } from "@/lib/charts/format";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { BASIS_LABEL, change, contextView, formatUnitValue, type ContextCard, type ContextEffect } from "@/lib/macro/context";

/**
 * GET /macro/context/{ticker}: the latest country figures for the places this holding is exposed
 * to, with each figure's place in its own history, and the exposure mapping that links them to
 * the holding. Only mapped exposure produces an effect; the remainder stays "unknown".
 */
export function MacroContextPanel({ ticker }: { ticker: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/macro/context/{ticker}", { params: { path: { ticker }, query: {} } }),
    [ticker],
  );
  const { state } = useApiResource(fetcher);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const view = useMemo(() => (data ? contextView(data) : null), [data]);

  return (
    <>
    <Blueprint aria-labelledby="macro-heading" className="flex min-w-0 flex-col gap-3 px-[22px] py-5">
      <PanelHeading
        kicker="Country and industry context"
        title={<span id="macro-heading">What is happening where it operates</span>}
      />
      {state.kind === "loading" && !view ? <p className="text-sm text-text-muted">Loading…</p> : null}
      {state.kind === "error" && !view ? (
        <p className="text-sm text-text-muted">
          Context unavailable: {state.title}
          {state.detail ? `. ${state.detail}` : ""}
        </p>
      ) : null}

      {view && view.exposureUnknown ? (
        <p className="max-w-[68ch] border border-dashed border-border px-3 py-2 text-sm text-text-muted">
          This holding has no mapped country or product exposure, so no figure below is attributed to it. Map its revenue
          by region in the{" "}
          <a href="#exposure-editor" className="text-accent-ink underline">
            exposure editor
          </a>{" "}
          below to see how these figures could affect it.
        </p>
      ) : null}

      {view && view.cards.length === 0 && !view.exposureUnknown ? (
        <p className="max-w-[68ch] text-sm text-text-muted">
          No macro observations are stored for the mapped countries yet. The public data job fetches them once the
          worker has run the source audit; see its schedule in{" "}
          <Link href="/config/jobs" className="text-accent-ink hover:underline">
            Config: Jobs
          </Link>{" "}
          and check{" "}
          <Link href="/data-sources" className="text-accent-ink hover:underline">
            Data sources
          </Link>{" "}
          to see what each provider returned.
        </p>
      ) : null}

      {view && view.cards.length > 0 ? (
        <ul className="grid grid-cols-1 gap-px border border-border bg-border sm:grid-cols-2 xl:grid-cols-3">
          {view.cards.map((c) => (
            <FigureCard key={c.key} card={c} />
          ))}
        </ul>
      ) : null}

      {view && view.effects.length > 0 ? (
        <div className="space-y-1.5">
          <h3 className="text-sm font-semibold">How this could affect the holding</h3>
          <ul className="divide-y divide-border-soft border border-border">
            {view.effects.map((e) => (
              <EffectRow key={e.key} effect={e} />
            ))}
          </ul>
        </div>
      ) : null}

      {view && view.cards.length > 0 ? (
        <p className="text-xs">
          <Link href="/macro" className="text-accent-ink hover:underline">
            See every macro series with its revisions
          </Link>
        </p>
      ) : null}

      {view && view.gaps.length > 0 ? (
        <details className="text-sm">
          <summary className="cursor-pointer text-accent-ink hover:underline">
            {view.gaps.length} dataset{view.gaps.length === 1 ? "" : "s"} not available
          </summary>
          <ul className="mt-2 space-y-1 text-xs text-text-muted">
            {view.gaps.map((g) => (
              <li key={g.key}>
                <span className="font-mono text-text">
                  {g.provider}/{g.dataset}
                </span>{" "}
                for {g.geography}: {g.state.replaceAll("_", " ")}
                {g.reason ? `, ${g.reason}` : ""}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </Blueprint>
    <MacroExposureEditor ticker={ticker} />
    </>
  );
}

function FigureCard({ card }: { card: ContextCard }) {
  const delta = change(card);
  const range = card.fiveYear ?? card.tenYear;
  const rangeLabel = card.fiveYear ? "five-year" : "ten-year";
  return (
    <li className="flex flex-col gap-1 bg-surface px-3 py-2.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="truncate text-sm font-medium" title={card.seriesId}>
          {card.seriesId}
        </span>
        <span className="text-xs text-text-muted">
          {card.geography}, {card.source}
        </span>
      </div>
      <div className="num text-lg leading-tight">{formatUnitValue(card.value, card.unit)}</div>
      <div className="num text-xs text-text-muted">
        {card.value === null
          ? `No value (${card.accessState.replaceAll("_", " ")})`
          : delta === null
            ? "No previous release"
            : `${delta > 0 ? "Up" : delta < 0 ? "Down" : "Unchanged"}${delta === 0 ? "" : ` ${Math.abs(delta).toFixed(2)}`} on ${card.previousPeriod ? formatDate(card.previousPeriod) : "previous"}`}
      </div>
      {range ? (
        <div
          role="img"
          aria-label={`${formatUnitValue(card.value, card.unit)} within its ${rangeLabel} range of ${range.low} to ${range.high}`}
          className="mt-0.5"
        >
          <div className="relative h-3">
            <span className="absolute inset-x-0 top-1/2 h-px bg-border" />
            <span className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 bg-accent" style={{ left: `${range.pos}%` }} />
          </div>
          <div className="num flex justify-between text-xs text-text-faint">
            <span>{range.low}</span>
            <span>{rangeLabel} range</span>
            <span>{range.high}</span>
          </div>
        </div>
      ) : (
        <div className="text-xs text-text-muted">Range not available</div>
      )}
      <p className="text-xs text-text-muted">
        Period {formatDate(card.period)}
        {card.releaseAt ? `, released ${formatDate(card.releaseAt)}` : ", release date not recorded"}.{" "}
        <a href={card.sourceUrl} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">
          Source
        </a>
      </p>
    </li>
  );
}

function EffectRow({ effect }: { effect: ContextEffect }) {
  return (
    <li className="space-y-0.5 px-3 py-2 text-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4">
        <span className="font-medium">
          {effect.segment}
          {effect.geography ? <span className="text-text-muted">, {effect.geography}</span> : null}
          {effect.hsCode ? <span className="num text-text-muted">, HS {effect.hsCode}</span> : null}
        </span>
        <span className="num text-xs text-text-muted">
          {effect.weight === null ? "Weight not set" : `${formatPct(effect.weight, 0)} of the business`}
          {effect.unknownWeight !== null && effect.unknownWeight > 0 ? `, ${formatPct(effect.unknownWeight, 0)} unmapped` : ""}
        </span>
      </div>
      <p className="text-xs text-text-muted">
        {BASIS_LABEL[effect.basis] ?? effect.basis}. {effect.explanation}
      </p>
    </li>
  );
}
