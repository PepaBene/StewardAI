import { dayLabel, money } from "@/lib/disclosure/coverage";
import { CRITERIA, criterionLabel, lookRows, summary, weightPct, type FormValues, type LookThrough, type ScreenResult } from "@/lib/disclosure/screen";

const TH = "border-b border-border px-2 py-1.5 text-left text-xs font-semibold text-text-muted";
const TD = "border-b border-border-soft px-2 py-1.5 align-top";
const INPUT = "num h-9 w-28 border border-border bg-surface-raised px-2 text-sm outline-none focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30";

/** The seven criteria. A blank box is not a criterion; nothing is defaulted to zero. */
export function CriteriaFields({ values, onChange, disabled }: { values: FormValues; onChange: (next: FormValues) => void; disabled?: boolean }) {
  return (
    <div className="grid gap-x-6 gap-y-3 md:grid-cols-2">
      {CRITERIA.map((c) => (
        <div key={c.key} className="flex flex-col gap-1 text-sm">
          {c.kind === "flag" ? (
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={values[c.key] === true} disabled={disabled} onChange={(e) => onChange({ ...values, [c.key]: e.target.checked })} aria-describedby={`${c.key}-hint`} />
              <span className="font-medium">{c.label}</span>
            </label>
          ) : (
            <label className="flex flex-col gap-1">
              <span className="font-medium">{c.label}</span>
              <input inputMode="decimal" value={typeof values[c.key] === "string" ? (values[c.key] as string) : ""} disabled={disabled} onChange={(e) => onChange({ ...values, [c.key]: e.target.value })} className={INPUT} aria-describedby={`${c.key}-hint`} />
            </label>
          )}
          <span id={`${c.key}-hint`} className="text-xs text-text-muted">{c.hint}</span>
        </div>
      ))}
      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium">Insider window (days, 1 to 365)</span>
        <input inputMode="numeric" value={values.insider_window_days ?? ""} placeholder="90" disabled={disabled} onChange={(e) => onChange({ ...values, insider_window_days: e.target.value })} className={INPUT} />
      </label>
    </div>
  );
}

