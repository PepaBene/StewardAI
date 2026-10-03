"use client";

import { useCallback, useState, type FormEvent } from "react";

import { HBar, LineChart, Sankey } from "@/components/charts";
import { ChartFrame, ProvenanceFooter, Segmented, StatePlaceholder } from "@/components/data-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { formatCompact, formatNumber, formatPct } from "@/lib/charts/format";
import { isFiniteNumber } from "@/lib/charts/scale";
import { useApiResource, type ApiResult } from "@/lib/hooks/use-api-resource";
import { fetchIndustryContext, fetchTradeContext } from "@/lib/macro/api";
import { equalPartners, hhiPoints, hhiTrend, normaliseReporter, partnerLabel, partnerSankey, tradePartyName, tradeRows, TRADE_METRIC_LABELS, TRADE_STALE_AFTER_MONTHS, tradePeriodStale } from "@/lib/macro/logic";
import type { ResearchContext, TradeContext, TradeFlowPartners } from "@/lib/macro/types";

import { day } from "./format";
import { ResourceBoundary } from "./resource-boundary";

const usd = (v: number) => `$${formatCompact(v)}`;
const HS_RE = /^[0-9]{2,6}$/;

export type TradeQuery = { reporter: string; hsCode: string };

/** Metric tiles of GET /macro/trade (product-scoped) or of its reporter_wide block: growth, partner concentration, shares. */
export function TradeMetrics({ metrics }: { metrics: Readonly<Record<string, number | null>> }) {
  const entries = Object.entries(metrics);
  return (
    <dl className="m-0 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
      {entries.map(([key, value]) => {
        const meta = TRADE_METRIC_LABELS[key] ?? { label: key.replace(/_/g, " "), kind: "index" as const, note: "" };
        return (
          <div key={key} className="flex min-w-0 flex-col gap-1 border border-border-soft p-3">
            <dt className="text-xs text-text-muted">{meta.label}</dt>
            <dd className="m-0">
              {!isFiniteNumber(value) ? (
                <span className="num font-heading text-[22px] font-semibold text-text-muted">not available</span>
              ) : meta.kind === "growth" ? (
                <span className="num font-heading text-[22px] font-semibold">{formatPct(value, 1, true)}</span>
              ) : meta.kind === "share" ? (
                <span className="num font-heading text-[22px] font-semibold">{formatPct(value, 1)}</span>
              ) : meta.kind === "hhi" ? (
                <span className="flex flex-col gap-1">
                  <span className="num font-heading text-[22px] font-semibold">{formatNumber(hhiPoints(value) ?? 0, 0)} <span className="text-xs font-normal text-text-muted">of 10,000</span></span>
                  <HBar value={value} label={meta.label} formatValue={(v) => `${formatNumber(Math.round(v * 10_000), 0)} of 10,000`} />
                  <span className="text-[11px] text-text-muted">About {formatNumber(equalPartners(value) ?? 0, 1)} equal partners; 10,000 is a single partner.</span>
                </span>
              ) : (
                <span className="num font-heading text-[22px] font-semibold">{formatNumber(value, 3)}</span>
              )}
            </dd>
            {meta.note ? <p className="text-[11px] text-text-muted">{meta.note}</p> : null}
          </div>
        );
      })}
    </dl>
  );
}

const flowLabel = (f: string) => (f === "export" ? "Exports" : f === "import" ? "Imports" : f);

