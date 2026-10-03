import Link from "next/link";

import { StatePlaceholder } from "@/components/data-state";
import { formatNumber } from "@/lib/charts/format";
import { latestKnownAt, summariseHistory, toNumber } from "@/lib/holding-data/logic";
import type { FinancialFact, FinancialHistory } from "@/lib/holding-data/types";

import { day, HoldingPanel, ProvenanceClass, SourceLink, TableWrap, TD, TH } from "./shared";

const SHOWN = 12;

/** Latest period first; within a period, statement then concept, so the order is stable. */
export function latestFacts(facts: readonly FinancialFact[], limit = SHOWN): FinancialFact[] {
  return [...facts]
    .sort((a, b) => b.period_end.localeCompare(a.period_end) || a.statement.localeCompare(b.statement) || a.concept.localeCompare(b.concept) || b.revision - a.revision)
    .slice(0, limit);
}

/**
 * Global financial history of the holding: how many stored statement facts there are, over
 * which periods, from which class of source (primary filing vs vendor), and the latest figures
 * with their source, known-at date and revision. The full statements live on /financials.
 */
export function FinancialHistoryCard({ history, ticker }: { history: FinancialHistory; ticker: string }) {
  const s = summariseHistory(history);
  const shown = latestFacts(history.facts);
  const has = history.total_rows > 0;
  const href = `/financials?ticker=${encodeURIComponent(ticker)}`;
  return (
    <HoldingPanel
      id="financial-history"
      kicker="Global financial history"
      title="Stored statement facts"
      takeaway={
        has
          ? `${history.total_rows} stored fact rows from ${day(history.earliest_period)} to ${day(history.latest_period)}; ${s.provenance.map((p) => `${p.count} ${p.cls}`).join(", ")}.`
          : "No financial statement facts were known by this cutoff."
      }
      action={
        <Link href={href} className="text-xs text-accent-ink hover:underline">
          Open financials
        </Link>
      }
      provenance={{
        source: has ? `Stored statement facts (${s.provenance.map((p) => p.cls).join(" / ")} sources)` : "Stored statement facts",
        asOf: history.latest_period,
        knownAt: latestKnownAt(history.facts.map((f) => f.known_at)),
        method: "Latest revision of each fact known by the cutoff; no restatement is back-filled.",
        state: !has ? "unavailable" : s.truncated ? "partial" : "verified",
        reason: !has ? "No statement facts are stored for this holding." : s.truncated ? `Showing ${s.returned} of ${s.denominator} stored rows.` : null,
        coverage: has ? { covered: s.returned, total: s.denominator, unit: "stored fact rows" } : null,
      }}
    >
      {!has ? (
        <StatePlaceholder state="unavailable" reason="No financial statement facts were known by this cutoff." minHeight={80} />
      ) : (
        <>
          <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-muted">
            {s.statements.map((st) => (
              <li key={st.name} className="num">
                {st.name}: {st.count}
              </li>
            ))}
            {s.provenance.map((p) => (
              <li key={p.cls} className="flex items-center gap-1">
                <ProvenanceClass cls={p.cls} />
                <span className="num">
                  {p.count} of {s.denominator}
                  {p.share !== null ? ` (${Math.round(p.share * 100)}%)` : ""}
                </span>
              </li>
            ))}
          </ul>
          <TableWrap caption={`Latest stored statement facts of ${ticker}`}>
            <thead className="bg-surface-muted">
              <tr className="border-b border-border">
                <th scope="col" className={TH}>Figure</th>
                <th scope="col" className={TH}>Period</th>
                <th scope="col" className={`${TH} text-right`}>Value</th>
                <th scope="col" className={TH}>Source</th>
                <th scope="col" className={TH}>Known at</th>
                <th scope="col" className={`${TH} text-right`}>Rev</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((f) => {
                const v = toNumber(f.value);
                return (
                  <tr key={f.id} className="border-b border-border-soft last:border-0">
                    <th scope="row" className={`${TD} text-left font-normal`}>
                      {f.label ?? f.concept}
                      <span className="block text-text-muted">{f.statement} · {f.basis}</span>
                    </th>
                    <td className={`${TD} num`}>{day(f.period_end)}{f.fiscal_period ? ` · ${f.fiscal_period}` : ""}</td>
                    <td className={`${TD} num text-right`}>{v === null ? "not reported" : `${formatNumber(v, Math.abs(v) < 100 ? 2 : 0)} ${f.currency ?? f.unit}`}</td>
                    <td className={TD}>
                      <span className="flex flex-wrap items-center gap-1">
                        <ProvenanceClass cls={f.provenance} />
                        <SourceLink url={f.source_url} label={f.source} />
                      </span>
                    </td>
                    <td className={`${TD} num`}>{day(f.known_at)}</td>
                    <td className={`${TD} num text-right`}>{f.revision}</td>
                  </tr>
                );
              })}
            </tbody>
          </TableWrap>
          <p className="text-xs text-text-muted">
            Latest {shown.length} of {history.returned_rows} returned rows ({history.total_rows} stored). {history.facts.some((f) => f.evidence_id !== null) ? "Open the evidence ledger below for the source passage of text-block facts." : ""}
          </p>
        </>
      )}
    </HoldingPanel>
  );
}
