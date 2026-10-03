"use client";

import { useMemo, useState } from "react";

import { Choropleth, type ChoroplethDatum, type ChoroplethExtraRow } from "@/components/charts";
import { ChartFrame, Segmented } from "@/components/data-state";
import { formatNumber } from "@/lib/charts/format";
import { countryName } from "@/lib/charts/geo";
import { isFiniteNumber } from "@/lib/charts/scale";
import { describeCoverage } from "@/lib/data-state/provenance";
import { BASIS_LABELS, canonicalGeography, countryExposureView, rowShare, type BasisFilter } from "@/lib/macro/logic";
import type { PortfolioCountryContext, PortfolioMacroCoverage } from "@/lib/macro/types";

import { day, formatShare } from "./format";

const BASIS_OPTIONS = [
  { id: "all", label: "All bases" },
  { id: "reported", label: "Reported" },
  { id: "estimated", label: "Estimated" },
  { id: "user_assigned", label: "Owner-assigned" },
] as const satisfies readonly { id: BasisFilter; label: string }[];

const pct = formatShare;

/**
 * Country exposure of the whole portfolio as a choropleth overview with a ranked list beside it
 * and the exact figures in a table. Shares are of the total portfolio value, split by the
 * evidence basis of each mapping (reported, estimated, owner-assigned); the value no mapping
 * explains is its own explicit "Unknown" row, never spread over the countries.
 */