/** The partner-concentration trend of one flow on the 0..10,000 axis, with the partners-exceed-total flag. */
export function ConcentrationTrend({ flow }: { flow: TradeFlowPartners }) {
  const trend = hhiTrend(flow.history);
  const exceeding = trend.filter((t) => t.exceedsTotal);
  return (
    <div className="flex flex-col gap-2">
      <h4 className="text-sm font-semibold">{flowLabel(flow.flow)} partner concentration over time</h4>
      <LineChart
        data={trend.map((t) => ({ date: t.period, value: t.points }))}
        label={`${flowLabel(flow.flow)} concentration`}
        yTickCount={4}
        height={180}
        formatValue={(v) => `${formatNumber(v, 0)} of 10,000`}
        legend={false}
      />
      <p className="text-[11px] text-text-muted">
        Herfindahl index over the reported country partners, 10,000 = a single partner. Periods are spaced evenly; a period without an index is a gap, not zero.
      </p>
      {exceeding.length > 0 ? (
        <p role="note" className="border border-border px-3 py-1.5 text-xs text-text">
          Partners exceed the total in {exceeding.map((t) => day(t.period)).join(", ")}: the partner rows add up to more than the World row, so the index for {exceeding.length === 1 ? "that period" : "those periods"} rests on inconsistent source data.
        </p>
      ) : null}
    </div>
  );
}

/** Product -> partner flow for one trade flow from the latest-period partner rows, with the unmapped remainder as its own target. */
export function PartnerSankey({ trade }: { trade: TradeContext }) {
  const flows = trade.partners.map((f) => f.flow).sort();
  const [flow, setFlow] = useState<string | null>(null);
  const active = flow && flows.includes(flow) ? flow : (flows.find((f) => f === "export") ?? flows[0] ?? null);
  const data = active ? trade.partners.find((f) => f.flow === active) : undefined;
  const s = data ? partnerSankey(data, trade.hsCode) : null;
  const hasValue = !!s && s.links.length > 0;
  const reporterName = tradePartyName(trade.reporter);
  const sum = data?.summary;
  const stated = isFiniteNumber(sum?.unmappedValue);
  const flagged = !!sum?.partnersExceedTotal;

  return (
    <ChartFrame
      id="trade-partners"
      kicker="UN Comtrade"
      title={`${reporterName}: HS ${trade.hsCode} ${active ?? "trade"} by partner`}
      takeaway={
        hasValue && s && sum
          ? `${s.drawn} partner${s.drawn === 1 ? "" : "s"} in ${day(s.period)}. ${
              stated ? `${usd(sum.unmappedValue as number)}${isFiniteNumber(sum.unmappedShare) ? ` (${formatPct(sum.unmappedShare, 1)})` : ""} of the total is not attributed to any listed partner.` : "Unmapped remainder not stated: no World total was reported for this period, so it is unknown, not zero."
            }${flagged ? " Partner rows add up to more than the World total (source data inconsistent)." : ""}`
          : `No ${active ?? ""} partner rows are available for HS ${trade.hsCode} of ${reporterName}.`
      }
      units={`US dollars, ${trade.hsRevision}; partners from the latest period with a value; total is the ${sum?.totalBasis === "partner_sum" ? "sum of the partner rows (no World row)" : "World row"}`}
      hasValue={hasValue}
      controls={flows.length > 1 ? <Segmented label="Trade flow" options={flows.map((f) => ({ id: f, label: flowLabel(f) }))} value={active ?? flows[0]} onChange={setFlow} /> : undefined}
      provenance={{
        source: "UN Comtrade (national trade statistics)",
        sourceUrl: trade.sourceUrl,
        asOf: s?.period ?? null,
        knownAt: trade.knownAt,
        method: "Partner rows of one product and flow; World is a total, never a partner; the unmapped remainder is its own target and is never folded into a partner.",
        state: trade.partners.length === 0 ? "not_covered" : !stated || flagged || (s?.skipped ?? 0) > 0 ? "partial" : "verified",
        reason: trade.partners.length === 0 ? "The backend returned no partner rows for this product." : "National trade rows do not identify any company's customers or suppliers.",
      }}
      table={{
        caption: `${reporterName} HS ${trade.hsCode} ${active ?? "trade"} by partner`,
        columns: ["Partner", "Value (USD)", "Share of total"],
        rows: [
          ...(data?.partners ?? []).map((p) => [partnerLabel(p), isFiniteNumber(p.value) ? usd(p.value) : "n/a", isFiniteNumber(p.share) ? formatPct(p.share, 1) : "n/a"]),
          ["Unmapped remainder", stated ? usd(sum?.unmappedValue as number) : "not stated: no World total", isFiniteNumber(sum?.unmappedShare) ? formatPct(sum.unmappedShare, 1) : "not stated"],
        ],
      }}
    >
      {s && s.links.length > 0 ? (
        <>
          <Sankey nodes={s.nodes} links={s.links} label={`${reporterName} HS ${trade.hsCode} ${active} by partner`} formatValue={usd} labelWidth={120} height={Math.max(180, 40 + s.nodes.filter((n) => n.column === 1).length * 30)} />
          {!stated ? <p className="text-xs text-text">Unmapped remainder: not stated, because no World total was reported for this period. It is unknown, not zero.</p> : null}
        </>
      ) : null}
      {data && data.history.length > 0 ? <ConcentrationTrend flow={data} /> : null}
    </ChartFrame>
  );
}

