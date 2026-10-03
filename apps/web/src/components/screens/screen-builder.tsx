"use client";

import { useMemo } from "react";

import { AlertMessage } from "@/components/alert-message";
import { lastGood } from "@/components/coverage/resource-gate";
import { CriteriaEditor } from "@/components/screens/criteria-editor";
import { ResultsTable, SummaryTiles, UniverseTable } from "@/components/screens/results";
import { ProvenanceFooter, Segmented, StatePlaceholder } from "@/components/data-state";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { useQueryState } from "@/lib/coverage/use-query-state";
import { useLedgerGaps, useScreenSources, useValuations } from "@/lib/screens/hooks";
import { buildUniverse, inScope, isComplete, needsValuations, runScreen, summarize } from "@/lib/screens/logic";
import { metricDef } from "@/lib/screens/metrics";
import type { ScreenInputs } from "@/lib/screens/types";
import { parseScreenParams, screenQuery, type ScreenState, type ShowFilter } from "@/lib/screens/url";

const EMPTY: ScreenInputs["held"] = [];

/**
 * The screen builder. Universe: the app's own held positions and watchlist, not the global
 * market. Every figure is what an API endpoint returned; the browser only compares it with the
 * thresholds typed here. Filter state lives in the URL.
 */
export function ScreenBuilder() {
  const { params, replace } = useQueryState();
  const state = useMemo(() => parseScreenParams(params), [params]);
  const set = (patch: Partial<ScreenState>) => replace(screenQuery({ ...state, ...patch }));

  const sources = useScreenSources();
  const held = lastGood(sources.held);
  const watch = lastGood(sources.watch);
  const prices = lastGood(sources.price);
  const { gaps } = useLedgerGaps();

  const universe = useMemo(() => buildUniverse(held ?? EMPTY, watch ?? []), [held, watch]);
  const scoped = useMemo(() => universe.filter((e) => inScope(e, state.scope)), [universe, state.scope]);
  const complete = useMemo(() => state.criteria.filter(isComplete), [state.criteria]);
  const useVal = needsValuations(complete);
  const { valuations, reload: reloadValuations, refreshFailed } = useValuations(scoped, useVal);

  const inputs: ScreenInputs = useMemo(
    () => ({ held: held ?? EMPTY, watchlist: watch ?? [], prices: prices ?? [], valuations, ledger: gaps }),
    [held, watch, prices, valuations, gaps],
  );
  const outcomes = useMemo(() => (complete.length ? runScreen(scoped, complete, inputs, state.missing) : []), [scoped, complete, inputs, state.missing]);
  const summary = useMemo(() => summarize(outcomes), [outcomes]);
  const shown = outcomes.filter((o) =>
    state.show === "all" ? true : state.show === "matched" ? o.kind === "matched" : state.show === "failed" ? o.kind === "failed" : o.kind === "excluded" || o.kind === "unknown",
  );

  // Figures kept on screen after their latest refresh failed: still shown, but not silently.
  const staleRefreshes = [...refreshFailed].filter((k) => valuations.get(k)?.kind === "ok").length;
  const failedSources = [
    sources.held.kind === "error" ? `holdings (${sources.held.title})` : null,
    sources.watch.kind === "error" ? `watchlist (${sources.watch.title})` : null,
    sources.price.kind === "error" ? `prices (${sources.price.title})` : null,
    staleRefreshes > 0 ? `${staleRefreshes} valuation${staleRefreshes === 1 ? "" : "s"} could not be refreshed (the previous figures are shown)` : null,
  ].filter(Boolean);
  const loadingUniverse = sources.held.kind === "loading" || sources.watch.kind === "loading";
  const metricsUsed = [...new Set(complete.map((c) => metricDef(c.metric)!))];
  const known = outcomes.filter((o) => o.kind === "matched" || o.kind === "failed").length;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Research & Verification"
        title="Screens"
        description="Screen the app's own universe on figures the API already returns, and see exactly who was left out and why."
        aside={
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => {
              sources.reload();
              reloadValuations();
            }}
          >
            Refresh data
          </Button>
        }
      />

      <AlertMessage tone="info" title="Universe: held positions and watchlist only">
        This screen covers the {universe.length} tickers in this app (held positions and watchlist items), not the global market. There is no market-wide screener endpoint, so a result here says nothing about securities you do not hold or watch.
      </AlertMessage>

      {failedSources.length ? (
        <AlertMessage title="Some inputs failed to load">
          {failedSources.join("; ")}. The last loaded figures are kept where they exist; entities that depend on missing inputs are reported as unknown, never as matches.
        </AlertMessage>
      ) : null}

      <Blueprint as="section" aria-labelledby="criteria-heading" className="flex flex-col gap-3 px-5 py-5">
        <PanelHeading kicker="Builder" title={<span id="criteria-heading">Metrics and thresholds</span>} />
        <CriteriaEditor
          criteria={state.criteria}
          onChange={(criteria) => set({ criteria })}
          missing={state.missing}
          onMissing={(missing) => set({ missing })}
          scope={state.scope}
          onScope={(scope) => set({ scope })}
        />
        <p className="text-xs text-text-muted">The criteria, treatment and universe are saved in the page address, so a screen can be bookmarked or shared.</p>
      </Blueprint>

      <Blueprint as="section" aria-labelledby="result-heading" className="flex flex-col gap-3 px-5 py-5">
        <PanelHeading kicker="Result" title={<span id="result-heading">Who matched, who failed, who was left out</span>} />
        {loadingUniverse ? (
          <p role="status" className="text-sm text-text-muted">Loading holdings and watchlist…</p>
        ) : complete.length === 0 ? (
          <>
            <p className="text-sm text-text">
              {scoped.length} {scoped.length === 1 ? "entity is" : "entities are"} in scope. Add a criterion with a numeric threshold to run the screen; until then nothing is matched or excluded.
            </p>
            <UniverseTable entities={scoped} />
          </>
        ) : scoped.length === 0 ? (
          <StatePlaceholder state="not_covered" reason="The universe is empty: no held position and no watchlist item in this scope, so there is nothing to screen." />
        ) : (
          <>
            <p className="max-w-prose text-sm text-text">
              {summary.pending > 0
                ? `Loading valuation figures: ${scoped.length - summary.pending} of ${scoped.length} entities resolved so far.`
                : `${summary.matched} of ${summary.universe} entities match; ${summary.excluded + summary.unknown} ${state.missing === "exclude" ? "were excluded" : "are shown as unknown"} because a metric is unknown, and ${summary.failed} failed a criterion on known data.`}
            </p>
            <SummaryTiles s={summary} treatment={state.missing} hasCriteria />
            <Segmented<ShowFilter>
              label="Show"
              value={state.show}
              onChange={(show) => set({ show })}
              options={[
                { id: "all", label: `All (${summary.universe})` },
                { id: "matched", label: `Matched (${summary.matched})` },
                { id: "failed", label: `Failed (${summary.failed})` },
                { id: "excluded", label: `${state.missing === "exclude" ? "Excluded" : "Unknown"} (${summary.excluded + summary.unknown})` },
              ]}
            />
            <ResultsTable outcomes={shown} criteria={complete} />
            <ProvenanceFooter
              provenance={{
                source: `${[...new Set(metricsUsed.map((m) => m.endpoint))].join("; ")}${useVal ? "; GET /data/coverage (paid_only and not_covered reasons)" : ""}`,
                asOf: prices?.map((p) => p.asOf).filter((d): d is string => d !== null).sort().at(-1) ?? null,
                method: "Figures are exactly as the API returned them; the browser only compares them with your thresholds. A stale or missing figure never passes and is never read as zero. A definite failure outranks an unknown on another criterion.",
                state: failedSources.length || summary.pending > 0 ? "partial" : summary.excluded + summary.unknown > 0 ? "partial" : "verified",
                reason: summary.excluded + summary.unknown > 0 ? `${summary.excluded + summary.unknown} of ${summary.universe} entities have an unknown metric.` : null,
                coverage: { covered: known, total: summary.universe, unit: "entities with known data on every criterion or a definite fail" },
              }}
            />
          </>
        )}
      </Blueprint>
    </div>
  );
}
