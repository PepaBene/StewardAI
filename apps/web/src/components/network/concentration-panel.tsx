import { ChartFrame } from "@/components/data-state";
import { formatPct } from "@/lib/charts/format";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { ProvenanceFooter } from "@/components/data-state";
import { concentrationRows, directionLabel, humanize, largestReportedShare, shareBasisLabel, sideShareLabel } from "@/lib/network/logic";
import type { ConcentrationPayload, KnockOnPayload } from "@/lib/network/types";

function pct(v: number | null): string {
  return v === null ? "not reported" : formatPct(v, 1);
}

const side = (s: Parameters<typeof sideShareLabel>[0]) => sideShareLabel(s, (v) => formatPct(v, 1));

/**
 * Customer and supplier concentration from confirmed, dated, directed, issuer-basis
 * disclosures only. Named and anonymous counterparties stay separate; a sum over rows where
 * some counterparties reported no share is a floor ("at least"), never a total.
 */
export function ConcentrationPanel({ data, asOfDate }: { data: ConcentrationPayload; asOfDate: string | null }) {
  const rows = concentrationRows(data.periods);
  const any = rows.length > 0;
  return (
    <ChartFrame
      id="network-concentration"
      kicker="Customers and suppliers"
      title="Concentration from confirmed disclosures"
      takeaway={
        any
          ? `${rows.length} fiscal-period disclosure ${rows.length === 1 ? "group is" : "groups are"} confirmed; ${largestReportedShare(rows) === null ? "no counterparty reported a share." : `the largest single reported share is ${pct(largestReportedShare(rows))}.`}`
          : "No confirmed, dated customer or supplier disclosure is stored, so no concentration is shown."
      }
      units="Share as reported by the filer, against the company's own revenue (customers) or purchases (suppliers). Shares are never imputed."
      provenance={{
        source: "Relationship disclosures reviewed at passage level (SEC filings, issuer disclosures, TED awards)",
        asOf: asOfDate,
        method: "Only owner-confirmed disclosures with a start date, a fiscal period, a known direction and an issuer basis count.",
        state: any ? (rows.some((r) => r.isFloor || r.openContradictions > 0 || r.shareExceedsTotal) ? "partial" : "verified") : "not_covered",
        reason: any ? null : "Confirm disclosures in the passage review below to build this view.",
        coverage: any ? { covered: rows.filter((r) => !r.isFloor).length, total: rows.length, unit: "periods with every share reported" } : null,
      }}
      hasValue={any}
      table={{
        caption: "Customer and supplier concentration by fiscal period",
        columns: ["Period / relationship", "Basis", "Named", "Anonymous", "Named share", "Anonymous share", "Largest", "Open contradictions"],
        rows: rows.map((r) => [
          `${r.fiscalPeriod} · ${humanize(r.relationship)}`,
          shareBasisLabel(r.shareBasis),
          r.namedCount,
          r.anonymousCount,
          side(r.named),
          side(r.anonymous),
          pct(r.largestShare),
          r.openContradictions,
        ]),
      }}
    >
      <ul className="flex flex-col gap-3">
        {rows.map((r) => {
          const named = Math.min(1, Math.max(0, r.named.value ?? 0));
          const anon = Math.min(1 - named, Math.max(0, r.anonymous.value ?? 0));
          return (
            <li key={`${r.fiscalPeriod}|${r.relationship}|${r.shareBasis}`} className="flex flex-col gap-1">
              <p className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm">
                <span className="font-medium">
                  {r.fiscalPeriod} · {humanize(r.relationship)}s
                </span>
                <span className="num text-xs text-text-muted">
                  {r.namedCount} named, {r.anonymousCount} anonymous · {shareBasisLabel(r.shareBasis)}
                </span>
              </p>
              <div
                role="img"
                aria-label={`Named counterparties ${side(r.named)}, anonymous ${side(r.anonymous)}`}
                className="flex h-3 w-full bg-surface-muted"
              >
                <span className="h-3 bg-accent" style={{ width: `${named * 100}%` }} />
                <span className="h-3 bg-categorical-4" style={{ width: `${anon * 100}%` }} />
              </div>
              <p className="num text-xs text-text-muted">
                Named {side(r.named)} · anonymous {side(r.anonymous)} · largest {pct(r.largestShare)}
                {r.missingShareCount ? ` · ${r.missingShareCount} disclosed without a share (not imputed)` : ""}
              </p>
              {r.shareExceedsTotal ? <p className="text-xs text-text">The reported shares add up to more than 100%: a contradiction to review, not a figure to trust.</p> : null}
              {r.openContradictions ? <p className="text-xs text-text">{r.openContradictions} open contradiction(s) for this period.</p> : null}
            </li>
          );
        })}
      </ul>
      <div className="flex flex-wrap gap-4 text-xs text-text-muted" aria-label="Legend">
        <span className="inline-flex items-center gap-1.5"><span className="inline-block size-3 bg-accent" aria-hidden="true" />Named counterparties</span>
        <span className="inline-flex items-center gap-1.5"><span className="inline-block size-3 bg-categorical-4" aria-hidden="true" />Anonymous counterparties</span>
      </div>
      {data.limitations.length ? <p className="text-xs text-text-muted">{data.limitations.join(" ")}</p> : null}
    </ChartFrame>
  );
}

