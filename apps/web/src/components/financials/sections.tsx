"use client";

import { useMemo, useState } from "react";

import { Segmented, StatePlaceholder } from "@/components/data-state";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { ValuationPanel } from "@/components/reference/valuation-panel";
import { useHoldingSegments } from "@/components/reference/segments-panel";
import {
  useActualVsConsensus,
  useForwardData,
  useIrReleases,
  useIrSources,
  usePeerMetrics,
  usePeerSet,
  useSegmentProfitFacts,
  useStatementFacts,
} from "@/lib/financials/hooks";
import { ACTUAL_LINE_ITEM, actualsFor, cashWalkFromFacts, debtFromFacts, forwardMetrics } from "@/lib/financials/logic";
import type { FactRow, ForwardPayload } from "@/lib/financials/types";

import { CashWaterfall } from "./cash-waterfall";
import { DebtMaturityChart } from "./debt-maturity-chart";
import { EarningsCard } from "./earnings-card";
import { EstimatesFan } from "./estimates-fan";
import { IrPanel } from "./ir-panel";
import { PeerSetPanel } from "./peer-set-panel";
import { PeerTable } from "./peer-table";
import { ProbePanel } from "./probe-panel";
import { ResourceBoundary } from "./resource";
import { SegmentTreemapPanel } from "./segment-treemap-panel";
import { StatementExplorer } from "./statement-explorer";

type Common = { ticker: string; name: string; asOf: string | null; reconciledOnly: boolean };

/** Statements, cash walk, debt ladder and the estimates fan share one facts request. */
export function FundamentalsSections({ ticker, name, asOf, reconciledOnly }: Common) {
  const facts = useStatementFacts(ticker, asOf, reconciledOnly);
  const forward = useForwardData(ticker, asOf);
  return (
    <>
      <ResourceBoundary state={facts.state} label="Filed statements">
        {(f) => (
          <>
            <StatementExplorer name={name} facts={f} asOf={asOf} />
            <div className="grid min-w-0 grid-cols-1 items-start gap-5 xl:grid-cols-2">
              <CashWaterfall result={cashWalkFromFacts(f.rows)} />
              <DebtMaturityChart schedule={debtFromFacts(f.rows)} />
            </div>
          </>
        )}
      </ResourceBoundary>
      <SegmentsSection ticker={ticker} asOf={asOf} />
      <ResourceBoundary state={forward.state} label="Forward data">
        {(fw) => <ForwardSections ticker={ticker} asOf={asOf} forward={fw} facts={facts.state.kind === "ok" ? facts.state.data.rows : facts.state.kind === "error" && facts.state.previous ? facts.state.previous.rows : []} />}
      </ResourceBoundary>
    </>
  );
}

function SegmentsSection({ ticker, asOf }: { ticker: string; asOf: string | null }) {
  const [basis, setBasis] = useState<"annual" | "quarter" | null>(null);
  const segments = useHoldingSegments(ticker, basis);
  const profit = useSegmentProfitFacts(ticker, asOf);
  const profitRows: readonly FactRow[] | null = profit.kind === "ok" ? profit.data.rows : profit.kind === "error" && profit.previous ? profit.previous.rows : null;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <ResourceBoundary state={segments.state} label="Segment revenue">
        {(data) => (
          <SegmentTreemapPanel
            data={data}
            profitFacts={profitRows}
            basisControl={
              data.available_bases.length > 1 && data.basis ? (
                <Segmented
                  label="Period"
                  value={data.basis}
                  onChange={setBasis}
                  options={data.available_bases.map((b) => ({ id: b, label: b === "annual" ? "Annual" : "Quarter" }))}
                />
              ) : null
            }
          />
        )}
      </ResourceBoundary>
      {asOf ? <p className="text-xs text-text-muted">Segment revenue and valuation show the latest stored filing: the API has no as-known-on cut-off for them, so the date above does not apply here.</p> : null}
    </div>
  );
}

const AVC_PERIOD = /^FY\d{4}(Q[1-4]|H[12]|M9)?$/;

