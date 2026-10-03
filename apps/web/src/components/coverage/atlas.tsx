"use client";

import { useMemo } from "react";

import { Scorecard } from "@/components/coverage/scorecard";
import { AtlasFiltersBar } from "@/components/coverage/atlas-filters";
import { AtlasMatrix } from "@/components/coverage/atlas-matrix";
import { GlobalReach } from "@/components/coverage/global-reach";
import { LedgerTable } from "@/components/coverage/ledger-table";
import { PlanGaps } from "@/components/coverage/plan-gaps";
import { ResourceGate, lastGood } from "@/components/coverage/resource-gate";
import { GlyphLegend } from "@/components/coverage/state-glyph";
import { SummaryStrip } from "@/components/coverage/summary-strip";
import { ChartFrame } from "@/components/data-state";
import { PageHeader } from "@/components/page-header";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { useDataContext, useHeldPriceCount, useIdentityGaps, useMeasuredCoverage, useCoverageSummary } from "@/lib/coverage/hooks";
import { LEDGER_ROW_CAP, atlasFooterState, buildAtlas, gapsFirst, costScore, drillEntries, globalReachScore, identityScore, priceScore, provenanceScore, returnWindowScore } from "@/lib/coverage/logic";
import { atlasQuery, parseAtlasParams } from "@/lib/coverage/url";
import type { AtlasDrill, AtlasFilters } from "@/lib/coverage/types";
import { useQueryState } from "@/lib/coverage/use-query-state";
import { useDataCoverage, useZeroCostGate } from "@/lib/source-coverage/hooks";

function useOptions() {
  const sources = useCoverageSummary("source");
  const metrics = useCoverageSummary("metric");
  const states = useCoverageSummary("state");
  const keys = (s: typeof sources.state) => {
    const d = lastGood(s);
    return d ? d.groups.map((g) => g.key).filter((k): k is string => k !== null) : [];
  };
  return { providers: keys(sources.state), datasets: keys(metrics.state), states: keys(states.state) };
}

