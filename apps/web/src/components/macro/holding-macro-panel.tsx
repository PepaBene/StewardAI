"use client";

import { useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ChartFrame, DataStateBadge, Segmented, StatePlaceholder, TableView } from "@/components/data-state";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { formatPct } from "@/lib/charts/format";
import { resolveGeography } from "@/lib/charts/geo";
import { isFiniteNumber } from "@/lib/charts/scale";
import { comparisonGroups, geographyName, groupByGeography, mappedTargets, sharedTimeDomain, type VintageMode } from "@/lib/macro/logic";
import type { HoldingMacroContext } from "@/lib/macro/types";
import { cn } from "@/lib/utils";

import { ExposureTag } from "./exposure-tag";
import { day, formatChange, formatFigure } from "./format";
import { IndicatorCard } from "./indicator-card";

/** Same-geography test across spellings (DE, DEU, 276). */
function sameGeography(a: string, b: string): boolean {
  const ga = resolveGeography(a);
  const gb = resolveGeography(b);
  return ga.code !== null && ga.code === gb.code ? true : a.toUpperCase() === b.toUpperCase();
}

/**
 * What the macro store says about one holding as of a date: the exposure mapping that links the
 * holding to countries and products, the latest country indicators for those countries (with
 * previous period, 5/10-year range and release/known-at dates), the exposure-weighted change per
 * indicator, and a ranked cross-country view of the same indicators. A holding with no mapping
 * gets an explicit "exposure unknown" state, never a default country.
 */
