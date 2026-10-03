"use client";

import { useCallback, useState, type FormEvent } from "react";

import { HBar, Sankey } from "@/components/charts";
import { ChartFrame, ProvenanceFooter, Segmented, StatePlaceholder } from "@/components/data-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { formatCompact, formatNumber, formatPct } from "@/lib/charts/format";
import { isFiniteNumber } from "@/lib/charts/scale";
import { useApiResource, type ApiResult } from "@/lib/hooks/use-api-resource";
import { fetchCountryContext, fetchIndustryContext, fetchTradeContext } from "@/lib/macro/api";
import { equalPartners, hhiPoints, normaliseReporter, productSankey, tradeFlows, tradePartyName, tradeRows, TRADE_METRIC_LABELS } from "@/lib/macro/logic";
import type { ResearchContext, TradeContext, TradeRow } from "@/lib/macro/types";

import { day } from "./format";
import { ResourceBoundary } from "./resource-boundary";

const usd = (v: number) => `$${formatCompact(v)}`;
const HS_RE = /^[0-9]{2,6}$/;

export type TradeQuery = { reporter: string; hsCode: string };

/** Metric tiles of GET /macro/trade: growth, partner concentration and export share, each with its flow and note. */
export function TradeMetrics({ trade }: { trade: TradeContext }) {
  const entries = Object.entries(trade.metrics);
  return (
    <div className="flex flex-col gap-3">
      <p className="max-w-prose text-xs text-text-muted">
        Reporter-wide figures: the backend computes these from every product stored for {tradePartyName(trade.reporter)}, not from HS {trade.hsCode} alone, so they move when another product is collected. Only the latest-period totals above are for HS {trade.hsCode}.
      </p>
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
    </div>
  );
}