/** The largest reporter-partner rows for the product across reporters (industry context), never summed. */
export function ProductRows({ context, hsCode }: { context: ResearchContext; hsCode: string }) {
  const { rows } = tradeRows(context);
  const sorted = [...rows].filter((r) => isFiniteNumber(r.valueUsd)).sort((a, b) => (b.valueUsd ?? 0) - (a.valueUsd ?? 0));
  const top = sorted.length ? (sorted[0].valueUsd ?? 0) : 0;
  return (
    <ChartFrame
      id="trade-product"
      kicker="Industry context"
      title={`Largest reporter-partner rows for HS ${hsCode}`}
      takeaway={sorted.length ? `${sorted.length} rows in the latest period; each is a separate reporter, partner and flow, so they are listed, not added up.` : "No trade rows for this product were known by the cutoff."}
      units="US dollars per reporter-partner-flow row"
      hasValue={sorted.length > 0}
      provenance={{
        source: "UN Comtrade via research context",
        sourceUrl: sorted[0]?.claim.sourceUrl ?? null,
        asOf: sorted[0]?.period ?? null,
        knownAt: sorted[0]?.claim.knownAsOf ?? null,
        method: context.method,
        state: sorted.length ? "partial" : "not_covered",
        reason: sorted.length ? "Only the largest rows are returned; the product's full trade matrix is not in this response." : (context.gaps[0]?.reason ?? null),
      }}
      table={{
        caption: `Largest reporter-partner rows for HS ${hsCode}`,
        columns: ["Reporter → partner", "Flow", "Revision", "Value (USD)", "Period"],
        rows: sorted.map((r) => [`${tradePartyName(r.reporter)} → ${tradePartyName(r.partner)}`, r.flow, r.hsRevision, usd(r.valueUsd ?? 0), day(r.period)]),
      }}
    >
      <ol className="m-0 flex list-none flex-col gap-1.5 p-0">
        {sorted.map((r) => (
          <li key={r.claim.claimId} className="grid grid-cols-[minmax(7rem,16rem)_minmax(0,1fr)_4.5rem] items-center gap-2 text-sm">
            <span className="truncate" title={`${tradePartyName(r.reporter)} → ${tradePartyName(r.partner)} (${r.flow})`}>
              {tradePartyName(r.reporter)} → {tradePartyName(r.partner)} <span className="text-xs text-text-muted">{r.flow}</span>
            </span>
            <span className="flex h-2 bg-surface-muted" aria-hidden="true">
              <span className="block h-full bg-accent" style={{ width: `${top > 0 ? ((r.valueUsd ?? 0) / top) * 100 : 0}%` }} />
            </span>
            <span className="num text-right">{usd(r.valueUsd ?? 0)}</span>
          </li>
        ))}
      </ol>
    </ChartFrame>
  );
}

