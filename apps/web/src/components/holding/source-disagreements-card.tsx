import { DataStateBadge, StatePlaceholder } from "@/components/data-state";
import { formatNumber, formatPct } from "@/lib/charts/format";
import { coverageEntryView, disagreementRows, latestKnownAt } from "@/lib/holding-data/logic";
import type { CoverageEntry, Disagreement } from "@/lib/holding-data/types";

import { day, HoldingPanel, ProvenanceClass, TableWrap, TD, TH } from "./shared";

const val = (v: number | null) => (v === null ? "not reported" : formatNumber(v, Math.abs(v) < 100 ? 2 : 0));

/**
 * Where two sources gave different values for the same figure and period: the preferred source
 * and the alternative with their provenance class (primary filing vs vendor), the difference
 * against the stored tolerance, and the resolution. The per-provider coverage ledger follows,
 * with available of expected periods.
 */
export function SourceDisagreementsCard({ disagreements, coverage, asOf }: { disagreements: readonly Disagreement[]; coverage: readonly CoverageEntry[]; asOf: string | null }) {
  const rows = disagreementRows(disagreements);
  const cross = rows.filter((r) => r.crossClass).length;
  return (
    <HoldingPanel
      id="source-disagreements"
      kicker="Source disagreements"
      title="Primary against vendor"
      takeaway={rows.length === 0 ? "No stored figure has two sources that disagree at this cutoff." : `${rows.length} figure${rows.length === 1 ? "" : "s"} where sources disagree; ${cross} of them set a primary source against a vendor or other class.`}
      provenance={{
        source: "Reconciliation ledger of stored figures",
        asOf,
        knownAt: latestKnownAt(rows.map((r) => r.alternative.knownAt)),
        method: "Preferred source by provenance class; difference compared with the stored tolerance. The vendor value is shown, never silently averaged.",
        state: rows.length === 0 ? "verified" : "disputed",
        reason: rows.length === 0 ? "Nothing in dispute does not prove agreement for figures that are not stored by two sources." : null,
        coverage: null,
      }}
    >
      {rows.length === 0 ? null : (
        <TableWrap caption="Source disagreements of this holding">
          <thead className="bg-surface-muted">
            <tr className="border-b border-border">
              <th scope="col" className={TH}>Figure</th>
              <th scope="col" className={TH}>Preferred</th>
              <th scope="col" className={TH}>Alternative</th>
              <th scope="col" className={`${TH} text-right`}>Difference</th>
              <th scope="col" className={TH}>Resolution</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-b border-border-soft align-top last:border-0">
                <th scope="row" className={`${TD} text-left font-normal`}>{r.metric}<span className="block text-text-muted">period {day(r.periodEnd)} · {r.status}</span></th>
                <td className={TD}><ProvenanceClass cls={r.preferred.provenance} /> <span className="num">{val(r.preferred.value)}</span><span className="block text-text-muted">{r.preferred.source ?? "source not reported"} · known {day(r.preferred.knownAt)}</span></td>
                <td className={TD}><ProvenanceClass cls={r.alternative.provenance} /> <span className="num">{val(r.alternative.value)}</span><span className="block text-text-muted">{r.alternative.source} · known {day(r.alternative.knownAt)}</span></td>
                <td className={`${TD} num text-right`}>
                  {r.difference === null ? "not reported" : formatNumber(r.difference, 2)}
                  <span className="block text-text-muted">{r.tolerance === null ? "tolerance not stored" : `tolerance ${formatNumber(r.tolerance, 2)}${r.exceedsTolerance === null ? "" : r.exceedsTolerance ? " · exceeded" : " · within"}`}</span>
                </td>
                <td className={`${TD} text-text-muted`}>{r.resolution ?? "unresolved"}</td>
              </tr>
            ))}
          </tbody>
        </TableWrap>
      )}
      <h3 className="kicker mt-1">Coverage ledger</h3>
      {coverage.length === 0 ? (
        <StatePlaceholder state="not_covered" reason="No provider has recorded a coverage check for this holding." minHeight={60} />
      ) : (
        <TableWrap caption="Per-provider coverage of this holding">
          <thead className="bg-surface-muted">
            <tr className="border-b border-border">
              <th scope="col" className={TH}>Provider · dataset</th>
              <th scope="col" className={TH}>State</th>
              <th scope="col" className={`${TH} text-right`}>Periods</th>
              <th scope="col" className={TH}>Latest period</th>
              <th scope="col" className={TH}>Checked / reason</th>
            </tr>
          </thead>
          <tbody>
            {coverage.map((c) => {
              const v = coverageEntryView(c);
              return (
                <tr key={`${c.provider}|${c.dataset}|${c.entity_key}`} className="border-b border-border-soft align-top last:border-0">
                  <th scope="row" className={`${TD} text-left font-normal`}>{c.provider} · {c.dataset}<span className="block text-text-muted">{c.entity_key}</span></th>
                  <td className={TD}><DataStateBadge state={v.state} reason={c.reason} /></td>
                  <td className={`${TD} num text-right`}>{v.covered !== null && v.total !== null ? `${v.covered} of ${v.total} (${formatPct(v.covered / v.total, 0)})` : "no denominator"}</td>
                  <td className={`${TD} num`}>{day(c.latest_period)}</td>
                  <td className={`${TD} text-text-muted`}>{day(c.checked_at)}{c.reason ? ` · ${c.reason}` : ""}</td>
                </tr>
              );
            })}
          </tbody>
        </TableWrap>
      )}
    </HoldingPanel>
  );
}