/** Product -> partner flow for one trade flow, from the reporter's largest rows in its latest period. */
export function PartnerSankey({ context, selectedHs }: { context: ResearchContext; selectedHs: string }) {
  const { rows, unparsed } = tradeRows(context);
  const flows = tradeFlows(rows);
  const [flow, setFlow] = useState<string | null>(null);
  const active = flow && flows.includes(flow) ? flow : (flows.find((f) => f === "export") ?? flows[0] ?? null);
  const s = active ? productSankey(rows, active) : null;
  const hasValue = !!s && s.links.length > 0;
  const reporterName = tradePartyName(context.subject);
  const inRows = (s?.nodes ?? []).some((n) => n.id.startsWith(`hs:${selectedHs}:`));
  const flowRows: TradeRow[] = active ? rows.filter((r) => r.flow === active) : [];

  return (
    <ChartFrame
      id="trade-partners"
      kicker="UN Comtrade"
      title={`${reporterName}: largest ${active ?? "trade"} rows by partner`}
      takeaway={
        hasValue && s
          ? `${s.drawn} reporter-to-partner row${s.drawn === 1 ? "" : "s"} among ${reporterName}'s ten largest in ${s.period ? day(s.period) : "the latest period"}${inRows ? "" : `; HS ${selectedHs} is not among them`}. Rows outside the top ten are not shown: they are unknown here, not zero.`
          : `No ${active ?? ""} partner rows are available for ${reporterName} in the latest period.`
      }
      units={`US dollars, ${s?.revisions.join(" / ") || "HS revision n/a"}; one link per product and partner, partners are not summed to a total`}
      hasValue={hasValue}
      controls={flows.length > 1 ? <Segmented label="Trade flow" options={flows.map((f) => ({ id: f, label: f === "export" ? "Exports" : f === "import" ? "Imports" : f }))} value={active ?? flows[0]} onChange={setFlow} /> : undefined}
      provenance={{
        source: "UN Comtrade via research context",
        sourceUrl: flowRows[0]?.claim.sourceUrl ?? null,
        asOf: s?.period ?? null,
        knownAt: flowRows[0]?.claim.knownAsOf ?? null,
        method: context.method,
        state: rows.length === 0 ? "not_covered" : unparsed.length > 0 ? "partial" : "verified",
        reason: rows.length === 0 ? (context.gaps.find((g) => g.domain === "trade")?.reason ?? "No trade rows were returned.") : "National trade rows do not identify any company's customers or suppliers.",
        coverage: rows.length ? { covered: flowRows.length, total: rows.length, unit: "rows of this flow among the returned rows" } : null,
      }}
      table={{
        caption: `${reporterName} ${active ?? "trade"} rows by product and partner`,
        columns: ["Product (revision)", "Partner", "Value (USD)", "Period"],
        rows: flowRows.map((r) => [`HS ${r.hsCode} (${r.hsRevision})`, tradePartyName(r.partner), r.valueUsd === null ? "n/a" : usd(r.valueUsd), day(r.period)]),
      }}
    >
      {s && s.links.length > 0 ? (
        <>
          <Sankey nodes={s.nodes} links={s.links} label={`${reporterName} ${active} by product and partner`} formatValue={usd} labelWidth={120} height={Math.max(180, 40 + s.nodes.filter((n) => n.column === 1).length * 30)} />
          {s.worldRows.length > 0 ? (
            <p className="text-xs text-text-muted">
              World-total rows ({s.worldRows.map((r) => `HS ${r.hsCode}: ${r.valueUsd === null ? "n/a" : usd(r.valueUsd)}`).join(", ")}) are totals, not a partner, and are left out of the flow.
            </p>
          ) : null}
        </>
      ) : null}
      {unparsed.length > 0 ? <p className="text-xs text-text-muted">{unparsed.length} trade claim{unparsed.length === 1 ? "" : "s"} could not be placed (unexpected id) and {unparsed.length === 1 ? "is" : "are"} left out.</p> : null}
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

/** The product's latest-period totals (HS-specific) and the reporter-wide figures, labelled apart. */
export function TradeSummary({ trade: t }: { trade: TradeContext }) {
  return (
    <section aria-label="Trade summary" className="flex flex-col gap-3">
      <p className="max-w-prose text-sm text-text">
        {tradePartyName(t.reporter)}, HS {t.hsCode}: latest period for this product {t.latestPeriod ? day(t.latestPeriod) : "n/a"}.
        {Object.keys(t.latestByFlow).length === 0 ? " No flow total exists for the latest period." : ""}
      </p>
      <p className="max-w-prose text-xs text-text-muted">
        Reporter-wide: {t.periods} period{t.periods === 1 ? "" : "s"} stored across all of this reporter&apos;s products; HS revision {t.hsRevision === "mixed" ? "mixed (several revisions are stored)" : t.hsRevision}.
      </p>
      <dl className="m-0 flex flex-wrap gap-3">
        {Object.entries(t.latestByFlow).map(([flow, v]) => (
          <div key={flow} className="flex min-w-36 flex-col border border-border-soft px-3 py-2">
            <dt className="text-xs text-text-muted">{flow === "export" ? "Exports" : flow === "import" ? "Imports" : flow}, latest period</dt>
            <dd className="num m-0 font-heading text-[22px] font-semibold">{usd(v)} <span className="text-xs font-normal text-text-muted">{t.currency}</span></dd>
          </div>
        ))}
      </dl>
      <TradeMetrics trade={t} />
      <ProvenanceFooter
        provenance={{
          source: "UN Comtrade (national trade statistics)",
          sourceUrl: t.sourceUrl,
          asOf: t.latestPeriod,
          knownAt: t.knownAt,
          method: `${t.hsRevision === "mixed" ? "several HS revisions stored for this reporter" : t.hsRevision}; latest revision of each observation; exports and imports never blended. Metrics are reporter-wide, the latest-period totals are for HS ${t.hsCode}`,
          state: t.latestPeriod ? "verified" : "not_covered",
          reason: t.notice,
        }}
      />
    </section>
  );
}

/**
 * Trade context for one reporter and HS product: the backend's latest value per flow and
 * its reporter-wide metrics, the reporter's largest product-to-partner rows as a Sankey, and
 * the largest rows for the product across reporters. The backend gives only the latest partner
 * concentration, not a trend, and no unmapped-partner share, so neither is drawn.
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
  const countryFetcher = useCallback((): Promise<ApiResult<ResearchContext>> => (reporterCode !== null ? fetchCountryContext(reporterCode) : Promise.resolve(NOT_REQUESTED_CTX)), [reporterCode]);
  const industryFetcher = useCallback((): Promise<ApiResult<ResearchContext>> => (HS_RE.test(query.hsCode) ? fetchIndustryContext(query.hsCode) : Promise.resolve(NOT_REQUESTED_CTX)), [query.hsCode]);
  const { state: trade } = useApiResource(tradeFetcher);
  const { state: country } = useApiResource(countryFetcher);
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
            {(t) => <TradeSummary trade={t} />}
          </ResourceBoundary>
          <ResourceBoundary state={country} what="reporter trade rows">
            {(c) => <PartnerSankey context={c} selectedHs={query.hsCode} />}
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