/** Confirmed, dated links between this holding and other held companies. */
export function KnockOnPanel({ ticker, data, asOfDate }: { ticker: string; data: KnockOnPayload; asOfDate: string | null }) {
  const mine = data.links.filter((l) => l.ticker === ticker || l.counterpartyTicker === ticker);
  return (
    <Blueprint as="section" aria-labelledby="network-knockon-heading" className="flex flex-col gap-3 px-5 py-5">
      <PanelHeading kicker="Portfolio" title={<span id="network-knockon-heading">Links to other holdings</span>} />
      <p className="max-w-prose text-sm text-text">
        {mine.length === 0
          ? `No confirmed, dated customer or supplier link connects this holding to another open position (${data.links.length} such links exist across the portfolio).`
          : `${mine.length} confirmed link${mine.length === 1 ? "" : "s"} to other open positions.`}
      </p>
      {mine.length > 0 ? (
        <div className="relative overflow-x-auto">
          <table className="w-full min-w-[30rem] text-sm">
            <caption className="sr-only">Confirmed customer and supplier links between holdings</caption>
            <thead className="text-[11px] tracking-[0.08em] text-text-muted uppercase">
              <tr className="border-b border-border text-left">
                <th scope="col" className="px-2 py-1.5 font-medium">Holding</th>
                <th scope="col" className="px-2 py-1.5 font-medium">Counterparty holding</th>
                <th scope="col" className="px-2 py-1.5 font-medium">Link</th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">Reported share</th>
              </tr>
            </thead>
            <tbody>
              {mine.map((l) => (
                <tr key={`${l.source}`} className="border-b border-border-soft">
                  <th scope="row" className="px-2 py-1.5 text-left font-mono text-xs font-normal">{l.ticker}</th>
                  <td className="px-2 py-1.5 font-mono text-xs">{l.counterpartyTicker}</td>
                  <td className="px-2 py-1.5 text-xs">
                    {humanize(l.relationship)}, {directionLabel(l.direction)}
                    {l.fiscalPeriod ? ` · ${l.fiscalPeriod}` : ""}
                  </td>
                  <td className="num px-2 py-1.5 text-right">
                    {l.reportedShare === null ? "not reported" : `${formatPct(l.reportedShare, 1)} ${l.shareBasis ? shareBasisLabel(l.shareBasis) : ""}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <ProvenanceFooter
        provenance={{
          source: "Confirmed disclosures and edges between two open positions",
          asOf: asOfDate,
          knownAt: data.knownAsOf,
          method: data.limitations.join(" ") || "Shares are as reported by the filer and are not summed or imputed.",
          state: data.links.length === 0 ? "not_covered" : "verified",
          reason: data.links.length === 0 ? "No confirmed holding-to-holding link is stored." : null,
        }}
      />
    </Blueprint>
  );
}