function ForwardSections({ ticker, asOf, forward, facts }: { ticker: string; asOf: string | null; forward: ForwardPayload; facts: readonly FactRow[] }) {
  const releases = useIrReleases(ticker, asOf);
  const sources = useIrSources(ticker);
  return (
    <>
      <EstimatesFan payload={forward} facts={facts} asOf={asOf} />
      <ActualVsConsensusSection ticker={ticker} forward={forward} facts={facts} />
      <ResourceBoundary state={releases} label="IR releases">
        {(r) => (
          <ResourceBoundary state={sources} label="IR pages">
            {(s) => <IrPanel releases={r} sources={s} />}
          </ResourceBoundary>
        )}
      </ResourceBoundary>
    </>
  );
}

/** Actual versus the consensus known before the report, for a metric and a fiscal period. */
function ActualVsConsensusSection({ ticker, forward, facts }: { ticker: string; forward: ForwardPayload; facts: readonly FactRow[] }) {
  const metrics = useMemo(() => forwardMetrics(forward).filter((m) => m in ACTUAL_LINE_ITEM), [forward]);
  const [metricChoice, setMetric] = useState<string | null>(null);
  const [periodChoice, setPeriod] = useState<string | null>(null);
  const metric = metricChoice && metrics.includes(metricChoice) ? metricChoice : (metrics[0] ?? null);
  const periods = useMemo(() => (metric ? actualsFor(metric, facts).points.map((p) => p.period).filter((p) => AVC_PERIOD.test(p)).reverse() : []), [metric, facts]);
  const period = periodChoice && periods.includes(periodChoice) ? periodChoice : (periods[0] ?? null);
  const avc = useActualVsConsensus(ticker, metric, period);

  return (
    <Blueprint as="section" aria-labelledby="earnings-heading" className="flex min-w-0 flex-col gap-3 px-5 py-5">
      <PanelHeading
        kicker="Reported figure vs the expectation"
        title={<span id="earnings-heading">Earnings against the consensus known before the report</span>}
        action={
          metrics.length > 0 ? (
            <div className="flex flex-wrap items-center gap-2">
              <Segmented label="Metric" value={metric ?? metrics[0]} onChange={setMetric} options={metrics.map((m) => ({ id: m, label: m }))} />
              {periods.length > 0 ? (
                <label className="flex items-center gap-1 text-xs text-text-muted">
                  <span className="sr-only">Fiscal period</span>
                  <select value={period ?? ""} onChange={(e) => setPeriod(e.target.value)} className="border border-border bg-surface-raised px-2 py-1 text-xs text-text">
                    {periods.map((p) => (
                      <option key={p} value={p}>
                        {p}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
            </div>
          ) : null
        }
      />
      {metrics.length === 0 ? (
        <StatePlaceholder state="not_covered" reason="No stored forward figure belongs to a metric that has a reported counterpart (revenue, diluted EPS, net income, operating income, capex), so there is nothing to compare an actual to." minHeight={80} />
      ) : periods.length === 0 ? (
        <StatePlaceholder state="not_covered" reason={`No reported ${metric} figure with a fiscal period is stored.`} minHeight={80} />
      ) : (
        <ResourceBoundary state={avc} label="Actual vs consensus">{(a) => <EarningsCard avc={a} />}</ResourceBoundary>
      )}
    </Blueprint>
  );
}

/** Peer metrics (confirmed peers only) and the owner's peer-set validation. */
export function PeerSections({ ticker, asOf, reconciledOnly }: Common) {
  const metrics = usePeerMetrics(ticker, asOf, reconciledOnly);
  const peers = usePeerSet(ticker);
  return (
    <>
      <ResourceBoundary state={metrics.state} label="Peer metrics">
        {(m) => <PeerTable data={m} ticker={ticker} />}
      </ResourceBoundary>
      <ResourceBoundary state={peers.state} label="Peer set">
        {(p) => (
          <PeerSetPanel
            ticker={ticker}
            peers={p}
            onChanged={() => {
              peers.reload();
              metrics.reload();
            }}
          />
        )}
      </ResourceBoundary>
    </>
  );
}

export function ValuationSection({ ticker }: { ticker: string }) {
  return <ValuationPanel ticker={ticker} />;
}

export function ProbeSection({ ticker, onDone }: { ticker: string; onDone: () => void }) {
  return <ProbePanel ticker={ticker} onDone={onDone} />;
}
