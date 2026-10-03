"use client";

import { ArrowRightIcon } from "lucide-react";
import Link from "next/link";

import { AlertMessage } from "@/components/alert-message";
import { ChartFrame } from "@/components/data-state";
import { StatePlaceholder } from "@/components/data-state/state-placeholder";
import { getBrowserApiClient } from "@/lib/api/client";
import type { DataProvenance } from "@/lib/data-state/provenance";
import type { DataState } from "@/lib/data-state/states";
import { formatDate } from "@/lib/format";
import {
  useApiResource,
  type ResourceState,
} from "@/lib/hooks/use-api-resource";
import { fetchPortfolioMacroCoverage } from "@/lib/macro/api";
import type { PortfolioMacroCoverage } from "@/lib/macro/types";
import {
  concentrationRows,
  dec,
  overlapSummary,
  PANEL_GAP_DOMAINS,
  panelState,
  pctText,
  returnCoverageRows,
  sensitivityRows,
  shareText,
  shareView,
  type PortfolioConcentration,
  type PortfolioDataContext,
} from "@/lib/overview-data/context";
import { formatCompactMoney } from "@/lib/portfolio/money";

const fetchContext = () =>
  getBrowserApiClient().GET("/portfolio/data-context", {
    params: { query: {} },
  });

const SOURCE =
  "Stored exposure mappings, country series, price-return series and fund holdings";

function provenance(
  ctx: PortfolioDataContext,
  state: DataState,
  reason: string | null,
  method: string,
): DataProvenance {
  return {
    source: SOURCE,
    asOf: ctx.snapshot_date,
    knownAt: ctx.known_as_of,
    method,
    state,
    reason,
    coverage: null,
  };
}

/**
 * Compact data-context panels for the portfolio overview: concentration by country and
 * product, macro sensitivities, adjusted-return coverage and fund overlap. All figures come
 * from GET /portfolio/data-context; only holdings with a confirmed issuer link and an active
 * mapping are rolled up, and the unmapped remainder is shown beside every percentage.
 */
export function PortfolioDataContextSection() {
  const context = useApiResource(fetchContext).state;
  const coverage = useApiResource(fetchPortfolioMacroCoverage).state;
  return <PortfolioDataContextView context={context} coverage={coverage} />;
}

type ContextState = ResourceState<PortfolioDataContext>;

