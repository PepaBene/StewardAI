"use client";

import { ExternalLinkIcon } from "lucide-react";
import { useCallback } from "react";

import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { formatCompact, formatPct } from "@/lib/charts/format";
import { useApiResource, type ApiResult } from "@/lib/hooks/use-api-resource";
import { fetchCountryContext, fetchIndustryContext } from "@/lib/macro/api";
import { geographyName, type MappedTarget } from "@/lib/macro/logic";
import type { Claim, ResearchContext } from "@/lib/macro/types";
import { isExternalUrl } from "@/lib/sources/sources";

import { ExposureTag } from "./exposure-tag";
import { day, formatFigure } from "./format";
import { ResourceBoundary } from "./resource-boundary";

function claimValue(c: Claim): string {
  if (c.value === null) return c.textValue ?? "n/a";
  if (c.unit === "USD" || c.currency === "USD") return `$${formatCompact(c.value)}`;
  return `${formatFigure(c.value)}${c.unit ? ` ${c.unit}` : ""}`;
}

/** Cited claims of a research context: value, period, when the app learned it, source and the note (previous period and change). */
export function ClaimList({ claims }: { claims: readonly Claim[] }) {
  if (claims.length === 0) return null;
  return (
    <div className="relative overflow-x-auto">
      <table className="w-full min-w-[34rem] text-xs">
        <caption className="sr-only">Claims with value, period, known-since date and source</caption>
        <thead className="bg-surface-muted">
          <tr className="border-b border-border text-left text-[11px] tracking-[0.08em] text-text-muted uppercase">
            <th scope="col" className="px-3 py-1.5 font-medium">Claim</th>
            <th scope="col" className="px-3 py-1.5 text-right font-medium">Value</th>
            <th scope="col" className="px-3 py-1.5 font-medium">Period</th>
            <th scope="col" className="px-3 py-1.5 font-medium">Known since</th>
            <th scope="col" className="px-3 py-1.5 font-medium">Source</th>
          </tr>
        </thead>
        <tbody>
          {claims.map((c) => (
            <tr key={c.claimId} className="border-b border-border-soft align-top last:border-0">
              <th scope="row" className="px-3 py-1.5 text-left font-normal">
                {c.label}
                {c.note ? <span className="block text-text-muted">{c.note}</span> : null}
              </th>
              <td className="num px-3 py-1.5 text-right">{claimValue(c)}</td>
              <td className="num px-3 py-1.5">{c.period ? day(c.period) : "n/a"}</td>
              <td className="num px-3 py-1.5">{day(c.knownAsOf)}</td>
              <td className="px-3 py-1.5">
                {isExternalUrl(c.sourceUrl) ? (
                  <a href={c.sourceUrl} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-0.5 text-accent-ink hover:underline">
                    {c.source}
                    <ExternalLinkIcon aria-hidden="true" className="size-3" />
                    <span className="sr-only">(opens in a new tab)</span>
                  </a>
                ) : (
                  c.source
                )}
                <span className="block text-text-muted">{c.provenance}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ContextBody({ ctx }: { ctx: ResearchContext }) {
  const macro = ctx.claims.filter((c) => c.domain === "macro");
  const trade = ctx.claims.filter((c) => c.domain === "trade");
  const other = ctx.claims.filter((c) => c.domain !== "macro" && c.domain !== "trade");
  return (
    <div className="flex flex-col gap-3">
      {macro.length > 0 ? <ClaimList claims={macro} /> : null}
      {trade.length > 0 ? (
        <details className="border border-border-soft text-sm">
          <summary className="cursor-pointer px-3 py-1.5 text-xs font-medium text-accent-ink select-none hover:underline">
            {trade.length} trade row{trade.length === 1 ? "" : "s"} (largest in the latest period)
          </summary>
          <div className="border-t border-border-soft">
            <ClaimList claims={trade} />
          </div>
        </details>
      ) : null}
      {other.length > 0 ? <ClaimList claims={other} /> : null}
      {ctx.gaps.length > 0 ? (
        <ul className="m-0 list-disc pl-5 text-xs text-text-muted">
          {ctx.gaps.map((g, i) => (
            <li key={i}>
              {g.domain}: {g.reason}
            </li>
          ))}
        </ul>
      ) : null}
      <p className="text-[11px] text-text-muted">
        As of {day(ctx.asOf)}. {ctx.method}
      </p>
    </div>
  );
}

function TargetHeader({ target, title }: { target: MappedTarget; title: string }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div className="min-w-0">
        <h3 className="font-heading text-lg leading-tight font-semibold">{title}</h3>
        <p className="text-xs text-text-muted">
          {formatPct(target.weight, 1)} of the issuer mapped here · {target.segments.join(", ")}
        </p>
      </div>
      <span className="flex flex-wrap gap-1">
        {target.bases.map((b) => (
          <ExposureTag key={b} basis={b} />
        ))}
      </span>
    </div>
  );
}

function CountryCard({ target }: { target: MappedTarget }) {
  const fetcher = useCallback((): Promise<ApiResult<ResearchContext>> => fetchCountryContext(target.key.toUpperCase()), [target.key]);
  const { state } = useApiResource(fetcher);
  return (
    <article className="flex min-w-0 flex-col gap-3 border border-border-soft p-3">
      <TargetHeader target={target} title={geographyName(target.key)} />
      <ResourceBoundary state={state} what={`${target.key} context`}>
        {(ctx) => <ContextBody ctx={ctx} />}
      </ResourceBoundary>
    </article>
  );
}

function IndustryCard({ target }: { target: MappedTarget }) {
  const fetcher = useCallback((): Promise<ApiResult<ResearchContext>> => fetchIndustryContext(target.key), [target.key]);
  const { state } = useApiResource(fetcher);
  return (
    <article className="flex min-w-0 flex-col gap-3 border border-border-soft p-3">
      <TargetHeader target={target} title={`HS ${target.key}`} />
      <ResourceBoundary state={state} what={`HS ${target.key} context`}>
        {(ctx) => <ContextBody ctx={ctx} />}
      </ResourceBoundary>
    </article>
  );
}

const MAX_CARDS = 6;

/**
 * Country and industry context for the targets a holding is mapped to. A card exists only for
 * a mapped target, shows the mapping weight and basis beside the cited claims, and says nothing
 * about a country the holding is not mapped to. The context claims are the country's or product's
 * own stored observations; they are not a company-specific effect.
 */
export function ContextCards({ targets, ticker }: { targets: readonly MappedTarget[]; ticker: string }) {
  const countries = targets.filter((t) => t.kind === "geography");
  const products = targets.filter((t) => t.kind === "hs_product");
  const shownCountries = countries.slice(0, MAX_CARDS);
  const shownProducts = products.slice(0, MAX_CARDS);
  return (
    <Blueprint as="section" aria-labelledby="context-cards-heading" className="flex flex-col gap-3 px-5 py-5">
      <PanelHeading kicker="Country and industry context" title={<span id="context-cards-heading">Context for what {ticker} is mapped to</span>} />
      {targets.length === 0 ? (
        <p className="border border-dashed border-border px-3 py-4 text-sm text-text-muted">
          {ticker} has no mapped country or product, so no context is attached to it and no effect is inferred. Its sensitivity to any country is unknown.
        </p>
      ) : (
        <>
          <p className="max-w-prose text-sm text-text-muted">
            Only countries and products mapped to {ticker} appear here, each with its mapped weight and evidence basis. The claims are the stored observations of that country or product, with the date the app learned them.
          </p>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {shownCountries.map((t) => (
              <CountryCard key={`g-${t.key}`} target={t} />
            ))}
            {shownProducts.map((t) => (
              <IndustryCard key={`p-${t.key}`} target={t} />
            ))}
          </div>
          {countries.length > MAX_CARDS || products.length > MAX_CARDS ? <p className="text-xs text-text-muted">Showing the {MAX_CARDS} largest of each; the rest are in the mapping table.</p> : null}
        </>
      )}
    </Blueprint>
  );
}