/** The product's latest-period totals and product-scoped metrics, with the reporter-wide context collapsed apart. */
export function TradeSummary({ trade: t }: { trade: TradeContext }) {
  const productMetrics = Object.fromEntries(Object.entries(t.metrics).filter(([k]) => k !== "product_mix_change"));
  const rw = t.reporterWide;
  const stale = tradePeriodStale(t.latestPeriod, new Date());
  return (
    <section aria-label="Trade summary" className="flex flex-col gap-3">
      <p className="max-w-prose text-sm text-text">
        {tradePartyName(t.reporter)}, HS {t.hsCode}: latest period for this product {t.latestPeriod ? day(t.latestPeriod) : "n/a"}; {t.periods} period{t.periods === 1 ? "" : "s"} stored for it; HS revision {t.hsRevision === "mixed" ? "mixed (several revisions are stored)" : t.hsRevision}.
        {Object.keys(t.latestByFlow).length === 0 ? " No flow total exists for the latest period." : ""}
      </p>
      <dl className="m-0 flex flex-wrap gap-3">
        {Object.entries(t.latestByFlow).map(([flow, v]) => (
          <div key={flow} className="flex min-w-36 flex-col border border-border-soft px-3 py-2">
            <dt className="text-xs text-text-muted">{flowLabel(flow)}, latest period</dt>
            <dd className="num m-0 font-heading text-[22px] font-semibold">{usd(v)} <span className="text-xs font-normal text-text-muted">{t.currency}</span></dd>
          </div>
        ))}
      </dl>
      <TradeMetrics metrics={productMetrics} />
      {rw ? (
        <details className="border border-border-soft text-sm">
          <summary className="cursor-pointer px-3 py-1.5 text-xs font-medium text-accent-ink select-none hover:underline">Reporter-wide context</summary>
          <div className="flex flex-col gap-3 border-t border-border-soft p-3">
            <p className="max-w-prose text-xs text-text-muted">
              All {rw.products} product{rw.products === 1 ? "" : "s"} stored for {tradePartyName(t.reporter)} together, {rw.periods} period{rw.periods === 1 ? "" : "s"}; HS revision {rw.hsRevision === "mixed" ? "mixed (several revisions are stored)" : rw.hsRevision}. These figures are not specific to HS {t.hsCode}.
            </p>
            <TradeMetrics metrics={rw.metrics} />
          </div>
        </details>
      ) : null}
      <ProvenanceFooter
        provenance={{
          source: "UN Comtrade (national trade statistics)",
          sourceUrl: t.sourceUrl,
          asOf: t.latestPeriod,
          knownAt: t.knownAt,
          method: `${t.hsRevision === "mixed" ? "several HS revisions stored for this product" : t.hsRevision}; latest revision of each observation; exports and imports never blended. Figures are for HS ${t.hsCode}; reporter-wide context is shown apart`,
          state: !t.latestPeriod ? "not_covered" : stale ? "stale" : "verified",
          reason: stale ? `Latest stored period is ${t.latestPeriod}, more than ${TRADE_STALE_AFTER_MONTHS} months ago.${t.notice ? " " + t.notice : ""}` : t.notice,
        }}
      />
    </section>
  );
}

/**
 * Trade context for one reporter and HS product, all product-scoped: the latest value per flow
 * and metrics, the product -> partner Sankey with the unmapped remainder as its own target, the
 * partner-concentration trend, and the largest rows for the product across reporters. The
 * reporter-wide figures sit in a collapsed block.
 */