export function PortfolioDataContextView({
  context,
  coverage,
}: {
  context: ContextState;
  coverage?: ResourceState<PortfolioMacroCoverage>;
}) {
  if (context.kind === "loading") {
    return (
      <p className="text-sm text-text-muted" role="status">
        Loading portfolio data context…
      </p>
    );
  }
  const ctx = context.kind === "ok" ? context.data : context.previous;
  if (!ctx && context.kind === "error") {
    return (
      <section aria-labelledby="data-context-heading" className="space-y-3">
        <h2
          id="data-context-heading"
          className="text-[22px] leading-tight font-semibold"
        >
          Macro and trade context
        </h2>
        <StatePlaceholder
          state={context.status === 409 ? "partial" : "unavailable"}
          reason={`${context.title}${context.detail ? `: ${context.detail}` : ""}`}
        />
      </section>
    );
  }
  if (!ctx) return null;
  const macro =
    coverage?.kind === "ok"
      ? coverage.data
      : coverage?.kind === "error"
        ? coverage.previous
        : null;
  const currency = ctx.base_currency ?? "";
  const money = (v: number) =>
    currency ? formatCompactMoney(v, currency) : String(Math.round(v));
  const total = dec(ctx.total_value);

  return (
    <section aria-labelledby="data-context-heading" className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2
            id="data-context-heading"
            className="text-[22px] leading-tight font-semibold"
          >
            Macro and trade context
          </h2>
          <p className="max-w-prose text-sm text-text-muted">
            Where the portfolio is exposed by country and product, and how
            reliable that picture is. Holdings without a confirmed issuer link
            or an exposure mapping are left out and counted as unmapped, never
            spread over the rest.
          </p>
        </div>
        <nav
          aria-label="Related views"
          className="flex flex-wrap gap-x-4 gap-y-1 text-sm"
        >
          <Link
            href="/macro"
            className="inline-flex items-center gap-1 text-accent-ink hover:underline"
          >
            Country choropleth{" "}
            <ArrowRightIcon aria-hidden="true" className="size-3.5" />
          </Link>
          <Link
            href="/xray"
            className="inline-flex items-center gap-1 text-accent-ink hover:underline"
          >
            Fund x-ray{" "}
            <ArrowRightIcon aria-hidden="true" className="size-3.5" />
          </Link>
        </nav>
      </div>
      {context.kind === "error" ? (
        <AlertMessage title={`Could not refresh: ${context.title}`}>
          <p>Showing the last data loaded. {context.detail}</p>
        </AlertMessage>
      ) : null}
      <p className="num text-xs text-text-muted">
        Issuer identity: {shareText(ctx.identity_coverage, money)} · snapshot{" "}
        {ctx.snapshot_date ? formatDate(ctx.snapshot_date) : "none"}, known as
        of {formatDate(ctx.known_as_of.slice(0, 10))}
      </p>
      {ctx.gaps.length > 0 ? (
        <ul
          className="space-y-1 text-xs text-text-muted"
          aria-label="Coverage gaps"
        >
          {ctx.gaps.map((g) => (
            <li key={`${g.domain}-${g.reason}`}>
              <span className="font-medium text-text">
                {g.domain.replace(/_/g, " ")}
              </span>{" "}
              ({g.state.replace(/_/g, " ")}): {g.reason}
            </li>
          ))}
        </ul>
      ) : null}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <ConcentrationPanel
          ctx={ctx}
          money={money}
          macro={macro}
          total={total}
        />
        <SensitivityPanel ctx={ctx} />
      </div>
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <ReturnCoveragePanel ctx={ctx} money={money} />
        <OverlapPanel ctx={ctx} />
      </div>
    </section>
  );
}

function Bar({ ratio, label }: { ratio: number | null; label: string }) {
  const width = ratio === null ? 0 : Math.min(100, Math.max(0, ratio * 100));
  return (
    <span
      role="img"
      aria-label={label}
      className="block h-1.5 w-full min-w-12 bg-surface-muted"
    >
      <span className="block h-full bg-accent" style={{ width: `${width}%` }} />
    </span>
  );
}

