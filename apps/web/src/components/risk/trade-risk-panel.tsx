import { formatPercent } from "@/lib/format";
import { changeText, isWorse, pointsChange, type TradeRisk } from "@/lib/risk/sizing";
import { cn } from "@/lib/utils";

/** The book’s forecast risk before and after a trade: the same model as “Today’s risk”, run on the book as it would stand. */
export function TradeRiskPanel({ risk }: { risk: TradeRisk }) {
  if (!risk.available) {
    return <p className="text-sm text-text-muted">The risk after the trade could not be forecast: {risk.reason ?? "no forecast"}.</p>;
  }
  const worse = risk.limits.filter((l) => isWorse(l.change));
  return (
    <div className="space-y-4">
      <dl className="grid gap-x-8 gap-y-2 sm:grid-cols-3">
        <Figure
          label="Forecast volatility, a year"
          before={formatPercent(risk.volatility_before)}
          after={formatPercent(risk.volatility_after)}
          change={pointsChange(risk.volatility_before, risk.volatility_after)}
          note={risk.volatility_limit != null ? `Your limit is ${formatPercent(risk.volatility_limit, { decimals: 0 })}` : undefined}
        />
        <Figure
          label="Independent bets"
          before={risk.effective_bets_before?.toFixed(1) ?? "—"}
          after={risk.effective_bets_after?.toFixed(1) ?? "—"}
        />
        <Figure
          label="Diversification ratio"
          before={risk.diversification_before?.toFixed(2) ?? "—"}
          after={risk.diversification_after?.toFixed(2) ?? "—"}
        />
      </dl>
      {risk.limits.length > 0 ? (
        <ul className="space-y-1.5">
          {risk.limits.map((l) => (
            <li key={l.key} className={cn("border-l-2 py-0.5 pl-3 text-sm", isWorse(l.change) ? "border-warning-signal" : "border-border")}>
              <span className="font-semibold">{changeText(l.change)}.</span> <span className="text-text-muted">{l.detail}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-text-muted">No ex-ante risk limit is breached before or after the trade.</p>
      )}
      {worse.length === 0 && risk.limits.length > 0 ? <p className="text-sm text-text-muted">None of these gets worse with the trade.</p> : null}
      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <caption className="pb-1 text-left text-xs font-medium text-text-muted">Largest sources of risk and every holding the trade moves</caption>
            <thead>
              <tr className="border-b border-border text-left">
                <th scope="col" className="py-1.5 pr-3 font-medium">Holding</th>
                <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Weight</th>
                <th scope="col" className="num py-1.5 text-right font-medium">Share of risk</th>
              </tr>
            </thead>
            <tbody>
              {risk.lines.map((l) => (
                <tr key={l.ticker} className="border-b border-border-soft last:border-b-0">
                  <th scope="row" className="py-1.5 pr-3 text-left font-semibold">
                    {l.ticker}
                    {l.status_after === "excluded" ? <span className="ml-1.5 text-xs font-normal text-text-muted">not in model</span> : null}
                  </th>
                  <td className="num py-1.5 pr-3 text-right">
                    {formatPercent(l.weight_before)} → {formatPercent(l.weight_after)}
                  </td>
                  <td className="num py-1.5 text-right">
                    {l.risk_share_before == null ? "—" : formatPercent(l.risk_share_before)} → {l.risk_share_after == null ? "—" : formatPercent(l.risk_share_after)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <caption className="pb-1 text-left text-xs font-medium text-text-muted">Share of risk by tier</caption>
            <thead>
              <tr className="border-b border-border text-left">
                <th scope="col" className="py-1.5 pr-3 font-medium">Tier</th>
                <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Share</th>
                <th scope="col" className="num py-1.5 text-right font-medium">Budget</th>
              </tr>
            </thead>
            <tbody>
              {risk.tiers.map((t) => (
                <tr key={t.tier} className="border-b border-border-soft last:border-b-0">
                  <th scope="row" className="py-1.5 pr-3 text-left font-semibold capitalize">{t.tier}</th>
                  <td className="num py-1.5 pr-3 text-right">
                    {formatPercent(t.risk_share_before, { decimals: 0 })} → {formatPercent(t.risk_share_after, { decimals: 0 })}
                  </td>
                  <td className="num py-1.5 text-right text-text-muted">{t.budget == null ? "—" : formatPercent(t.budget, { decimals: 0 })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {[...risk.warnings, ...risk.not_modelled].map((w) => (
        <p key={w} className="text-xs text-text-muted">
          {w}
        </p>
      ))}
    </div>
  );
}

function Figure({ label, before, after, change, note }: { label: string; before: string; after: string; change?: string; note?: string }) {
  return (
    <div>
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className="num text-lg font-semibold">
        {before} → {after}
      </dd>
      {change ? <dd className="text-xs text-text-muted">{change}</dd> : null}
      {note ? <dd className="text-xs text-text-muted">{note}</dd> : null}
    </div>
  );
}