/** The coverage atlas page body: scorecard, filterable matrix with drill-down, summary, global reach and plan gaps. */
export function CoverageAtlas() {
  const { params, replace } = useQueryState();
  const { filters, drill } = useMemo(() => parseAtlasParams(params), [params]);
  const options = useOptions();
  const setFilters = (f: AtlasFilters) => replace(atlasQuery(f, drill));
  const setDrill = (d: AtlasDrill) => replace(atlasQuery(filters, d));

  const ledger = useDataCoverage({
    provider: filters.provider || undefined,
    dataset: filters.dataset || undefined,
    entityKey: filters.entity || undefined,
    state: filters.state || undefined,
    period: filters.period || undefined,
    entityKind: filters.kind === "any" ? undefined : filters.kind,
  });
  const summary = useCoverageSummary(filters.group, filters);

  const dataContext = useDataContext();
  const identityGaps = useIdentityGaps();
  const heldPrices = useHeldPriceCount();
  const gate = useZeroCostGate();
  const measured = useMeasuredCoverage();

  const scoreItems = [
    identityScore(lastGood(dataContext.state), lastGood(identityGaps)),
    provenanceScore(),
    priceScore(lastGood(heldPrices)),
    returnWindowScore(lastGood(dataContext.state)),
    globalReachScore(lastGood(measured.state)),
    costScore(lastGood(gate.state)),
  ];

  const entries = lastGood(ledger.state);
  const atlas = useMemo(() => (entries ? buildAtlas(entries) : []), [entries]);
  const drilled = entries && drill ? drillEntries(entries, drill.dataset, drill.kind) : null;
  const total = entries?.length ?? 0;
  const newest = entries && entries.length ? entries.map((e) => e.checkedAt).sort().at(-1)! : null;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Research & Verification"
        title="Coverage atlas"
        description="What data exists for which dataset and entity, in what state and why. Every count is of coverage-ledger entries, not of the global market."
      />

      <Scorecard items={scoreItems} />

      <Blueprint as="section" aria-labelledby="filters-heading" className="flex flex-col gap-3 px-5 py-5">
        <PanelHeading kicker="Filters" title={<span id="filters-heading">Dataset, entity, period and provider</span>} />
        <AtlasFiltersBar filters={filters} options={options} onChange={setFilters} />
        <p className="text-xs text-text-muted">Filters live in the page address, so a filtered atlas can be bookmarked or shared. The backend applies them; nothing is filtered in the browser.</p>
      </Blueprint>

      <ChartFrame
        id="atlas"
        kicker="Atlas"
        title="Dataset by entity kind"
        takeaway={
          entries
            ? total === 0
              ? "The ledger has no entry for these filters. That is unknown, not zero coverage."
              : `${total} ledger ${total === 1 ? "entry" : "entries"} across ${atlas.length} dataset${atlas.length === 1 ? "" : "s"} match the filters.`
            : undefined
        }
        units="Ledger entries per cell, as returned by GET /data/coverage; only entities with a ledger entry appear"
        hasValue={total > 0}
        controls={null}
        provenance={{
          source: "Coverage ledger via GET /data/coverage and GET /data/coverage/summary",
          asOf: newest ? newest.slice(0, 10) : null,
          method: "Backend filters by provider, dataset, entity key, state, period and entity kind; the matrix counts the returned rows. A partial state means the ledger records fewer periods than expected.",
          // The badge describes the data, not the fetch: a ledger of only gaps is not "verified".
          state: ledger.state.kind === "error" ? "unavailable" : atlasFooterState(entries ?? []),
          reason: total === 0 ? "No ledger entry matches. The ledger holds only entities a collector has checked." : null,
          coverage: null,
        }}
      >
        <div className="flex flex-col gap-4">
          <ResourceGate state={ledger.state} label="coverage ledger">
            {() => (
              <>
                <AtlasMatrix rows={atlas} drill={drill} onDrill={setDrill} />
                <details className="border border-border-soft">
                  <summary className="cursor-pointer px-3 py-1.5 text-xs font-medium text-accent-ink select-none">State key (icon, fill pattern, meaning)</summary>
                  <div className="border-t border-border-soft p-3">
                    <GlyphLegend />
                  </div>
                </details>
              </>
            )}
          </ResourceGate>
        </div>
      </ChartFrame>

      <Blueprint as="section" aria-labelledby="ledger-heading" id="ledger" className="flex scroll-mt-24 flex-col gap-3 px-5 py-5">
        <PanelHeading
          kicker="Drill-down"
          title={<span id="ledger-heading">{drill ? `Ledger rows: ${drill.dataset}, ${drill.kind}` : "Ledger rows"}</span>}
          action={
            drill ? (
              <button type="button" onClick={() => setDrill(null)} className="text-xs text-accent-ink hover:underline">
                Show all matching rows
              </button>
            ) : null
          }
        />
        <p className="text-xs text-text-muted">
          Select a matrix cell to see its rows; otherwise the rows matching the filters are listed, gaps first (up to {LEDGER_ROW_CAP}; a longer list says how many are left out).
        </p>
        <ResourceGate state={ledger.state} label="ledger rows">
          {(rows) => (
            <LedgerTable
              entries={drilled ?? gapsFirst(rows)}
              totalMatching={drilled ? drilled.length : rows.length}
              caption="Coverage ledger rows with state, reason and last check"
            />
          )}
        </ResourceGate>
      </Blueprint>

      <Blueprint as="section" aria-labelledby="summary-heading" id="summary" className="flex scroll-mt-24 flex-col gap-3 px-5 py-5">
        <PanelHeading kicker="Summary" title={<span id="summary-heading">Entries grouped by {filters.group}</span>} />
        <ResourceGate state={summary.state} label="coverage summary">
          {(d) => (d.totalEntries === 0 ? <p className="text-sm text-text-muted">The ledger has no entry for these filters.</p> : <SummaryStrip data={d} />)}
        </ResourceGate>
      </Blueprint>

      <ResourceGate state={measured.state} label="measured coverage">
        {(rows) => <GlobalReach rows={rows} />}
      </ResourceGate>

      <PlanGaps />
    </div>
  );
}
