import { exposureOrder, relationshipOrder, weightText, type LookThrough } from "@/lib/disclosure/look-through";
import { safeHref } from "@/lib/reference/identity";

const TH = "border-b border-border px-2 py-1.5 text-left text-xs font-semibold text-text-muted";
const TD = "border-b border-border-soft px-2 py-1.5 align-top";

/** Direct weight, weight reached through held funds, and the funds behind it. Largest first. */
export function ExposureTable({ view }: { view: LookThrough }) {
  const rows = exposureOrder(view.exposures);
  if (rows.length === 0) return <p className="text-sm text-text-muted">No exposure could be computed from the holdings stored at this date.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">Exposure by company: direct weight, weight through held funds and the funds that reach it</caption>
        <thead>
          <tr>
            <th scope="col" className={TH}>Company</th>
            <th scope="col" className={`${TH} text-right`}>Direct</th>
            <th scope="col" className={`${TH} text-right`}>Through funds</th>
            <th scope="col" className={`${TH} text-right`}>Total</th>
            <th scope="col" className={TH}>Funds</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.ticker}>
              <th scope="row" className={`${TD} num text-left font-medium`}>{e.ticker}{e.held_directly ? "" : " (not held directly)"}</th>
              <td className={`${TD} num text-right`}>{weightText(e.direct_weight)}</td>
              <td className={`${TD} num text-right`}>{weightText(e.fund_weight)}</td>
              <td className={`${TD} num text-right font-medium`}>{weightText(e.total_weight)}</td>
              <td className={`${TD} text-xs text-text-muted`}>
                {e.funds.length === 0 ? "none" : e.funds.map((f) => `${f.fund_ticker} ${weightText(f.fund_pct)} of the fund, report ${f.lag_days} days old`).join("; ")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Customer and supplier records weighted by the holding. An unstated share reads "unknown". */
export function RelationshipTable({ view }: { view: LookThrough }) {
  const rows = relationshipOrder(view.relationships);
  if (rows.length === 0) return <p className="text-sm text-text-muted">No customer or supplier record is stored for the holdings. That is not proof that none exists.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">Customer and supplier records with the stated share and the portfolio weight it carries</caption>
        <thead>
          <tr>
            <th scope="col" className={TH}>Holding</th>
            <th scope="col" className={TH}>Counterparty</th>
            <th scope="col" className={TH}>Role</th>
            <th scope="col" className={`${TH} text-right`}>Stated share</th>
            <th scope="col" className={`${TH} text-right`}>Weight in portfolio</th>
            <th scope="col" className={TH}>Source</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.ticker}|${r.relationship}|${r.counterparty_key ?? r.counterparty_name ?? "anonymous"}|${r.fiscal_period ?? ""}`}>
              <th scope="row" className={`${TD} num text-left font-medium`}>{r.ticker}</th>
              <td className={TD}>{r.counterparty_name ?? (r.anonymous ? "Not named" : "Not recorded")}{r.concentrated_customer ? " (concentrated customer)" : ""}</td>
              <td className={TD}>{r.relationship === "customer" ? "Customer" : "Supplier"}</td>
              <td className={`${TD} num text-right`}>{weightText(r.share)}{r.share_basis ? <span className="block text-xs text-text-muted">{r.share_basis}</span> : null}</td>
              <td className={`${TD} num text-right`}>{r.weight_state === "unknown" ? "unknown" : weightText(r.weighted_share, 2)}</td>
              <td className={`${TD} text-xs`}>
                known {r.known_at.slice(0, 10)}
                {r.source_url ? (
                  <>
                    {" "}
                    <a href={safeHref(r.source_url) ?? undefined} target="_blank" rel="noopener noreferrer" className="text-accent-ink hover:underline">filing</a>
                  </>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Deals that touch held names, with the weight at stake. */
export function DealExposureTable({ view }: { view: LookThrough }) {
  if (view.deals.length === 0) return <p className="text-sm text-text-muted">No active deal is known for a holding at this date.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">Deals involving held companies and the portfolio weight each touches</caption>
        <thead>
          <tr>
            <th scope="col" className={TH}>Holding</th>
            <th scope="col" className={TH}>Deal</th>
            <th scope="col" className={TH}>State</th>
            <th scope="col" className={`${TH} text-right`}>Weight</th>
          </tr>
        </thead>
        <tbody>
          {view.deals.map((d) => (
            <tr key={`${d.ticker}|${d.deal_id}`}>
              <th scope="row" className={`${TD} num text-left font-medium`}>{d.ticker}</th>
              <td className={TD}>{d.title}</td>
              <td className={TD}>{d.state ?? "not resolved"}</td>
              <td className={`${TD} num text-right`}>{weightText(d.holding_weight)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