export function PortfolioExposureMap({ ctx, coverage }: { ctx: PortfolioCountryContext; coverage: PortfolioMacroCoverage | null }) {
  const [basis, setBasis] = useState<BasisFilter>("all");
  const view = useMemo(() => countryExposureView(ctx, basis), [ctx, basis]);
  const allView = useMemo(() => countryExposureView(ctx, "all"), [ctx]);
  const c = ctx.country;
  const total = ctx.totalValue;
  const currency = ctx.baseCurrency ?? "";

  const seriesByGeo = useMemo(() => {
    const m = new Map<string, { series: number; latest: string | null }>();
    for (const e of coverage?.byExposure ?? []) {
      if (e.exposureType !== "geography") continue;
      // DE, DEU and 276 are one country: keyed by the resolved code, like the exposure rows.
      const key = canonicalGeography(e.exposureKey);
      const prev = m.get(key);
      m.set(key, {
        series: Math.max(prev?.series ?? 0, e.availableSeries ?? 0),
        latest: [prev?.latest, e.latestPeriod].filter((x): x is string => !!x).sort().pop() ?? null,
      });
    }
    return m;
  }, [coverage]);

  const basisShare = (b: "reported" | "estimated" | "user_assigned"): number | null => {
    if (!c || !isFiniteNumber(total) || total <= 0) return null;
    return c.rows.reduce((s, r) => s + (r.byBasis[b] ?? 0), 0) / total;
  };
  const reported = basisShare("reported");
  const estimated = basisShare("estimated");
  const assigned = basisShare("user_assigned");

  const data: ChoroplethDatum[] = view.countries.map((d) => ({
    code: d.code,
    value: d.value,
    detail:
      isFiniteNumber(total) && total > 0
        ? `Reported ${pct((d.byBasis.reported ?? 0) / total)} · estimated ${pct((d.byBasis.estimated ?? 0) / total)} · owner-assigned ${pct((d.byBasis.user_assigned ?? 0) / total)}; ${d.holdingsAtLeast ? "at least " : ""}${d.holdings} holding${d.holdings === 1 && !d.holdingsAtLeast ? "" : "s"}`
        : undefined,
  }));
  const extraRows: ChoroplethExtraRow[] = [
    ...view.aggregates.map((a) => ({ key: `agg-${a.code}`, label: countryName(a.code), value: a.value, kind: "aggregate" as const, note: "A trade bloc, not one country: no polygon." })),
    ...view.unrecognised.map((r) => ({
      key: `unk-${r.key}`,
      label: `Unrecognised code ${r.key}`,
      value: rowShare(r, basis, total),
      kind: "aggregate" as const,
      note: "The geography code is not an ISO country this map knows.",
    })),
    ...(basis !== "all" && view.otherBasisShare !== null
      ? [{ key: "other-bases", label: "Mapped on other bases", value: view.otherBasisShare, kind: "aggregate" as const, note: `Value mapped to a country, but not on the ${BASIS_LABELS[basis].toLowerCase()} basis shown here.` }]
      : []),
    {
      key: "unknown",
      label: "Unknown (unmapped)",
      value: view.unmappedShare,
      kind: "unknown",
      note: c && c.unmappedTickers.length > 0 ? `${c.unmappedTickers.length} holding${c.unmappedTickers.length === 1 ? "" : "s"} with no active country mapping: ${c.unmappedTickers.slice(0, 8).join(", ")}${c.unmappedTickers.length > 8 ? "…" : ""}` : "Value no mapping explains.",
    },
  ];

  const top = allView.countries[0];
  const positions = ctx.positionCount;
  const unmappedCount = c?.unmappedTickers.length ?? 0;
  const mapped = c?.mapped.ratio ?? null;
  const hasValue = c !== null && c.rows.length > 0;

  const gaps = [...ctx.gaps.filter((g) => /countr|geograph|exposure|macro/i.test(`${g.domain} ${g.reason}`)).map((g) => `${g.domain}: ${g.reason}`), ...(coverage?.gaps ?? [])];

  return (
    <ChartFrame
      id="portfolio-country-exposure"
      kicker="Country exposure"
      title="Where the portfolio is exposed"
      takeaway={
        hasValue && top && mapped !== null
          ? `${pct(mapped)} of portfolio value is mapped to a country${reported !== null ? ` (${pct(reported)} reported, ${pct(estimated ?? 0)} estimated, ${pct(assigned ?? 0)} owner-assigned)` : ""}; ${pct(view.unmappedShare ?? 1 - mapped)} is unknown. The largest mapped country is ${countryName(top.code)} at ${pct(top.value ?? 0)} (all bases).${basis !== "all" && view.basisShare !== null ? ` Shown on the ${BASIS_LABELS[basis].toLowerCase()} basis only: ${pct(view.basisShare)} of portfolio value; ${pct(view.otherBasisShare ?? 0)} is mapped on other bases and left out of the countries.` : ""}`
          : "No portfolio value has an active country mapping yet, so no country is attributed."
      }
      units={`% of total portfolio value${isFiniteNumber(total) ? `, ${formatNumber(total, 0)} ${currency}` : ""} across ${positions} position${positions === 1 ? "" : "s"}; basis: ${basis === "all" ? "all mappings" : BASIS_LABELS[basis]}`}
      hasValue={hasValue}
      controls={<Segmented label="Evidence basis" options={BASIS_OPTIONS} value={basis} onChange={setBasis} />}
      provenance={{
        source: "Exposure mappings × portfolio position snapshot",
        asOf: ctx.snapshotDate,
        knownAt: ctx.knownAsOf,
        method: c?.note ?? "Mapped holdings only; no weight is distributed over unmapped value.",
        state: !c ? "not_covered" : (mapped ?? 0) <= 0 ? "not_covered" : (mapped ?? 0) < 1 || gaps.length > 0 ? "partial" : "verified",
        reason: !c ? "The API returned no country concentration: no holding has an active country mapping." : gaps.length > 0 ? gaps.join(" ") : null,
        coverage: positions > 0 ? { covered: Math.max(0, positions - unmappedCount), total: positions, unit: "positions with a country mapping" } : null,
      }}
      table={{
        caption: "Portfolio exposure by country and evidence basis",
        columns: ["Country", basis === "all" ? "Share of portfolio" : `Share of portfolio (${BASIS_LABELS[basis].toLowerCase()} only)`, basis === "all" ? `Exposed value (${currency})` : `Exposed value, ${BASIS_LABELS[basis].toLowerCase()} only (${currency})`, "Reported", "Estimated", "Owner-assigned", "Holdings (all bases)", "Series stored", "Latest period"],
        rows: [
          ...[...view.countries, ...view.aggregates].map((d) => {
            const s = seriesByGeo.get(d.code);
            return [
              countryName(d.code),
              d.value === null ? "n/a" : pct(d.value),
              isFiniteNumber(d.exposedValue) ? formatNumber(d.exposedValue, 0) : "n/a",
              formatNumber(d.byBasis.reported ?? 0, 0),
              formatNumber(d.byBasis.estimated ?? 0, 0),
              formatNumber(d.byBasis.user_assigned ?? 0, 0),
              `${d.holdingsAtLeast ? "at least " : ""}${d.holdings}`,
              s ? String(s.series) : "not reported",
              s?.latest ? day(s.latest) : "n/a",
            ];
          }),
          ...view.unrecognised.map((r) => [
            `Unrecognised code ${r.key}`,
            rowShare(r, basis, total) === null ? "n/a" : pct(rowShare(r, basis, total) as number),
            basis === "all" ? (isFiniteNumber(r.exposedValue) ? formatNumber(r.exposedValue, 0) : "n/a") : formatNumber(r.byBasis[basis] ?? 0, 0),
            formatNumber(r.byBasis.reported ?? 0, 0),
            formatNumber(r.byBasis.estimated ?? 0, 0),
            formatNumber(r.byBasis.user_assigned ?? 0, 0),
            String(r.holdings),
            "n/a",
            "n/a",
          ]),
          ...(basis !== "all" && view.otherBasisShare !== null
            ? [["Mapped on other bases", pct(view.otherBasisShare), isFiniteNumber(total) ? formatNumber(view.otherBasisShare * total, 0) : "n/a", "—", "—", "—", "—", "—", "—"]]
            : []),
          [
            "Unknown (unmapped)",
            view.unmappedShare === null ? "n/a" : pct(view.unmappedShare),
            isFiniteNumber(view.unmappedValue) ? formatNumber(view.unmappedValue, 0) : "n/a",
            "—",
            "—",
            "—",
            String(unmappedCount),
            "—",
            "—",
          ],
        ],
      }}
    >
      <Choropleth
        data={data}
        extraRows={extraRows}
        formatValue={pct}
        unitLabel={`% of portfolio value, ${basis === "all" ? "all mappings" : BASIS_LABELS[basis].toLowerCase()}`}
        denominator={
          isFiniteNumber(total)
            ? `Denominator: total portfolio value ${formatNumber(total, 0)} ${currency}. Mapped ${mapped === null ? "n/a" : pct(mapped)}${basis !== "all" && view.basisShare !== null ? ` (${pct(view.basisShare)} on the ${BASIS_LABELS[basis].toLowerCase()} basis shown, ${pct(view.otherBasisShare ?? 0)} on other bases)` : ""}, unknown ${view.unmappedShare === null ? "n/a" : pct(view.unmappedShare)}. ${describeCoverage({ covered: Math.max(0, positions - unmappedCount), total: positions, unit: "positions" })} mapped.`
            : "Denominator unavailable."
        }
        ariaLabel={`World map of portfolio exposure by country, ${basis === "all" ? "all evidence bases" : BASIS_LABELS[basis]}`}
      />
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-text-muted">
        <span className="num">
          Country concentration (Herfindahl over mapped value): {c && c.hhi !== null ? `${formatNumber(Math.round(c.hhi * 10_000), 0)} of 10,000` : "not available"}
          {c && c.hhiDenominator !== null ? ` · mapped value ${formatNumber(c.hhiDenominator, 0)} ${currency}` : ""}
        </span>
        <span>Reported, estimated and owner-assigned weights are never merged into one claim.</span>
      </div>
      {gaps.length > 0 ? (
        <ul className="m-0 list-disc pl-5 text-xs text-text-muted">
          {gaps.map((g, i) => (
            <li key={i}>{g}</li>
          ))}
        </ul>
      ) : null}
    </ChartFrame>
  );
}
