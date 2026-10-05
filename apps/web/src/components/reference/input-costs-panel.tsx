"use client";

import { useCallback, useMemo } from "react";

import { Sparkline } from "@/components/charts";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatCompact, formatPct } from "@/lib/charts/format";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { ASSUMPTION_LABEL, parseExposureInputs, windowChange } from "@/lib/macro/context";

/**
 * GET /holdings/{ticker}/exposure-context: commodity and currency series for the exposures you
 * entered in the holding profile. A price move is context only; it is never turned into a margin
 * estimate, and the share beside each exposure is your own assumption.
 */
export function InputCostsPanel({ ticker }: { ticker: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/exposure-context", { params: { path: { ticker } } }),
    [ticker],
  );
  const { state } = useApiResource(fetcher);
  const raw = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const view = useMemo(() => parseExposureInputs(raw), [raw]);

  return (
    <Blueprint aria-labelledby="inputs-heading" className="flex min-w-0 flex-col gap-3 px-[22px] py-5">
      <PanelHeading kicker="Input costs and currency" title={<span id="inputs-heading">Commodity and currency context</span>} />
      {state.kind === "loading" && !view ? <p className="text-sm text-text-muted">Loading…</p> : null}
      {state.kind === "error" && !view ? (
        <p className="text-sm text-text-muted">
          Input context unavailable: {state.title}
          {state.detail ? `. ${state.detail}` : ""}
        </p>
      ) : null}

      {view && view.assumptions.length === 0 ? (
        <p className="max-w-[68ch] text-sm text-text-muted">
          No commodity or currency exposure is entered for this holding, so no price series is attached. Add one in the
          holding profile to track an input cost or a revenue currency here.
        </p>
      ) : null}

      {view && view.assumptions.length > 0 ? (
        <ul className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
          {view.assumptions.map((a) => (
            <li key={`${a.kind}|${a.name}`}>
              <span className="text-text-muted">{ASSUMPTION_LABEL[a.kind] ?? a.kind}:</span> {a.name}{" "}
              <span className="num text-text-muted">{a.share === null ? "(share not entered)" : `(${formatPct(a.share, 0)}, your estimate)`}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {view && view.assumptions.length > 0 && view.series.length === 0 ? (
        <p className="max-w-[68ch] text-sm text-text-muted">
          No price series has been fetched for these exposures yet. The Alpha Vantage free allowance is shared, so this
          fills only when you request it.
        </p>
      ) : null}

      {view && view.series.length > 0 ? (
        <ul className="grid grid-cols-1 gap-px border border-border bg-border sm:grid-cols-2 xl:grid-cols-3">
          {view.series.map((s) => {
            const last = s.points[s.points.length - 1];
            const delta = windowChange(s);
            return (
              <li key={s.metric} className="flex flex-col gap-1 bg-surface px-3 py-2.5">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate text-sm font-medium">{s.metric}</span>
                  <span className="num text-xs text-text-muted">{formatDate(last.date)}</span>
                </div>
                <div className="flex items-end justify-between gap-3">
                  <div>
                    <div className="num text-lg leading-tight">
                      {formatCompact(last.value)} <span className="text-xs text-text-muted">{s.unit ?? s.currency ?? ""}</span>
                    </div>
                    <div className="num text-xs text-text-muted">
                      {delta === null ? "One data point" : `${formatPct(delta, 1, true)} over ${s.points.length} observations`}
                    </div>
                  </div>
                  <Sparkline
                    values={s.points.map((p) => p.value)}
                    width={96}
                    height={32}
                    className="h-8 w-24 shrink-0"
                    label={`${s.metric}, ${s.points.length} observations`}
                    formatValue={formatCompact}
                  />
                </div>
                {s.sourceUrl ? (
                  <a href={s.sourceUrl} target="_blank" rel="noreferrer" className="text-xs text-accent-ink hover:underline">
                    Source
                  </a>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}

      {view && view.limitations.length > 0 ? (
        <ul className="space-y-0.5 text-xs text-text-muted">
          {view.limitations.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      ) : null}
    </Blueprint>
  );
}
