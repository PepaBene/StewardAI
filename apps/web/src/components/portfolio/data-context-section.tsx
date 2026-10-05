"use client";

import { TriangleAlertIcon } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { HBar } from "@/components/charts";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatCompact } from "@/lib/charts/format";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  concentrationView,
  fundRows,
  num,
  pct,
  returnCoverageRows,
  sensitivityRows,
  signed,
  unknownFund,
  type ConcentrationView,
  type PortfolioDataContext,
  type Ratio,
  type SensitivityLeg,
} from "@/lib/portfolio/data-context";
import { cn } from "@/lib/utils";

const fetchContext = () => getBrowserApiClient().GET("/portfolio/data-context", { params: { query: {} } });

const BASIS_WORD: Record<string, string> = { reported: "reported", estimated: "estimated", user_assigned: "assigned by you" };

/**
 * Overview section built on /portfolio/data-context: where the portfolio's exposure sits by
 * country and product, how mapped macro series move it, how complete the return history is, and
 * how the funds overlap. Each percentage names its denominator; unmapped, unknown and
 * not-looked-through value is shown as its own line and never spread over the known positions.
 */
export function PortfolioDataContextSection() {
  const { state } = useApiResource(fetchContext);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;

  if (state.kind === "loading" && !data) return <p className="text-sm text-text-muted">Loading exposure context…</p>;
  if (!data) {
    return (
      <p className="text-sm text-text-muted">
        Exposure context unavailable{state.kind === "error" ? `: ${state.title}` : ""}.
      </p>
    );
  }
  const ccy = data.base_currency ?? "";
  return (
    <section aria-label="Exposure context" className="space-y-5">
      <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-2">
        <ConcentrationPanel data={data} currency={ccy} />
        <SensitivityPanel data={data} />
      </div>
      <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-2">
        <FundOverlapPanel data={data} currency={ccy} />
        <ReturnCoveragePanel data={data} currency={ccy} />
      </div>
      {data.gaps.length > 0 ? (
        <details className="text-sm">
          <summary className="cursor-pointer text-accent-ink hover:underline">
            {data.gaps.length} gap{data.gaps.length === 1 ? "" : "s"} in the exposure context
          </summary>
          <ul className="mt-2 space-y-1 text-xs text-text-muted">
            {data.gaps.map((g, i) => (
              <li key={`${g.domain}${i}`}>
                <span className="font-medium text-text">{g.domain}</span>, {g.state.replaceAll("_", " ")}: {g.reason}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

function Denominator({ r, currency }: { r: Ratio | null; currency: string }) {
  if (!r || r.ratio === null) return <>No base to measure against</>;
  return (
    <>
      {pct(r.ratio, 0)} of {r.denominator !== null ? `${currency} ${formatCompact(r.denominator)}` : r.basis}
    </>
  );
}

// --- Concentration ----------------------------------------------------------------------

function ConcentrationPanel({ data, currency }: { data: PortfolioDataContext; currency: string }) {
  const country = useMemo(() => concentrationView(data.country_concentration), [data.country_concentration]);
  const product = useMemo(() => concentrationView(data.product_concentration), [data.product_concentration]);
  const [dim, setDim] = useState<"country" | "product">("country");
  const view = dim === "country" ? country : product;

  return (
    <Blueprint aria-labelledby="conc-heading" className="flex min-w-0 flex-col gap-3 px-[22px] py-5">
      <PanelHeading
        kicker="Exposure by mapped revenue"
        title={<span id="conc-heading">Where the business earns</span>}
        action={
          <div role="group" aria-label="Dimension" className="inline-flex border border-border text-xs font-semibold">
            {(["country", "product"] as const).map((d, i) => (
              <button
                key={d}
                type="button"
                aria-pressed={dim === d}
                onClick={() => setDim(d)}
                className={cn("px-2.5 py-1.5", i > 0 && "border-l border-border", dim === d ? "bg-accent text-accent-foreground" : "text-text hover:bg-border-soft")}
              >
                {d === "country" ? "Country" : "Product"}
              </button>
            ))}
          </div>
        }
      />
      {!view ? (
        <p className="max-w-[68ch] text-sm text-text-muted">
          No {dim} exposure is mapped to any holding yet. Map each holding&apos;s revenue by {dim} in the exposure editor and it appears here.
        </p>
      ) : (
        <ConcentrationRows view={view} currency={currency} />
      )}
    </Blueprint>
  );
}

function ConcentrationRows({ view, currency }: { view: ConcentrationView; currency: string }) {
  const mappedRatio = view.mapped?.ratio ?? null;
  return (
    <>
      <p className="max-w-[68ch] text-sm text-text-muted">
        <Denominator r={view.mapped} currency={currency} /> is mapped. Shares below are of the whole portfolio; the rest is
        unmapped and is not guessed.
      </p>
      <ul className="space-y-2">
        {view.rows.slice(0, 10).map((r) => (
          <li key={r.key} className="grid grid-cols-[4.5rem_minmax(0,1fr)_5rem] items-center gap-x-3 text-sm">
            <span className="truncate font-mono text-xs" title={r.key}>
              {r.key}
            </span>
            <HBar value={r.ofPortfolio} label={`${r.key} share of portfolio`} formatValue={(v) => pct(v, 1)} />
            <span className="num text-right">{pct(r.ofPortfolio, 1)}</span>
            <span />
            <span className="col-span-2 text-xs text-text-muted">
              {r.holdings} holding{r.holdings === 1 ? "" : "s"}
              {r.byBasis.length > 0 ? `; ${r.byBasis.map((b) => `${BASIS_WORD[b.basis] ?? b.basis} ${formatCompact(b.value)}`).join(", ")}` : ""}
              {r.ofMapped !== null ? `; ${pct(r.ofMapped, 0)} of the mapped part` : ""}
            </span>
          </li>
        ))}
        <li className="grid grid-cols-[4.5rem_minmax(0,1fr)_5rem] items-center gap-x-3 border-t border-dashed border-border pt-2 text-sm">
          <span className="text-xs text-text-muted">Unmapped</span>
          <HBar value={mappedRatio === null ? null : 1 - mappedRatio} tone="muted" label="Unmapped share of portfolio" formatValue={(v) => pct(v, 1)} />
          <span className="num text-right text-text-muted">{pct(mappedRatio === null ? null : 1 - mappedRatio, 1)}</span>
          <span />
          <span className="col-span-2 text-xs text-text-muted">
            {view.unmappedTickers.length > 0
              ? `${view.unmappedTickers.length} holding${view.unmappedTickers.length === 1 ? "" : "s"} without a mapping: ${view.unmappedTickers.slice(0, 6).join(", ")}${view.unmappedTickers.length > 6 ? ", …" : ""}`
              : "Every holding has a mapping"}
          </span>
        </li>
      </ul>
      {view.rows.length > 10 ? <p className="text-xs text-text-muted">{view.rows.length - 10} smaller entries not shown.</p> : null}
      {view.hhi !== null ? (
        <p className="num text-xs text-text-muted">Concentration index {view.hhi.toFixed(2)} within the mapped part only. {view.note}</p>
      ) : null}
    </>
  );
}

// --- Macro sensitivities ----------------------------------------------------------------

function SensitivityPanel({ data }: { data: PortfolioDataContext }) {
  const rows = useMemo(() => sensitivityRows(data.macro_sensitivities), [data.macro_sensitivities]);
  return (
    <Blueprint aria-labelledby="sens-heading" className="flex min-w-0 flex-col gap-3 px-[22px] py-5">
      <PanelHeading
        kicker="Macro sensitivity"
        title={<span id="sens-heading">How mapped economies moved</span>}
        action={
          <Link href="/macro" className="text-xs text-accent-ink hover:underline">
            Macro series and revisions
          </Link>
        }
      />
      {rows.length === 0 ? (
        <p className="max-w-[68ch] text-sm text-text-muted">
          No macro series is linked to a mapped exposure, so no change is attributed to the portfolio. Series appear once a
          country exposure is mapped and the public data job has run.
        </p>
      ) : (
        <>
          <p className="max-w-[68ch] text-sm text-text-muted">
            Change in each series since its previous release, averaged over the exposure that has a figure. Reported means
            the company disclosed the split; inferred means you or an estimate assigned it. The two are never added.
          </p>
          <div className="relative overflow-x-auto border border-border">
            <table className="w-full min-w-[34rem] text-sm">
              <caption className="sr-only">Exposure-weighted change per macro series, reported and inferred shown separately</caption>
              <thead className="bg-surface-muted">
                <tr className="border-b border-border text-xs text-text-muted">
                  <th scope="col" className="px-3 py-2 text-left font-medium">Series</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">Reported exposure</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">Inferred exposure</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.key} className="border-b border-border align-top last:border-0">
                    <th scope="row" className="px-3 py-2 text-left font-normal">
                      <span className="font-medium">{r.seriesId}</span>
                      <span className="block text-xs text-text-muted">
                        {r.source}, {r.unit}
                        {r.latestMax ? `, to ${formatDate(r.latestMax)}` : ""}
                      </span>
                    </th>
                    <Leg leg={r.reported} currency={data.base_currency ?? ""} />
                    <Leg leg={r.inferred} currency={data.base_currency ?? ""} />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Blueprint>
  );
}

function Leg({ leg, currency }: { leg: SensitivityLeg; currency: string }) {
  return (
    <td className="px-3 py-2">
      <span className="num block">{signed(leg.weightedChange)}</span>
      <span className="num block text-xs text-text-muted">
        <Denominator r={leg.coverage} currency={currency} /> covered
        {leg.geographies.length > 0 ? `, ${leg.geographies.join(", ")}` : ""}
      </span>
    </td>
  );
}

// --- Return coverage --------------------------------------------------------------------

function ReturnCoveragePanel({ data, currency }: { data: PortfolioDataContext; currency: string }) {
  const rows = useMemo(() => returnCoverageRows(data.adjusted_return_coverage), [data.adjusted_return_coverage]);
  return (
    <Blueprint aria-labelledby="ret-heading" className="flex min-w-0 flex-col gap-3 px-[22px] py-5">
      <PanelHeading kicker="Return history" title={<span id="ret-heading">How complete the return data is</span>} />
      {rows.length === 0 ? (
        <p className="max-w-[68ch] text-sm text-text-muted">No return-coverage figures are available yet. They appear after prices and corporate actions are collected.</p>
      ) : (
        <>
          <ul className="space-y-3">
            {rows.map((r) => (
              <li key={r.basis} className="space-y-1">
                <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
                  <span className="font-medium">{r.basis.replaceAll("_", " ")}</span>
                  <span className="num text-xs text-text-muted">
                    <Denominator r={r.complete} currency={currency} /> complete
                  </span>
                </div>
                <HBar value={r.complete?.ratio ?? null} label={`${r.basis} return history complete`} formatValue={(v) => pct(v, 0)} />
                <p className="num text-xs text-text-muted">
                  Partial {r.partialValue === null ? "—" : `${currency} ${formatCompact(r.partialValue)}`}
                  {r.partialTickers.length > 0 ? ` (${r.partialTickers.slice(0, 4).join(", ")}${r.partialTickers.length > 4 ? ", …" : ""})` : ""}; missing{" "}
                  {r.missingValue === null ? "—" : `${currency} ${formatCompact(r.missingValue)}`}
                  {r.missingTickers.length > 0 ? ` (${r.missingTickers.slice(0, 4).join(", ")}${r.missingTickers.length > 4 ? ", …" : ""})` : ""}.
                </p>
              </li>
            ))}
          </ul>
          {data.adjusted_return_coverage?.note ? <p className="text-xs text-text-muted">{data.adjusted_return_coverage.note}</p> : null}
        </>
      )}
    </Blueprint>
  );
}

// --- Fund overlap -----------------------------------------------------------------------

function FundOverlapPanel({ data, currency }: { data: PortfolioDataContext; currency: string }) {
  const { funds, pairs, notes } = useMemo(() => fundRows(data.fund_overlap), [data.fund_overlap]);
  const unknown = useMemo(() => unknownFund(data.fund_unknown), [data.fund_unknown]);
  const fundValue = num(data.fund_total_value);
  return (
    <Blueprint aria-labelledby="fund-heading" className="flex min-w-0 flex-col gap-3 px-[22px] py-5">
      <PanelHeading kicker="Funds" title={<span id="fund-heading">Fund overlap and what is not known</span>} />
      {funds.length === 0 ? (
        <p className="max-w-[68ch] text-sm text-text-muted">
          No fund holdings are looked through, so there is no overlap to show. Funds appear here once their issuer holdings are
          collected.
        </p>
      ) : (
        <>
          {pairs.length > 0 ? (
            <ul className="space-y-2">
              {pairs.slice(0, 6).map((p) => (
                <li key={`${p.a}|${p.b}`} className="space-y-0.5">
                  <div className="flex items-baseline justify-between gap-2 text-sm">
                    <span>
                      {p.a} and {p.b}
                    </span>
                    <span className="num">{pct(p.overlap, 0)}</span>
                  </div>
                  <HBar value={p.overlap} label={`${p.a} and ${p.b} overlap`} formatValue={(v) => pct(v, 0)} size="sm" />
                  <p className="text-xs text-text-muted">
                    {p.shared} shared issuers{p.sameDate ? "" : "; the two snapshots are from different dates"}.
                  </p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-text-muted">Only one fund is looked through, so there is no pair to compare.</p>
          )}
          <div className="relative overflow-x-auto border border-border">
            <table className="w-full min-w-[30rem] text-sm">
              <caption className="sr-only">Fund holdings snapshots with their age and unknown weight</caption>
              <thead className="bg-surface-muted">
                <tr className="border-b border-border text-xs text-text-muted">
                  <th scope="col" className="px-3 py-2 text-left font-medium">Fund</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">Holdings as of</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Unknown weight</th>
                </tr>
              </thead>
              <tbody>
                {funds.map((f) => (
                  <tr key={f.isin} className="border-b border-border last:border-0">
                    <th scope="row" className="px-3 py-2 text-left font-normal">
                      {f.label}
                    </th>
                    <td className="num px-3 py-2">
                      {formatDate(f.asOf)}
                      {f.stale ? (
                        <span className="ml-2 inline-flex items-center gap-1 text-xs text-text-muted">
                          <TriangleAlertIcon aria-hidden="true" className="size-3.5 text-warning-signal" />
                          {f.lagDays} days old
                        </span>
                      ) : (
                        <span className="ml-2 text-xs text-text-muted">{f.lagDays} days old</span>
                      )}
                    </td>
                    <td className="num px-3 py-2 text-right">{pct(f.unknownWeight, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {unknown ? (
        <p className="num text-sm">
          Unknown fund weight is {pct(unknown.unknown, 1)} of the portfolio
          {fundValue !== null ? ` (${currency} ${formatCompact(unknown.value)} of ${currency} ${formatCompact(fundValue)} in funds)` : ""}
          : {pct(unknown.notLookedThrough, 1)} in funds that are not looked through and {pct(unknown.residual, 1)} left over in those that are. It
          is shown as its own line and never spread over the known positions.
        </p>
      ) : null}
      {notes.length > 0 ? (
        <ul className="space-y-0.5 text-xs text-text-muted">
          {notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : null}
    </Blueprint>
  );
}
