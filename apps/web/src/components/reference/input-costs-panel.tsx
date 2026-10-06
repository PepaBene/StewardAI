"use client";

import { useCallback, useMemo, useState } from "react";

import { Sparkline } from "@/components/charts";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatCompact, formatPct } from "@/lib/charts/format";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { ASSUMPTION_LABEL, parseExposureInputs, windowChange } from "@/lib/macro/context";

/** Whole days between an observation date and today (monthly commodity series lag by weeks). */
function staleDays(date: string): number {
  const then = Date.parse(date);
  return Number.isNaN(then) ? 0 : Math.floor((Date.now() - then) / 86_400_000);
}

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
  const { state, reload } = useApiResource(fetcher);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const raw = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const view = useMemo(() => (raw ? parseExposureInputs(raw) : null), [raw]);

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
        <ul className="flex flex-col gap-1.5 text-sm">
          {view.assumptions.map((a) => (
            <li key={`${a.kind}|${a.name}`} className="flex flex-wrap items-baseline gap-x-2">
              <span className="text-text-muted">{ASSUMPTION_LABEL[a.kind] ?? a.kind}:</span> {a.name}{" "}
              <span className="num text-text-muted">
                {a.share === null
                  ? "(share not disclosed)"
                  : `(${formatPct(a.share, 0)}, ${a.origin === "ai" ? "reported in the filing" : "your estimate"})`}
              </span>
              {a.origin === "ai" ? (
                <>
                  <span className="text-xs text-text-muted">
                    Added by the AI{a.period ? ` from ${a.period}` : ""}
                    {a.sourceUrl ? (
                      <>
                        {" "}
                        ·{" "}
                        <a href={a.sourceUrl} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">
                          source
                        </a>
                      </>
                    ) : null}
                  </span>
                  {a.quote ? <q className="w-full text-xs text-text-muted">{a.quote}</q> : null}
                </>
              ) : (
                <span className="text-xs text-text-muted">Entered by you</span>
              )}
              <button
                type="button"
                className="text-xs text-accent-ink hover:underline"
                onClick={async () => {
                  const id = `${a.kind}|${a.name}`;
                  if (confirming !== id) {
                    setConfirming(id);
                    setFailure(null);
                    return;
                  }
                  const result = await getBrowserApiClient().POST("/holdings/{ticker}/exposures/remove", {
                    params: { path: { ticker } },
                    body: { kind: a.kind, name: a.name },
                  });
                  setConfirming(null);
                  if (result.error) setFailure("Could not remove it. Reload the page and try again.");
                  else reload();
                }}
              >
                {confirming === `${a.kind}|${a.name}` ? "Confirm: it will not be added again" : a.origin === "ai" ? "Overturn" : "Remove"}
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {failure ? <p className="text-xs text-danger">{failure}</p> : null}

      {view && view.unlinked.length > 0 ? (
        <p className="max-w-[68ch] text-xs text-text-muted">
          No stored price series for: {view.unlinked.map((u) => u.split(":")[1]).join(", ")}. They stay listed without a
          chart rather than being given a stand-in.
        </p>
      ) : null}

      {view && view.assumptions.length > 0 && view.series.length === 0 ? (
        <p className="max-w-[68ch] text-sm text-text-muted">
          No price series is stored for these exposures yet. Commodity prices and currency fixings arrive with the macro
          collection; other series come from Alpha Vantage, whose free allowance is shared, only when you request them.
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
                  <span className="num text-xs text-text-muted">
                    {formatDate(last.date)}
                    {staleDays(last.date) > 60 ? ` (${staleDays(last.date)} days old)` : ""}
                  </span>
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
