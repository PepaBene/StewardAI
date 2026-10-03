"use client";

import { useCallback, useMemo, useState } from "react";

import { Legend } from "@/components/charts";
import { ChartFrame, DataStateBadge, Segmented } from "@/components/data-state";
import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  aggregateProvenance,
  buildExposureTreemap,
  buildLookThroughSankey,
  DIMENSIONS,
  DIMENSION_LABEL,
  fundDataState,
  fundStatusLabel,
  oldestFundAsOf,
  treemapNotes,
  usableOverlap,
  type Dimension,
} from "@/lib/xray/exposure";
import { compact, date, pct, weightPct } from "@/lib/xray/format";
import type { AggregateReport, FundOverlapReport } from "@/lib/xray/types";

import { DataTable, Facts } from "./facts";
import { LookThroughSankeyChart, SANKEY_LEGEND } from "./lookthrough-sankey";
import { NestedTreemap } from "./nested-treemap";
import { Panel, ResourceShell } from "./resource-shell";

const TODAY = () => new Date().toISOString().slice(0, 10);

function useAggregate(asOf: string) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/funds/aggregate", { params: { query: { top: 40, ...(asOf ? { as_of: asOf } : {}) } } }),
    [asOf],
  );
  return useApiResource(fetcher);
}

function useOverlap(asOf: string) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/funds/overlap", { params: { query: asOf ? { as_of: asOf } : {} } }),
    [asOf],
  );
  return useApiResource(fetcher);
}

/** Issuer, sector and geography exposure with the position -> fund -> issuer Sankey. */
export function ExposureSection({ asOf, onAsOfChange }: { asOf: string; onAsOfChange: (v: string) => void }) {
  const aggregate = useAggregate(asOf);
  const overlap = useOverlap(asOf);
  const overlapData: FundOverlapReport | null =
    overlap.state.kind === "ok" ? overlap.state.data : overlap.state.kind === "error" ? overlap.state.previous : null;
  const overlapLoading = overlap.state.kind === "loading";
  return (
    <div className="flex flex-col gap-5">
      <Panel
        id="xray-asof"
        kicker="Point in time"
        title="As-of date"
        description="Default is the latest stored data. Pick a past date to see the exposure from only the records known by the end of that day; fund holdings keep their own as-of dates either way."
      >
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <label className="flex items-center gap-2">
            <span className="text-text-muted">Answer as of</span>
            <input
              type="date"
              value={asOf}
              max={TODAY()}
              onChange={(e) => onAsOfChange(e.target.value)}
              className="h-8 border border-border bg-transparent px-2 text-sm text-text"
            />
          </label>
          {asOf ? (
            <button type="button" onClick={() => onAsOfChange("")} className="text-xs font-medium text-accent-ink hover:underline">
              Back to latest
            </button>
          ) : (
            <span className="text-xs text-text-muted">Showing the latest.</span>
          )}
        </div>
      </Panel>
      <ResourceShell state={aggregate.state} label="Fund aggregate">
        {(report, lastGood) => (
          <AggregateBody key={report.as_of} report={report} overlap={usableOverlap(report, overlapData)} overlapLoading={overlapLoading} lastGood={lastGood} />
        )}
      </ResourceShell>
    </div>
  );
}