/** Remount (key) with new `initial` values to reset the form, e.g. when the holding changes. */
export function TradePanel({ initial }: { initial: TradeQuery }) {
  const [query, setQuery] = useState<TradeQuery>(initial);
  const [reporter, setReporter] = useState(initial.reporter);
  const [hs, setHs] = useState(initial.hsCode);
  const [error, setError] = useState<string | null>(null);
  // A late-arriving default (the holding's mapped country and product load after the panel
  // mounts) fills the form until the owner has typed something of their own.
  const [touched, setTouched] = useState(false);
  const [seen, setSeen] = useState(initial);
  if (!touched && (seen.reporter !== initial.reporter || seen.hsCode !== initial.hsCode)) {
    setSeen(initial);
    setQuery(initial);
    setReporter(initial.reporter);
    setHs(initial.hsCode);
  }

  // The backend matches the reporter exactly (alpha-2 or the stored M49), so every spelling is
  // normalised to the alpha-2 code first; the country call below uses the same code.
  const reporterCode = normaliseReporter(query.reporter).code;
  const valid = reporterCode !== null && HS_RE.test(query.hsCode);
  const tradeFetcher = useCallback((): Promise<ApiResult<TradeContext>> => (reporterCode !== null && HS_RE.test(query.hsCode) ? fetchTradeContext(reporterCode, query.hsCode) : Promise.resolve(NOT_REQUESTED)), [reporterCode, query.hsCode]);
  const industryFetcher = useCallback((): Promise<ApiResult<ResearchContext>> => (HS_RE.test(query.hsCode) ? fetchIndustryContext(query.hsCode) : Promise.resolve(NOT_REQUESTED_CTX)), [query.hsCode]);
  const { state: trade } = useApiResource(tradeFetcher);
  const { state: industry } = useApiResource(industryFetcher);

  function submit(e: FormEvent) {
    e.preventDefault();
    setTouched(true);
    const r = normaliseReporter(reporter);
    if (r.error !== null) return setError(r.error);
    if (!HS_RE.test(hs.trim())) return setError("HS code: 2 to 6 digits, e.g. 8486.");
    setError(null);
    setReporter(r.code);
    setQuery({ reporter: r.code, hsCode: hs.trim() });
  }

  return (
    <Blueprint as="section" aria-labelledby="trade-heading" className="flex flex-col gap-4 px-5 py-5">
      <PanelHeading kicker="Trade" title={<span id="trade-heading">Trade for a reporter and product</span>} />
      <form onSubmit={submit} className="flex flex-wrap items-end gap-3" noValidate>
        <div className="flex min-w-0 flex-col gap-1">
          <Label htmlFor="trade-reporter">Reporter (ISO code or M49)</Label>
          <Input id="trade-reporter" value={reporter} onChange={(e) => { setTouched(true); setReporter(e.target.value); }} className="w-36" inputMode="text" autoComplete="off" aria-invalid={error?.startsWith("Reporter") || undefined} />
        </div>
        <div className="flex min-w-0 flex-col gap-1">
          <Label htmlFor="trade-hs">HS code</Label>
          <Input id="trade-hs" value={hs} onChange={(e) => { setTouched(true); setHs(e.target.value); }} className="w-32" inputMode="numeric" autoComplete="off" aria-invalid={error?.startsWith("HS") || undefined} />
        </div>
        <Button type="submit" size="sm">Show trade</Button>
        {error ? <p role="alert" className="text-sm text-text">{error}</p> : null}
      </form>

      {!valid ? (
        <StatePlaceholder state="not_covered" reason="Map a country and an HS product for this holding, or enter a reporter and HS code above, to read trade data." />
      ) : (
        <>
          <ResourceBoundary state={trade} what="trade context">
            {(t) => (
              <>
                <TradeSummary trade={t} />
                <PartnerSankey key={`${t.reporter}|${t.hsCode}`} trade={t} />
              </>
            )}
          </ResourceBoundary>
          <ResourceBoundary state={industry} what="product trade rows">
            {(c) => <ProductRows context={c} hsCode={query.hsCode} />}
          </ResourceBoundary>
        </>
      )}
    </Blueprint>
  );
}

const NOT_REQUESTED = { error: { detail: "No request made." }, response: new Response(null, { status: 404 }) };
const NOT_REQUESTED_CTX = NOT_REQUESTED;
