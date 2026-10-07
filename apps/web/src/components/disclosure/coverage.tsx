import { blindSpots, cellFor, cellSummary, dayLabel, failureLine, jurisdictions, money, needsAttention, pct, qualityLine, QUALITY_LABEL, rollup, STAGE_LABEL, type CoverageReport } from "@/lib/disclosure/coverage";

const TH = "border-b border-border px-2 py-1.5 text-left text-xs font-semibold text-text-muted";
const TD = "border-b border-border-soft px-2 py-1.5 align-top";

/**
 * Covered market value per dataset as a bar with its denominator, and the same numbers in a table.
 * A dataset with no denominator shows "unknown", never an empty bar read as zero.
 */
export function CoverageBars({ report }: { report: CoverageReport }) {
  const rows = rollup(report.cells);
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
      <ul aria-label="Covered share of held market value, by dataset" className="space-y-3">
        {rows.map((r) => (
          <li key={r.dataset}>
            <div className="flex justify-between gap-3 text-sm">
              <span className="font-medium">{r.label}</span>
              <span className="num text-text-muted">{pct(r.share === null ? null : String(r.share))} of value</span>
            </div>
            <div className="mt-1 h-3 w-full border border-border bg-surface-muted" role="img" aria-label={`${r.label}: ${r.covered} of ${r.tickers} holdings covered, ${pct(r.share === null ? null : String(r.share))} of held value`}>
              <div className="h-full bg-accent-ink" style={{ width: `${r.share === null ? 0 : Math.round(r.share * 100)}%` }} />
            </div>
          </li>
        ))}
      </ul>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <caption className="sr-only">Coverage by dataset over {report.holdings} holdings with a denominator of {money(report.denominator_value)}</caption>
          <thead>
            <tr>
              <th scope="col" className={TH}>Dataset</th>
              <th scope="col" className={TH}>Covered / held</th>
              <th scope="col" className={TH}>Uncovered</th>
              <th scope="col" className={TH}>Stale</th>
              <th scope="col" className={TH}>Value covered / total</th>
              <th scope="col" className={TH}>First known</th>
              <th scope="col" className={TH}>Last known</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.dataset}>
                <th scope="row" className={`${TD} text-left font-medium`}>{r.label}</th>
                <td className={`${TD} num`}>{r.covered} / {r.tickers}</td>
                <td className={`${TD} num`}>{r.uncovered}</td>
                <td className={`${TD} num`}>{r.stale}</td>
                <td className={`${TD} num`}>{money(String(r.coveredValue))} / {money(String(r.denominator))}</td>
                <td className={`${TD} num`}>{dayLabel(r.first)}</td>
                <td className={`${TD} num`}>{dayLabel(r.last)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Dataset by jurisdiction. A dashed border marks a cell with nothing stored or only old records. */
export function CoverageMatrix({ report }: { report: CoverageReport }) {
  const cols = jurisdictions(report.cells);
  const rows = rollup(report.cells);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">Covered holdings by dataset and jurisdiction. Dashed cells have no stored records or only old ones.</caption>
        <thead>
          <tr>
            <th scope="col" className={TH}>Dataset</th>
            {cols.map((j) => (
              <th key={j} scope="col" className={TH}>{j === "XX" ? "Unknown jurisdiction" : j}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.dataset}>
              <th scope="row" className={`${TD} text-left font-medium`}>{r.label}</th>
              {cols.map((j) => {
                const c = cellFor(report.cells, r.dataset, j);
                return (
                  <td key={j} className={`${TD} ${c && needsAttention(c) ? "border border-dashed border-border" : ""}`}>
                    <div>{cellSummary(c)}</div>
                    {c ? (
                      <div className="num text-xs text-text-muted">
                        {pct(c.covered_share)} of {money(c.denominator_value)}
                        {c.stale ? ` · ${c.stale} stale` : ""}
                      </div>
                    ) : null}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function BlindSpotList({ report }: { report: CoverageReport }) {
  const spots = blindSpots(report.cells);
  if (spots.length === 0) return <p className="text-sm text-text-muted">No blind spot is recorded. That is not proof there is none.</p>;
  return (
    <ul className="list-disc space-y-1 pl-5 text-sm">
      {spots.map((s) => (
        <li key={`${s.dataset}|${s.text}`}>
          <strong>{s.dataset}:</strong> {s.text}
        </li>
      ))}
    </ul>
  );
}

export function FailureList({ report }: { report: CoverageReport }) {
  if (report.failures.length === 0) return <p className="text-sm text-text-muted">No collection failure is recorded.</p>;
  return (
    <ul className="space-y-1 text-sm">
      {report.failures.map((f, i) => (
        <li key={i} className="border border-warning-signal/40 bg-warning-signal-subtle px-3 py-1.5">
          {failureLine(f)}
          {f.last_checked_at ? <span className="num text-text-muted"> · checked {dayLabel(f.last_checked_at)}</span> : null}
        </li>
      ))}
    </ul>
  );
}

export function QualityTable({ report }: { report: CoverageReport }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">Sampled error rates from a person&apos;s labels, with numerator and denominator</caption>
        <thead>
          <tr>
            <th scope="col" className={TH}>Dataset</th>
            <th scope="col" className={TH}>Wrong / labelled</th>
            <th scope="col" className={TH}>State</th>
            <th scope="col" className={TH}>Detail</th>
          </tr>
        </thead>
        <tbody>
          {report.quality.map((q) => (
            <tr key={q.dataset}>
              <th scope="row" className={`${TD} text-left font-medium`}>{q.dataset.replaceAll("_", " ")}</th>
              <td className={`${TD} num`}>{q.labelled === 0 ? "not sampled" : `${q.errors} / ${q.labelled}`}</td>
              <td className={TD}>{QUALITY_LABEL[q.state]}</td>
              <td className={`${TD} text-text-muted`}>{qualityLine(q)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function PeriodTable({ report }: { report: CoverageReport }) {
  if (report.by_period.length === 0) return <p className="text-sm text-text-muted">No financial period is stored for a held issuer yet.</p>;
  return (
    <table className="w-full text-sm">
      <caption className="sr-only">Financial coverage by fiscal year</caption>
      <thead>
        <tr>
          <th scope="col" className={TH}>Fiscal year</th>
          <th scope="col" className={TH}>Issuers covered / with identity</th>
          <th scope="col" className={TH}>Value covered / total</th>
        </tr>
      </thead>
      <tbody>
        {report.by_period.map((p) => (
          <tr key={`${p.dataset}-${p.period}`}>
            <th scope="row" className={`${TD} text-left font-medium num`}>{p.period}</th>
            <td className={`${TD} num`}>{p.issuers_covered} / {p.issuers_total}</td>
            <td className={`${TD} num`}>{money(p.covered_value)} / {money(p.denominator_value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function CostAndRelease({ report }: { report: CoverageReport }) {
  const c = report.cost_ledger;
  return (
    <div className="grid gap-5 md:grid-cols-2">
      <div className="space-y-2">
        <h3 className="text-sm font-semibold">Free-tier cost ledger</h3>
        <p className="num text-2xl font-semibold">${c.total_usd}</p>
        <p className="text-sm text-text-muted">{c.basis} {c.registered_sources} sources registered, {c.paid_sources} paid.</p>
        <ul className="list-disc space-y-0.5 pl-5 text-sm">
          {c.sources.map((s) => (
            <li key={s.name}>{s.name} <span className="text-text-muted">· {s.reference} · ${s.cost_usd}</span></li>
          ))}
        </ul>
      </div>
      <div className="space-y-2">
        <h3 className="text-sm font-semibold">Release order</h3>
        <ol className="space-y-1.5 text-sm">
          {report.release_order.map((s) => (
            <li key={s.order} className="border border-border-soft px-3 py-1.5">
              <span className="font-medium">{s.order}. {s.stage}</span> <span className="text-text-muted">· {STAGE_LABEL[s.state]}</span>
              {Object.entries(s.gates).length ? (
                <div className="text-xs text-text-muted">
                  {Object.entries(s.gates).map(([d, g]) => `${d}: ${g === null ? "no sampled gate" : g.replaceAll("_", " ")}`).join(" · ")}
                </div>
              ) : null}
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