function AggregateBody({
  report,
  overlap,
  overlapLoading,
  lastGood,
}: {
  report: AggregateReport;
  overlap: FundOverlapReport | null;
  overlapLoading: boolean;
  lastGood: boolean;
}) {
  const [dimension, setDimension] = useState<Dimension>("sector");
  const provenance = useMemo(() => {
    const p = aggregateProvenance(report);
    return lastGood ? { ...p, state: "unavailable" as const, reason: "The refresh failed; this is the last value that loaded." } : p;
  }, [report, lastGood]);
  const treemap = useMemo(() => buildExposureTreemap(report, dimension), [report, dimension]);
  const sankey = useMemo(() => buildLookThroughSankey(report, overlap), [report, overlap]);
  const oldest = oldestFundAsOf(report.funds);
  const cov = provenance.coverage;
  const unknown = report.unknown;
  const largest = treemap?.groups.find((g) => g.kind === "classified");

  const treemapTakeaway = treemap
    ? `${largest ? `${largest.label} is the largest ${DIMENSION_LABEL[dimension].toLowerCase()} bucket at ${weightPct(largest.weight)} of portfolio value. ` : ""}${
        unknown ? `${weightPct(unknown.unknown_fund_weight)} is unknown fund weight and is not spread over the other tiles.` : ""
      }`
    : undefined;

  return (
    <>
      <Panel id="xray-summary" kicker="Denominators" title="What this view covers">
        <Facts
          items={[
            {
              label: "Total value",
              value: report.available ? `${compact(report.total_value)} ${report.base_currency ?? ""}` : "—",
              note: report.portfolio_snapshot_date ? `Portfolio snapshot ${date(report.portfolio_snapshot_date)}` : "No portfolio snapshot",
            },
            {
              label: "Funds looked through",
              value: cov ? `${cov.covered} of ${cov.total}` : "—",
              note: oldest ? `Oldest fund holdings ${date(oldest)}` : "No fund holdings stored",
            },
            {
              label: "Unknown fund weight",
              value: unknown ? weightPct(unknown.unknown_fund_weight) : "—",
              note: unknown
                ? `${weightPct(unknown.not_looked_through_weight)} funds not looked through, ${weightPct(unknown.residual_weight)} not accounted for by published rows. Never redistributed.`
                : "Not computed",
            },
            {
              label: "Fee drag",
              value: report.fees ? `${pct(report.fees.annual_drag, 3)} / yr` : "—",
              note: report.fees
                ? `${report.fees.funds_with_ratio} funds with a known ratio; ${weightPct(report.fees.unpriced_weight)} of the portfolio is in funds with an unknown ratio (not assumed free).`
                : "No fund expense ratios known",
            },
          ]}
        />
        {report.notes.length > 0 ? (
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-text-muted">
            {report.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        ) : null}
      </Panel>

      <ChartFrame
        id="xray-treemap"
        kicker="X-Ray"
        title={`Issuer exposure by ${DIMENSION_LABEL[dimension].toLowerCase()}`}
        takeaway={treemapTakeaway}
        units={`% of total portfolio value${cov ? `, ${cov.covered} of ${cov.total} funds looked through` : ""}. ${treemap?.shownIssuers ?? 0} named issuers. Report as of ${date(report.as_of)}.`}
        provenance={provenance}
        hasValue={treemap !== null}
        controls={
          <Segmented
            label="Group by"
            options={DIMENSIONS.map((d) => ({ id: d, label: DIMENSION_LABEL[d] }))}
            value={dimension}
            onChange={setDimension}
          />
        }
        table={
          treemap
            ? {
                caption: `Exposure by ${dimension}`,
                columns: ["Group", "Share of portfolio value", "Held directly", "Held via funds", "Named issuers"],
                rows: treemap.groups.map((g) => [
                  g.label,
                  weightPct(g.weight),
                  weightPct(g.directWeight),
                  weightPct(g.fundWeight),
                  String(g.children.filter((c) => c.kind === "issuer").length),
                ]),
              }
            : undefined
        }
      >
        {treemap ? (
          <>
            <NestedTreemap map={treemap} label={`Nested treemap of exposure by ${dimension}, then issuer`} />
            {treemapNotes(treemap).map((n, i) => (
              <p key={i} role="status" className="text-xs text-text">
                <span aria-hidden="true" className="text-warning-signal">▲ </span>
                {n}
              </p>
            ))}
            <Legend
              items={[
                { label: "Classified group, issuers nested inside", swatch: "square", className: "bg-categorical-3" },
                { label: "Cash / fund cash and derivatives", swatch: "square", className: "bg-chart-benchmark" },
                { label: "Unclassified, unknown or not looked through", swatch: "dashed", className: "border-warning-signal" },
                { label: "Issuers outside the named list", swatch: "outline", className: "border-border" },
              ]}
            />
          </>
        ) : null}
      </ChartFrame>

      <ChartFrame
        id="xray-sankey"
        kicker="Look-through"
        title="Position to fund to underlying issuer"
        takeaway={
          sankey
            ? `${weightPct(sankey.drawnTotal)} of portfolio value is drawn: ${sankey.namedIssuers} named issuers, with cash, fund cash and derivatives, ${
                unknown ? `${weightPct(unknown.unknown_fund_weight)} unknown fund weight` : "unknown weight"
              } and other issuers kept as separate flows.`
            : undefined
        }
        units="Ribbon width = % of total portfolio value. Each fund's holdings are as of its own date, shown in its label."
        provenance={provenance}
        hasValue={sankey !== null}
        table={
          sankey
            ? {
                caption: "Look-through flows",
                columns: ["From", "To", "Share of portfolio value"],
                rows: sankey.links.map((l) => [
                  sankey.nodes.find((n) => n.id === l.source)?.label ?? l.source,
                  sankey.nodes.find((n) => n.id === l.target)?.label ?? l.target,
                  weightPct(l.value),
                ]),
              }
            : undefined
        }
      >
        {sankey ? (
          <>
            <LookThroughSankeyChart sankey={sankey} label="Look-through flows from portfolio positions through funds to underlying issuers" />
            <Legend items={SANKEY_LEGEND} />
            {sankey.notes.map((n, i) => (
              <p key={i} role="status" className="text-xs text-text">
                <span aria-hidden="true" className="text-warning-signal">▲ </span>
                {n}
              </p>
            ))}
            {!overlap ? (
              <p className="text-xs text-text-muted">
                {overlapLoading
                  ? "Fund overlap data is still loading; until it arrives each fund's cash and derivatives are not split from its other issuers."
                  : "Fund overlap data for this date did not load, so each fund's cash and derivatives are not split from its other issuers."}
              </p>
            ) : null}
          </>
        ) : null}
      </ChartFrame>

      <FundsAsOf report={report} />
      <IssuerTables report={report} />
    </>
  );
}

function FundsAsOf({ report }: { report: AggregateReport }) {
  return (
    <Panel
      id="xray-fund-asof"
      kicker="As-of dates and staleness"
      title="Fund holdings dates"
      description="Fund holdings are as of their own dates, not the portfolio date. A stale fund stays visible as stale; weight its published rows do not account for is shown as unknown."
    >
      {report.funds.length === 0 ? (
        <p className="text-sm text-text-muted">No funds are held, so there is nothing to look through.</p>
      ) : (
        <DataTable
          caption="Fund holdings as-of dates, unknown weight and fees"
          minWidth="62rem"
          columns={[
            { label: "Fund" },
            { label: "Weight", align: "right" },
            { label: "State" },
            { label: "Holdings as of", align: "right" },
            { label: "Age", align: "right" },
            { label: "Rows account for", align: "right" },
            { label: "Unknown weight", align: "right" },
            { label: "Expense ratio", align: "right" },
            { label: "Fee drag / yr", align: "right" },
          ]}
          rows={report.funds.map((f) => [
            <span key="n" className="block">
              <span className="font-medium text-text">{f.label}</span>
              <span className="block font-mono text-[11px] text-text-muted">{f.isin}</span>
              {f.warnings.map((w, i) => (
                <span key={i} className="block text-[11px] text-text font-medium">
                  {w}
                </span>
              ))}
            </span>,
            weightPct(f.weight),
            <span key="s" className="inline-flex flex-col gap-0.5">
              <DataStateBadge state={fundDataState(f)} reason={fundStatusLabel(f)} />
              <span className="text-[11px] text-text-muted">{fundStatusLabel(f)}</span>
            </span>,
            date(f.holdings_as_of),
            f.lag_days === null ? "—" : `${f.lag_days} d${f.stale ? " (stale)" : ""}`,
            f.rows_total_weight === null ? "—" : pct(f.rows_total_weight),
            weightPct(f.unknown_weight),
            f.expense_ratio === null ? "unknown" : pct(f.expense_ratio, 2),
            f.fee_drag === null ? "unknown" : pct(f.fee_drag, 3),
          ])}
        />
      )}
    </Panel>
  );
}

function IssuerTables({ report }: { report: AggregateReport }) {
  const top = report.issuers.slice(0, 15);
  return (
    <>
      <Panel
        id="xray-issuers"
        kicker="Merged by canonical issuer"
        title="Largest issuer exposures"
        description="A duplicate ADR and ordinary share are merged into one issuer but stay traceable to both securities. Weights are shares of total portfolio value, direct plus through funds."
      >
        {top.length === 0 ? (
          <p className="text-sm text-text-muted">No issuer exposure is available.</p>
        ) : (
          <DataTable
            caption="Largest issuer exposures"
            minWidth="48rem"
            columns={[
              { label: "Issuer" },
              { label: "Total", align: "right" },
              { label: "Direct", align: "right" },
              { label: "Via funds", align: "right" },
              { label: "Securities merged" },
            ]}
            rows={top.map((i) => [
              <span key="l" className="font-medium text-text">
                {i.label}
              </span>,
              weightPct(i.weight),
              weightPct(i.direct_weight),
              weightPct(i.fund_weight),
              <ul key="s" className="space-y-0.5 text-[11px] text-text-muted">
                {i.securities.map((s, k) => (
                  <li key={k}>
                    {s.name}
                    {s.isin ? ` · ${s.isin}` : " · no ISIN"} · {weightPct(s.weight)}
                  </li>
                ))}
              </ul>,
            ])}
          />
        )}
      </Panel>
      <Panel
        id="xray-overlap-issuers"
        kicker="Overlap"
        title="Issuers reached through more than one channel"
        description="Overlap weight is the part of an issuer's exposure supplied by more than one channel (total minus the largest single channel). It is concentration you may not intend, not a separate holding."
      >
        {report.overlap.length === 0 ? (
          <p className="text-sm text-text-muted">No issuer is reached through two or more channels.</p>
        ) : (
          <DataTable
            caption="Issuers with overlapping exposure"
            minWidth="44rem"
            columns={[{ label: "Issuer" }, { label: "Total", align: "right" }, { label: "Overlap", align: "right" }, { label: "Channels" }]}
            rows={report.overlap.slice(0, 15).map((i) => [
              i.label,
              weightPct(i.weight),
              weightPct(i.overlap_weight),
              <span key="c" className="text-[11px] text-text-muted">
                {i.channels.map((c) => `${c.label} ${weightPct(c.weight)}`).join(" · ")}
              </span>,
            ])}
          />
        )}
      </Panel>
    </>
  );
}