export function HoldingMacroPanel({ ctx, ticker, pastAsOf = false }: { ctx: HoldingMacroContext; ticker: string; /** The owner chose a past "known as of" date. */ pastAsOf?: boolean }) {
  const [mode, setMode] = useState<VintageMode>("latest");
  const groups = groupByGeography(ctx.observations);
  const domain = sharedTimeDomain(ctx.observations.map((o) => o.history));
  const targets = mappedTargets(ctx.effects);
  const mappedGeos = targets.filter((t) => t.kind === "geography").map((t) => t.key);
  const comps = comparisonGroups(ctx.comparisons);
  const relevantCoverage = ctx.coverage.filter((c) => mappedGeos.some((g) => sameGeography(g, c.geography)));
  const unknownRemainder = ctx.effects.length > 0 ? Math.max(...ctx.effects.map((e) => e.unknownWeight ?? 0)) : null;

  return (
    <div className="flex flex-col gap-5">
      <Blueprint as="section" aria-labelledby="holding-exposure-heading" className="flex flex-col gap-3 px-5 py-5">
        <PanelHeading kicker={`${ticker} · as of ${day(ctx.asOf)}`} title={<span id="holding-exposure-heading">Mapped exposure of {ticker}</span>} />
        {ctx.exposureUnknown ? (
          <AlertMessage tone="info" title="Exposure unknown">
            No exposure mapping for {ticker} was known by {day(ctx.asOf)}, so no country or product is attributed to it and no indicator is shown as its context. {pastAsOf ? "A mapping added now is dated from today, so it cannot appear at this past date; switch back to today to see it." : "Add a mapping below to link it to a country."}
          </AlertMessage>
        ) : (
          <>
            <p className="max-w-prose text-sm text-text">
              {targets.length} mapped target{targets.length === 1 ? "" : "s"}
              {unknownRemainder !== null && unknownRemainder > 0 ? `; up to ${formatPct(unknownRemainder, 0)} of the issuer in a dimension is unmapped and stays unknown` : ""}. Country indicators give dated context for these countries; they do not establish a company-specific sensitivity.
            </p>
            <div className="relative overflow-x-auto">
              <table className="w-full min-w-[40rem] text-xs">
                <caption className="sr-only">Mapped exposure of {ticker}</caption>
                <thead className="bg-surface-muted">
                  <tr className="border-b border-border text-left text-[11px] tracking-[0.08em] text-text-muted uppercase">
                    <th scope="col" className="px-3 py-1.5 font-medium">Segment</th>
                    <th scope="col" className="px-3 py-1.5 font-medium">Target</th>
                    <th scope="col" className="px-3 py-1.5 font-medium">Basis</th>
                    <th scope="col" className="px-3 py-1.5 text-right font-medium">Weight</th>
                    <th scope="col" className="px-3 py-1.5 text-right font-medium">Unmapped in dimension</th>
                    <th scope="col" className="px-3 py-1.5 font-medium">What it means</th>
                  </tr>
                </thead>
                <tbody>
                  {ctx.effects.map((e, i) => (
                    <tr key={i} className="border-b border-border-soft align-top last:border-0">
                      <th scope="row" className="px-3 py-1.5 text-left font-normal">{e.segment}</th>
                      <td className="px-3 py-1.5">{e.geography ? `${geographyName(e.geography)} (${e.geography})` : e.hsCode ? `HS ${e.hsCode}` : "n/a"}</td>
                      <td className="px-3 py-1.5"><ExposureTag basis={e.basis} /></td>
                      <td className="num px-3 py-1.5 text-right">{isFiniteNumber(e.weight) ? formatPct(e.weight, 1) : "n/a"}</td>
                      <td className="num px-3 py-1.5 text-right">{isFiniteNumber(e.unknownWeight) ? formatPct(e.unknownWeight, 1) : "n/a"}</td>
                      <td className="max-w-[28rem] px-3 py-1.5 text-text-muted">{e.explanation}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Blueprint>

      {!ctx.exposureUnknown ? (
        <Blueprint as="section" aria-labelledby="holding-indicators-heading" className="flex flex-col gap-4 px-5 py-5">
          <PanelHeading kicker="Country indicators" title={<span id="holding-indicators-heading">Latest values for the mapped countries</span>} />
          <p className="max-w-prose text-sm text-text-muted">
            Each card is one series at the latest period known by {day(ctx.asOf)}, in its own unit, with the previous period and where the value sits in its stored 5- and 10-year range.
            The small charts share one time axis. Latest vintage shows each period as last revised; first release shows the value as first published, and a period whose first release was never archived is left blank (vintage unavailable), not filled with the revised value. Ringed points were revised. A period with no value is left empty, never zero.
          </p>
          <Segmented
            label="Vintage"
            options={[
              { id: "latest" as const, label: "Latest vintage" },
              { id: "first_release" as const, label: "First release" },
            ]}
            value={mode}
            onChange={setMode}
          />
          {groups.length === 0 ? (
            <StatePlaceholder state="not_covered" reason={`No stored country series for ${mappedGeos.length ? mappedGeos.join(", ") : "the mapped geographies"} was known by ${day(ctx.asOf)}.`} />
          ) : (
            groups.map((g) => (
              <section key={g.geography} aria-label={`${g.name} indicators`} className="flex flex-col gap-2">
                <h3 className="font-heading text-lg font-semibold">
                  {g.name} <span className="num text-xs font-normal text-text-muted">{g.points.length} series</span>
                </h3>
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {g.points.map((p, i) => (
                    <IndicatorCard key={`${p.seriesId}|${p.source}|${p.geography}|${i}`} point={p} mode={mode} domain={domain} />
                  ))}
                </div>
              </section>
            ))
          )}
          {mappedGeos.filter((g) => !groups.some((x) => sameGeography(g, x.geography))).length > 0 ? (
            <p className="text-xs text-text-muted">
              No series stored for: {mappedGeos.filter((g) => !groups.some((x) => sameGeography(g, x.geography))).map((g) => geographyName(g)).join(", ")}. These are not-covered, not zero.
            </p>
          ) : null}
        </Blueprint>
      ) : null}

      {ctx.seriesEffects.length > 0 ? (
        <ChartFrame
          id="series-effects"
          kicker="Exposure-weighted change"
          title="How the mapped countries moved"
          takeaway="Each row is the sum of mapped weight times the latest change of a country series, over the mapped countries that have both periods."
          units="Change in the series' own unit; covered share of the issuer in brackets"
          provenance={{
            source: "Exposure mappings × country series",
            asOf: ctx.asOf,
            method: "Weight × (latest minus previous period); the unmapped remainder is unknown, not unchanged.",
            state: ctx.seriesEffects.every((s) => (s.coveredWeight ?? 0) >= 1 - 1e-9) ? "verified" : "partial",
            reason: "Covered share below 100% means part of the issuer is not represented in the change.",
          }}
          table={{
            caption: "Exposure-weighted latest change per series",
            columns: ["Series", "Weighted change", "Covered share", "Unknown share", "Countries"],
            rows: ctx.seriesEffects.map((s) => [
              `${s.seriesId} (${s.source})`,
              `${formatChange(s.weightedChange)} ${s.unit}`,
              isFiniteNumber(s.coveredWeight) ? formatPct(s.coveredWeight, 0) : "n/a",
              isFiniteNumber(s.coveredWeight) ? formatPct(Math.max(0, 1 - s.coveredWeight), 0) : "n/a",
              s.geographies.join(", "),
            ]),
          }}
        >
          <ul className="m-0 grid list-none grid-cols-1 gap-2 p-0 md:grid-cols-2">
            {ctx.seriesEffects.map((s) => (
              <li key={`${s.seriesId}|${s.source}`} className="flex items-baseline justify-between gap-3 border border-border-soft px-3 py-2 text-sm">
                <span className="min-w-0 break-words">
                  {s.seriesId}
                  <span className="block text-xs text-text-muted">{s.geographies.join(", ")} · {s.source}</span>
                </span>
                <span className="num text-right">
                  {formatChange(s.weightedChange)} {s.unit}
                  <span className="block text-xs text-text-muted">covers {isFiniteNumber(s.coveredWeight) ? formatPct(s.coveredWeight, 0) : "n/a"}</span>
                </span>
              </li>
            ))}
          </ul>
        </ChartFrame>
      ) : null}

      {comps.length > 0 ? (
        <Blueprint as="section" aria-labelledby="holding-comparison-heading" className="flex flex-col gap-4 px-5 py-5">
          <PanelHeading kicker="Country comparison" title={<span id="holding-comparison-heading">The same indicators across countries</span>} />
          <p className="max-w-prose text-sm text-text-muted">
            The API returns each country&apos;s latest value, not two comparable dates, so countries are ranked by value (no slope). Periods can differ between countries and are shown beside every value. Mapped countries are marked.
          </p>
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            {comps.map((g) => {
              const max = g.rows.length ? Math.max(...g.rows.map((r) => Math.abs(r.value))) : 0;
              return (
                <section key={g.key} aria-label={`${g.seriesId} across countries`} className="flex min-w-0 flex-col gap-2 border border-border-soft p-3">
                  <h3 className="font-heading text-lg font-semibold break-words">{g.seriesId}</h3>
                  <p className="text-xs text-text-muted">
                    {g.source} · {g.unit} · {g.rows.length} countr{g.rows.length === 1 ? "y" : "ies"}
                    {g.missing.length ? ` · no value: ${g.missing.join(", ")}` : ""}
                  </p>
                  {g.mixedPeriods ? <p className="text-xs text-text">Latest periods differ ({day(g.periodMin)} to {day(g.periodMax)}): read the ranking with the period column.</p> : null}
                  <ol className="m-0 flex list-none flex-col gap-1 p-0">
                    {g.rows.map((r) => {
                      const mapped = mappedGeos.some((m) => sameGeography(m, r.geography));
                      return (
                        <li key={r.geography} className="grid grid-cols-[minmax(4.5rem,8rem)_minmax(0,1fr)_5rem_5.5rem] items-center gap-2 text-xs">
                          <span className={cn("truncate", mapped && "font-semibold")} title={geographyName(r.geography)}>
                            {geographyName(r.geography)}
                            {mapped ? <span className="sr-only"> (mapped)</span> : null}
                          </span>
                          <span className="flex h-2 bg-surface-muted" aria-hidden="true">
                            <span className={cn("block h-full", mapped ? "bg-accent" : "bg-chart-benchmark")} style={{ width: `${max > 0 ? (Math.abs(r.value) / max) * 100 : 0}%` }} />
                          </span>
                          <span className="num text-right">{formatFigure(r.value)}</span>
                          <span className="num text-right text-text-muted">{day(r.period)}</span>
                        </li>
                      );
                    })}
                  </ol>
                  <p className="flex flex-wrap items-center gap-3 text-[11px] text-text-muted">
                    <span className="inline-flex items-center gap-1"><span aria-hidden="true" className="inline-block size-2.5 bg-accent" /> Mapped to {ticker}</span>
                    <span className="inline-flex items-center gap-1"><span aria-hidden="true" className="inline-block size-2.5 bg-chart-benchmark" /> Other country</span>
                  </p>
                  <TableView
                    summary="Table view"
                    table={{
                      caption: `${g.seriesId} across countries`,
                      columns: ["Country", `Value (${g.unit})`, "Period", "Known since"],
                      rows: g.rows.map((r) => [geographyName(r.geography), formatFigure(r.value), day(r.period), day(r.knownAt)]),
                    }}
                  />
                </section>
              );
            })}
          </div>
        </Blueprint>
      ) : null}

      {relevantCoverage.length > 0 ? (
        <Blueprint as="section" aria-labelledby="holding-coverage-heading" className="flex flex-col gap-2 px-5 py-5">
          <PanelHeading kicker="Provider coverage" title={<span id="holding-coverage-heading">Collection state for the mapped countries</span>} />
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[32rem] text-xs">
              <caption className="sr-only">Provider coverage for the mapped countries</caption>
              <thead className="bg-surface-muted">
                <tr className="border-b border-border text-left text-[11px] tracking-[0.08em] text-text-muted uppercase">
                  <th scope="col" className="px-3 py-1.5 font-medium">Provider · dataset</th>
                  <th scope="col" className="px-3 py-1.5 font-medium">Country</th>
                  <th scope="col" className="px-3 py-1.5 font-medium">State</th>
                  <th scope="col" className="px-3 py-1.5 text-right font-medium">Checked</th>
                </tr>
              </thead>
              <tbody>
                {relevantCoverage.map((c, i) => (
                  <tr key={i} className="border-b border-border-soft align-top last:border-0">
                    <th scope="row" className="px-3 py-1.5 text-left font-normal">{c.provider} · {c.dataset}</th>
                    <td className="px-3 py-1.5">{geographyName(c.geography)}</td>
                    <td className="px-3 py-1.5">
                      <DataStateBadge state={c.state} reason={c.reason} />
                      {c.reason ? <span className="mt-0.5 block text-text-muted">{c.reason}</span> : null}
                    </td>
                    <td className="num px-3 py-1.5 text-right">{day(c.checkedAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Blueprint>
      ) : null}
    </div>
  );
}