/** Matched holdings, then every excluded holding with its reasons; the counts always sit against the universe. */
export function ScreenResults({ result }: { result: ScreenResult }) {
  return (
    <div className="space-y-4" aria-live="polite">
      <p className="text-sm font-medium">{summary(result)}</p>
      <p className="num text-xs text-text-muted">
        Universe: {result.universe_basis}, {result.universe_size} holdings, market value {money(result.denominator_value)}.
        {result.matched_weight !== null && result.active_criteria.length ? ` Matched holdings are ${weightPct(result.matched_weight)} of it.` : ""}
      </p>
      {result.field_coverage.length ? (
        <ul className="flex flex-wrap gap-2 text-xs">
          {result.field_coverage.map((f) => (
            <li key={f.criterion} className="border border-border-soft px-2 py-1">
              {criterionLabel(f.criterion)}: <span className="num">{f.with_data}</span> with data, <span className="num">{f.without_data}</span> without
            </li>
          ))}
        </ul>
      ) : null}

      {result.matched.length ? (
        <table className="w-full text-sm">
          <caption className="mb-1 text-left font-semibold">Matched ({result.matched_count})</caption>
          <thead>
            <tr>
              <th scope="col" className={TH}>Holding</th>
              <th scope="col" className={TH}>Weight</th>
              <th scope="col" className={TH}>Verified values (period, known on, source)</th>
            </tr>
          </thead>
          <tbody>
            {result.matched.map((m) => (
              <tr key={m.ticker}>
                <th scope="row" className={`${TD} text-left font-medium`}>{m.ticker}</th>
                <td className={`${TD} num`}>{weightPct(m.weight)}</td>
                <td className={TD}>
                  <ul className="space-y-0.5">
                    {m.values.map((v) => (
                      <li key={v.criterion}>
                        {v.label}: <span className="num font-medium">{v.value}</span> <span className="text-xs text-text-muted">{v.unit}{v.period ? ` · ${v.period}` : ""} · known {dayLabel(v.known_at)} · {v.source}</span>
                      </li>
                    ))}
                  </ul>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : result.active_criteria.length ? (
        <p className="border border-dashed border-border bg-surface-muted px-3 py-2 text-sm">No holding matched every chosen criterion.</p>
      ) : null}

      {result.excluded.length ? (
        <table className="w-full text-sm">
          <caption className="mb-1 text-left font-semibold">Excluded ({result.excluded.length}), with the reason</caption>
          <thead>
            <tr>
              <th scope="col" className={TH}>Holding</th>
              <th scope="col" className={TH}>Weight</th>
              <th scope="col" className={TH}>Why</th>
            </tr>
          </thead>
          <tbody>
            {result.excluded.map((e) => (
              <tr key={e.ticker}>
                <th scope="row" className={`${TD} text-left font-medium`}>{e.ticker}</th>
                <td className={`${TD} num`}>{weightPct(e.weight)}</td>
                <td className={TD}>
                  <ul className="space-y-0.5">
                    {e.reasons.map((r) => (
                      <li key={r.criterion}>
                        <span className={`mr-1 border px-1 text-xs ${r.kind === "missing" ? "border-dashed border-border" : "border-warning-signal/40 bg-warning-signal-subtle"}`}>{r.kind === "missing" ? "Missing data" : "Fails the bar"}</span>
                        {r.reason}
                      </li>
                    ))}
                  </ul>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {result.notes.map((n) => (
        <p key={n} className="text-xs text-text-muted">{n}</p>
      ))}
    </div>
  );
}

/** Direct and fund exposure to confirmed relationships and deals; unknown weights stay visible and unsummed. */
export function LookThroughPanel({ data }: { data: LookThrough }) {
  const rows = lookRows(data);
  return (
    <div className="space-y-4">
      <p className="num text-xs text-text-muted">
        Denominator: whole portfolio, market value {money(data.denominator_value)}, {data.holdings} holdings. Weights are shares of it.
      </p>
      {data.mixed_as_of ? (
        <p role="note" className="border border-dashed border-border bg-surface-muted px-3 py-2 text-sm">
          Mixed dates: {data.as_of_dates.map((d) => `${d.kind.replaceAll("_", " ")} ${d.dates.join(", ")}`).join("; ")}. Fund holdings are as old as the fund&apos;s report.
        </p>
      ) : null}
      <table className="w-full text-sm">
        <caption className="sr-only">Portfolio weight by kind of exposure</caption>
        <thead>
          <tr>
            <th scope="col" className={TH}>Exposure</th>
            <th scope="col" className={TH}>Share of portfolio</th>
            <th scope="col" className={TH}>Holdings</th>
            <th scope="col" className={TH}>Note</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label}>
              <th scope="row" className={`${TD} text-left font-medium`}>{r.label}</th>
              <td className={`${TD} num`}>{weightPct(r.weight)}</td>
              <td className={TD}>{r.holdings.length ? r.holdings.join(", ") : "—"}</td>
              <td className={`${TD} text-text-muted`}>{r.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data.relationships.length ? (
        <table className="w-full text-sm">
          <caption className="mb-1 text-left font-semibold">Confirmed relationships</caption>
          <thead>
            <tr>
              <th scope="col" className={TH}>Holding</th>
              <th scope="col" className={TH}>Counterparty</th>
              <th scope="col" className={TH}>Share reported</th>
              <th scope="col" className={TH}>Portfolio weight at stake</th>
              <th scope="col" className={TH}>Period, known on</th>
            </tr>
          </thead>
          <tbody>
            {data.relationships.map((r, i) => (
              <tr key={i}>
                <th scope="row" className={`${TD} text-left font-medium`}>{r.ticker} <span className="text-xs font-normal text-text-muted">{r.relationship}</span></th>
                <td className={TD}>{r.anonymous ? "Anonymous (not named in the filing)" : (r.counterparty_name ?? "Unnamed")}{r.concentrated_customer ? " · concentrated" : ""}</td>
                <td className={`${TD} num`}>{r.share === null ? "not stated" : weightPct(r.share, 0)}</td>
                <td className={`${TD} num`}>{r.weighted_share === null ? "unknown" : weightPct(r.weighted_share, 2)}</td>
                <td className={`${TD} num`}>{r.fiscal_period ?? "period unknown"} · {dayLabel(r.known_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="border border-dashed border-border bg-surface-muted px-3 py-2 text-sm">No confirmed relationship is stored for a held company. That is not proof there is none.</p>
      )}
      {data.funds.length ? (
        <table className="w-full text-sm">
          <caption className="mb-1 text-left font-semibold">Held funds read</caption>
          <thead>
            <tr>
              <th scope="col" className={TH}>Fund</th>
              <th scope="col" className={TH}>Report date</th>
              <th scope="col" className={TH}>Lag (days)</th>
              <th scope="col" className={TH}>Matched / unmatched / unknown lines</th>
            </tr>
          </thead>
          <tbody>
            {data.funds.map((f) => (
              <tr key={f.fund_ticker}>
                <th scope="row" className={`${TD} text-left font-medium`}>{f.fund_ticker}</th>
                <td className={`${TD} num`}>{f.report_period_end}</td>
                <td className={`${TD} num`}>{f.lag_days}</td>
                <td className={`${TD} num`}>{weightPct(f.mapped_pct)} / {weightPct(f.unmapped_pct)} / {f.unknown_lines} lines</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}
