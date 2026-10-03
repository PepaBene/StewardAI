import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { ProvenanceFooter } from "@/components/data-state";
import { formatNumber } from "@/lib/charts/format";
import { formatDate } from "@/lib/format";
import { humanize } from "@/lib/network/logic";
import type { KpiLink, Reconciliation, ResearchObservation } from "@/lib/network/types";

import { RowStatusBadge, SourceLink } from "./badges";

/** Deals, leadership, product and patent events tied to thesis KPIs. Only the server's own rule can say an event supports a breach. */
export function KpiLinksPanel({ links, asOfDate }: { links: readonly KpiLink[]; asOfDate: string | null }) {
  return (
    <Blueprint as="section" aria-labelledby="network-kpi-heading" className="flex flex-col gap-3 px-5 py-5">
      <PanelHeading kicker="Thesis" title={<span id="network-kpi-heading">Events linked to thesis KPIs</span>} />
      <p className="max-w-prose text-sm text-text">
        {links.length === 0
          ? "No company event is linked to a thesis KPI."
          : `${links.length} linked events; ${links.filter((l) => l.canSupportBreach).length} are confirmed with linked evidence and may support a KPI breach. A suggested or disputed link can prompt a review only.`}
      </p>
      {links.length > 0 ? (
        <ul className="flex flex-col">
          {links.map((l) => (
            <li key={l.eventId} className="grid grid-cols-1 gap-x-3 gap-y-0.5 border-t border-border-soft py-2 text-sm first:border-0 sm:grid-cols-[minmax(0,1fr)_auto]">
              <span className="min-w-0">
                {humanize(l.eventType)} {l.eventState} → KPI <span className="font-mono text-xs">{l.kpiLabel ?? l.kpiId}</span>
                {!l.kpiDefined ? <span className="block text-xs text-text">This KPI is not in the active thesis.</span> : null}
                <span className="num block text-xs text-text-muted">
                  {l.date ? formatDate(l.date) : "undated"}
                  {l.latestVerdict ? ` · latest verdict ${l.latestVerdict.status}${l.latestVerdict.observed !== null ? ` (observed ${formatNumber(l.latestVerdict.observed, 2)}${l.kpiUnit ? ` ${l.kpiUnit}` : ""}${l.latestVerdict.threshold !== null ? `, threshold ${l.latestVerdict.operator ? `${l.latestVerdict.operator} ` : ""}${formatNumber(l.latestVerdict.threshold, 2)}${l.kpiUnit ? ` ${l.kpiUnit}` : ""}` : ""})` : ""}` : " · no verdict yet"}
                </span>
                {l.newsClusters.length ? <span className="block text-xs text-text-muted">News: {l.newsClusters.map((c) => c.headline).join(" · ")}</span> : null}
                {l.sourceUrl ? <SourceLink href={l.sourceUrl}>source</SourceLink> : null}
              </span>
              <span className="flex flex-wrap items-start gap-1.5">
                <RowStatusBadge status={l.status} />
                <span className="border border-border-soft px-1.5 py-0.5 text-xs text-text-muted">{l.canSupportBreach ? "May support a breach" : "Review prompt only"}</span>
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      <ProvenanceFooter
        provenance={{
          source: "Company events, thesis KPIs, KPI verdicts and news clusters",
          asOf: asOfDate,
          method: "An event supports a breach only when confirmed with linked evidence (the server checks a confirmed status and a linked evidence document, not the evidence tier).",
          state: links.length === 0 ? "not_covered" : "verified",
        }}
      />
    </Blueprint>
  );
}

const SHOWN_METRICS = ["employee_count", "total_funding_raised"];

/** Vendor-derived headcount and funding with match confidence, to compare against filings, plus open parent/successor reconciliations. */
export function ResearchPanel({
  observations,
  reconciliations,
  reconciliationsStatus = "ok",
  asOfDate,
}: {
  observations: readonly ResearchObservation[];
  reconciliations: readonly Reconciliation[];
  /** A failed or loading reconciliation request is not "none open". */
  reconciliationsStatus?: "ok" | "loading" | "unavailable";
  asOfDate: string | null;
}) {
  const shown = observations.filter((o) => SHOWN_METRICS.includes(o.metric));
  return (
    <Blueprint as="section" aria-labelledby="network-research-heading" className="flex flex-col gap-4 px-5 py-5">
      <PanelHeading kicker="Company research" title={<span id="network-research-heading">Headcount, funding and unexplained changes</span>} />
      <div>
        <h3 className="text-sm font-semibold">Vendor observations</h3>
        {shown.length === 0 ? (
          <p className="text-xs text-text-muted">No headcount or funding observation is stored. Nothing is estimated in its place.</p>
        ) : (
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[32rem] text-sm">
              <caption className="sr-only">Vendor headcount and funding observations</caption>
              <thead className="text-[11px] tracking-[0.08em] text-text-muted uppercase">
                <tr className="border-b border-border text-left">
                  <th scope="col" className="px-2 py-1.5 font-medium">Metric</th>
                  <th scope="col" className="px-2 py-1.5 text-right font-medium">Value</th>
                  <th scope="col" className="px-2 py-1.5 font-medium">Period</th>
                  <th scope="col" className="px-2 py-1.5 font-medium">Source</th>
                  <th scope="col" className="px-2 py-1.5 text-right font-medium">Match</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((o) => (
                  <tr key={o.id} className="border-b border-border-soft">
                    <th scope="row" className="px-2 py-1.5 text-left font-normal">{humanize(o.metric)}</th>
                    <td className="num px-2 py-1.5 text-right">
                      {o.value !== null ? `${formatNumber(o.value)}${o.currency ? ` ${o.currency}` : o.unit ? ` ${o.unit}` : ""}` : (o.textValue ?? "not stated")}
                    </td>
                    <td className="num px-2 py-1.5 text-xs">{o.periodEnd ? formatDate(o.periodEnd) : "undated"}</td>
                    <td className="px-2 py-1.5 text-xs"><SourceLink href={o.sourceUrl}>{o.provider}</SourceLink></td>
                    <td className="num px-2 py-1.5 text-right text-xs">{o.matchConfidence === null ? "not stated" : `${Math.round(o.matchConfidence * 100)}%`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="mt-1 text-xs text-text-muted">Vendor figures are unconfirmed context. Compare them with filings and announcements before relying on them.</p>
      </div>
      <div>
        <h3 className="text-sm font-semibold">Parent or successor changes no closed deal explains</h3>
        {reconciliationsStatus !== "ok" ? (
          <p role={reconciliationsStatus === "loading" ? "status" : "alert"} className="text-xs text-text">
            {reconciliationsStatus === "loading" ? "Loading…" : "Open reconciliations could not be loaded, so none can be said to be absent."}
          </p>
        ) : reconciliations.length === 0 ? (
          <p className="text-xs text-text-muted">None open.</p>
        ) : (
          <ul className="mt-1 flex flex-col">
            {reconciliations.map((r) => (
              <li key={r.id} className="border-t border-border-soft py-1.5 text-sm first:border-0">
                {humanize(r.changeKind)}: {r.fromKey ?? "unknown"} → {r.toKey ?? "unknown"}
                <span className="num block text-xs text-text-muted">
                  seen {r.observedAt ? formatDate(r.observedAt.slice(0, 10)) : "undated"} via {r.observedSource ?? "unknown source"} · {humanize(r.outcome)}
                </span>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-1 text-xs text-text-muted">An accounting parent change is never turned into a shareholder or a partnership without a deal or disclosure.</p>
      </div>
      <ProvenanceFooter
        provenance={{
          source: "Diffbot and People Data Labs observations; GLEIF and deal reconciliation",
          asOf: asOfDate,
          method: "Observations are appended with a match confidence; unchanged re-fetches keep the earliest known-at date.",
          state: reconciliationsStatus !== "ok" ? "unavailable" : shown.length === 0 && reconciliations.length === 0 ? "not_covered" : "partial",
          reason: "Vendor-derived and unconfirmed.",
        }}
      />
    </Blueprint>
  );
}