function ConcentrationTable({
  conc,
  label,
  money,
}: {
  conc: PortfolioConcentration | null;
  label: string;
  money: (v: number) => string;
}) {
  if (!conc)
    return (
      <p className="text-xs text-text-muted">
        {label}: not computed (no snapshot).
      </p>
    );
  const rows = concentrationRows(conc, 5);
  const mapped = shareView(conc.mapped);
  const unmapped = dec(conc.unmapped_value);
  return (
    <div className="min-w-0 space-y-1.5">
      <h3 className="text-sm font-semibold">{label}</h3>
      <p className="num text-xs text-text-muted">
        Mapped: {shareText(conc.mapped, money)}
        {unmapped !== null ? (
          <>
            {" "}
            · unmapped {money(unmapped)}
            {conc.unmapped_tickers.length > 0
              ? ` (${conc.unmapped_tickers.length} holdings)`
              : ""}
          </>
        ) : null}
        {conc.hhi !== null && dec(conc.hhi) !== null ? (
          <> · HHI {dec(conc.hhi)!.toFixed(2)} within mapped value</>
        ) : null}
      </p>
      {rows.length === 0 ? (
        <p className="text-xs text-text-muted">
          No active {label.toLowerCase()} mapping
          {mapped?.ratio === 0 ? "" : " rows"}: nothing is rolled up.
        </p>
      ) : (
        <div className="relative overflow-x-auto">
          <table className="w-full min-w-[26rem] text-xs">
            <caption className="sr-only">
              {label}: top exposures, share of total portfolio value, reported
              versus inferred basis
            </caption>
            <thead>
              <tr className="border-b border-border text-[11px] tracking-[0.08em] text-text-muted uppercase">
                <th scope="col" className="py-1 text-left font-medium">
                  {label.includes("ountry") ? "Country" : "HS code"}
                </th>
                <th scope="col" className="py-1 text-right font-medium">
                  Of portfolio
                </th>
                <th scope="col" className="w-24 py-1 font-medium">
                  <span className="sr-only">Bar</span>
                </th>
                <th scope="col" className="py-1 text-right font-medium">
                  Reported
                </th>
                <th scope="col" className="py-1 text-right font-medium">
                  Inferred
                </th>
                <th scope="col" className="py-1 text-right font-medium">
                  Held
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={`${r.key}|${r.hsRevision ?? ""}`}
                  className="border-b border-border-soft last:border-0"
                >
                  <th scope="row" className="py-1 text-left font-normal">
                    {r.key || "unspecified"}
                    {r.hsRevision ? (
                      <span className="text-text-muted"> ({r.hsRevision})</span>
                    ) : null}
                  </th>
                  <td className="num py-1 text-right">
                    {pctText(r.ofPortfolio)}
                  </td>
                  <td className="py-1 pl-2">
                    <Bar
                      ratio={r.ofPortfolio}
                      label={`${r.key} ${pctText(r.ofPortfolio)} of total portfolio value`}
                    />
                  </td>
                  <td className="num py-1 text-right">
                    {r.reported !== null && r.reported > 0
                      ? money(r.reported)
                      : "none"}
                  </td>
                  <td className="num py-1 text-right">
                    {r.inferred !== null && r.inferred > 0
                      ? money(r.inferred)
                      : "none"}
                  </td>
                  <td className="num py-1 text-right">{r.holdings}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ConcentrationPanel({
  ctx,
  money,
  macro,
  total,
}: {
  ctx: PortfolioDataContext;
  money: (v: number) => string;
  macro: PortfolioMacroCoverage | null;
  total: number | null;
}) {
  const country = ctx.country_concentration;
  const top = country ? concentrationRows(country, 1)[0] : undefined;
  const hasValue = country !== null || ctx.product_concentration !== null;
  const { state, reason } = panelState(
    ctx,
    PANEL_GAP_DOMAINS.concentration,
    hasValue,
  );
  return (
    <ChartFrame
      id="overview-concentration"
      kicker="Exposure"
      title="Country and product concentration"
      takeaway={
        top && top.ofPortfolio !== null
          ? `${top.key || "The largest mapped country"} is the largest mapped country exposure at ${pctText(top.ofPortfolio)} of total portfolio value.`
          : "No country exposure is mapped yet."
      }
      units={`Share of total portfolio value${total !== null ? ` (${money(total)}, ${ctx.position_count} holdings)` : ""}; mapped holdings only`}
      provenance={provenance(
        ctx,
        state,
        reason,
        "Position value × active exposure-mapping weight; reported and inferred bases kept apart",
      )}
      hasValue={hasValue}
    >
      <div className="space-y-4">
        <ConcentrationTable conc={country} label="Country" money={money} />
        <ConcentrationTable
          conc={ctx.product_concentration}
          label="Product"
          money={money}
        />
        {macro ? (
          <p className="num text-xs text-text-muted">
            Macro coverage ({macro.baseCurrency}, {formatDate(macro.asOf)}):
            mapped{" "}
            {macro.mappedWeight !== null ? pctText(macro.mappedWeight) : "n/a"}
            {macro.mappedValue !== null && macro.totalValue ? (
              <>
                {" "}
                ({money(macro.mappedValue)} of {money(macro.totalValue)})
              </>
            ) : null}
            {macro.unknownValue !== null ? (
              <> · unknown {money(macro.unknownValue)}</>
            ) : null}
            .
          </p>
        ) : null}
      </div>
    </ChartFrame>
  );
}

function SensitivityPanel({ ctx }: { ctx: PortfolioDataContext }) {
  const rows = sensitivityRows(ctx.macro_sensitivities).slice(0, 6);
  const { state, reason } = panelState(
    ctx,
    PANEL_GAP_DOMAINS.sensitivities,
    rows.length > 0,
  );
  const change = (v: number | null, unit: string) =>
    v === null ? "no change" : `${v > 0 ? "+" : ""}${v.toFixed(2)} ${unit}`;
  return (
    <ChartFrame
      id="overview-sensitivities"
      kicker="Macro"
      title="Macro sensitivities"
      takeaway={
        rows.length > 0
          ? `${ctx.macro_sensitivities.length} stored series cover a mapped country; each is weighted by the exposure it covers.`
          : "No stored country series covers a mapped geography."
      }
      units="Exposure-weighted latest change per series, over covered exposure only; the coverage denominator is the exposed value of that basis"
      provenance={provenance(
        ctx,
        state,
        reason,
        "Latest versus previous observation per country, weighted by mapped exposed value; never extrapolated to uncovered countries",
      )}
      hasValue={rows.length > 0}
    >
      <div className="relative overflow-x-auto">
        <table className="w-full min-w-[30rem] text-xs">
          <caption className="sr-only">
            Macro sensitivities: weighted change and coverage, reported versus
            inferred exposure
          </caption>
          <thead>
            <tr className="border-b border-border text-[11px] tracking-[0.08em] text-text-muted uppercase">
              <th scope="col" className="py-1 text-left font-medium">
                Series
              </th>
              <th scope="col" className="py-1 text-right font-medium">
                Reported exposure
              </th>
              <th scope="col" className="py-1 text-right font-medium">
                Inferred exposure
              </th>
              <th scope="col" className="py-1 text-right font-medium">
                Latest period
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.id}
                className="border-b border-border-soft align-top last:border-0"
              >
                <th scope="row" className="py-1 text-left font-normal">
                  {r.seriesId}
                  <span className="block text-text-muted">
                    {r.source} · {r.unit}
                  </span>
                </th>
                {[r.reported, r.inferred].map((b, i) => (
                  <td key={i} className="num py-1 text-right">
                    {change(b.weightedChange, r.unit)}
                    <span className="block text-text-muted">
                      {b.coverage?.denominator
                        ? `${pctText(b.coverage.ratio)} of ${Math.round(b.coverage.denominator).toLocaleString("en")} exposed`
                        : "no exposure"}
                    </span>
                  </td>
                ))}
                <td className="num py-1 text-right">
                  {r.periodMax ? formatDate(r.periodMax) : "n/a"}
                  {r.periodMin && r.periodMin !== r.periodMax ? (
                    <span className="block text-text-muted">
                      from {formatDate(r.periodMin)}
                    </span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </ChartFrame>
  );
}

function ReturnCoveragePanel({
  ctx,
  money,
}: {
  ctx: PortfolioDataContext;
  money: (v: number) => string;
}) {
  const rows = returnCoverageRows(ctx.adjusted_return_coverage);
  const { state, reason } = panelState(
    ctx,
    PANEL_GAP_DOMAINS.returns,
    rows.length > 0,
  );
  const best = rows.find((r) => r.basis === "total_return");
  return (
    <ChartFrame
      id="overview-return-coverage"
      kicker="Adjusted returns"
      title="Adjusted-return coverage"
      takeaway={
        best
          ? `Total-return series are complete for ${pctText(best.complete?.ratio ?? null)} of portfolio value.`
          : "No return-series coverage is available."
      }
      units="Share of total portfolio value with a complete stored series, per adjustment basis"
      provenance={provenance(
        ctx,
        state,
        reason,
        ctx.adjusted_return_coverage?.note ??
          "Coverage of stored return series, not a return",
      )}
      hasValue={rows.length > 0}
    >
      <div className="relative overflow-x-auto">
        <table className="w-full min-w-[26rem] text-xs">
          <caption className="sr-only">
            Adjusted-return coverage per basis
          </caption>
          <thead>
            <tr className="border-b border-border text-[11px] tracking-[0.08em] text-text-muted uppercase">
              <th scope="col" className="py-1 text-left font-medium">
                Basis
              </th>
              <th scope="col" className="py-1 text-right font-medium">
                Complete
              </th>
              <th scope="col" className="py-1 text-right font-medium">
                Partial
              </th>
              <th scope="col" className="py-1 text-right font-medium">
                Missing
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.basis}
                className="border-b border-border-soft align-top last:border-0"
              >
                <th scope="row" className="py-1 text-left font-normal">
                  {r.label}
                </th>
                <td className="num py-1 text-right">
                  {pctText(r.complete?.ratio ?? null)}
                  <span className="block text-text-muted">
                    {r.completeCount} holdings
                    {r.complete?.denominator
                      ? ` of ${money(r.complete.denominator)}`
                      : ""}
                  </span>
                </td>
                <td className="num py-1 text-right">
                  {r.partialValue !== null ? money(r.partialValue) : "n/a"}
                  <span className="block text-text-muted">
                    {r.partialCount} holdings
                  </span>
                </td>
                <td className="num py-1 text-right">
                  {r.missingValue !== null ? money(r.missingValue) : "n/a"}
                  <span className="block text-text-muted">
                    {r.missingCount} holdings
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </ChartFrame>
  );
}

function OverlapPanel({ ctx }: { ctx: PortfolioDataContext }) {
  const unknown = ctx.fund_unknown
    ? dec(ctx.fund_unknown.unknown_fund_weight)
    : null;
  const s = overlapSummary(ctx.fund_overlap, unknown);
  const hasValue = s !== null && s.funds > 0;
  const { state, reason } = panelState(
    ctx,
    PANEL_GAP_DOMAINS.overlap,
    hasValue,
  );
  const fundTotal = ctx.fund_total_value;
  return (
    <ChartFrame
      id="overview-fund-overlap"
      kicker="Funds"
      title="Fund overlap"
      takeaway={
        s && s.pairs.length > 0
          ? `${s.pairs[0].a} and ${s.pairs[0].b} overlap by ${pctText(s.pairs[0].overlap)} of weight.`
          : s && s.funds === 1
            ? "One fund has holdings, so there is no pair to compare."
            : s && s.funds > 1
              ? `${s.funds} funds with holdings; no pair of them shares an issuer.`
              : "No fund holdings snapshot is available."
      }
      units={`Pairwise overlap as a share of weight${fundTotal ? `; fund value ${Math.round(fundTotal).toLocaleString("en")} ${ctx.base_currency ?? ""}` : ""}${s?.unknownWeight != null ? `; unknown fund weight ${pctText(s.unknownWeight)}` : ""}`}
      provenance={provenance(
        ctx,
        state,
        reason,
        ctx.fund_overlap?.notes.join(" ") ||
          "Look-through of the latest fund holdings known at the as-of date",
      )}
      hasValue={hasValue}
    >
      <div className="space-y-2">
        {s && s.pairs.length > 0 ? (
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[22rem] text-xs">
              <caption className="sr-only">Most overlapping fund pairs</caption>
              <thead>
                <tr className="border-b border-border text-[11px] tracking-[0.08em] text-text-muted uppercase">
                  <th scope="col" className="py-1 text-left font-medium">
                    Pair
                  </th>
                  <th scope="col" className="py-1 text-right font-medium">
                    Overlap
                  </th>
                  <th scope="col" className="py-1 text-right font-medium">
                    Shared issuers
                  </th>
                </tr>
              </thead>
              <tbody>
                {s.pairs.map((p) => (
                  <tr
                    key={`${p.isinA}|${p.isinB}`}
                    className="border-b border-border-soft last:border-0"
                  >
                    <th scope="row" className="py-1 text-left font-normal">
                      {p.a} / {p.b}
                      {!p.sameDate ? (
                        <span className="block text-text-muted">
                          holdings dates differ
                        </span>
                      ) : null}
                    </th>
                    <td className="num py-1 text-right">
                      {pctText(p.overlap)}
                    </td>
                    <td className="num py-1 text-right">{p.shared}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {s && s.staleFunds > 0 ? (
          <p className="text-xs text-text-muted">
            {s.staleFunds} of {s.funds} funds have stale holdings.
          </p>
        ) : null}
        <Link
          href="/xray"
          className="inline-flex items-center gap-1 text-xs text-accent-ink hover:underline"
        >
          Open the fund x-ray{" "}
          <ArrowRightIcon aria-hidden="true" className="size-3" />
        </Link>
      </div>
    </ChartFrame>
  );
}
