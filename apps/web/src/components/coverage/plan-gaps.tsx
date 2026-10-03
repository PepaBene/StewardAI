import { Blueprint, PanelHeading } from "@/components/ui/blueprint";

/** Static documentation text from the free-data-sources plan. It is NOT live data and carries no figure. */
export const PLAN_GAPS: readonly { dataset: string; path: string; consequence: string }[] = [
  {
    dataset: "Long, clean global total-return history and complete corporate actions",
    path: "Exchange/issuer announcements and the existing Stooq/Yahoo path where available; selective Alpha Vantage/EODHD checks.",
    consequence: "Return, benchmark and backtest windows can be shorter or partial; never splice price-only and total-return series.",
  },
  {
    dataset: "Complete point-in-time global listed-company financials, segments and shares",
    path: "SEC filings, ESEF filings and issuer annual/interim reports; vendor facts only as field-level gap fills.",
    consequence: "Deep financial history and cross-market screener coverage are measured, not assumed.",
  },
  {
    dataset: "Global analyst consensus and revision history",
    path: "Verified free event estimates or company guidance when available.",
    consequence: "Consensus screens, revisions and surprise histories remain unavailable for many securities. Owner scenarios are labelled separately.",
  },
  {
    dataset: "Full global institutional/insider ownership and short-interest history",
    path: "SEC 13F/insider filings and jurisdiction-specific official filings added case by case.",
    consequence: "US coverage is deeper; non-US ownership cannot be inferred from a provider marketing page.",
  },
  {
    dataset: "Full historical index membership, constituent weights and official total-return benchmarks",
    path: "Issuer-published fund holdings or public index factsheets as dated proxies, with provenance.",
    consequence: "Historical index attribution/backtesting is disabled without genuine as-of constituents.",
  },
  {
    dataset: "Global fund holdings/NAV/fees and fixed-income security terms",
    path: "Issuer disclosures and US N-PORT where available.",
    consequence: "Fund comparison/X-Ray shows stale or unknown weight; bond duration/quality only where actually published.",
  },
  {
    dataset: "Company-level supplier/customer contracts and geographic revenue for all issuers",
    path: "Reported segments, cited company disclosures and owner-reviewed graph evidence.",
    consequence: "UN Comtrade and macro data provide context, not invented company counterparties or revenue weights.",
  },
  {
    dataset: "Corporate actions and tax treatments across every jurisdiction",
    path: "Broker ledger, issuer disclosures and owner-confirmed exceptions.",
    consequence: "Personal return and gain calculations identify unreconciled events; no unsupported tax filing result.",
  },
];

export function PlanGaps() {
  return (
    <Blueprint as="section" aria-labelledby="plan-gaps-heading" id="plan-gaps" className="flex scroll-mt-24 flex-col gap-3 px-5 py-5">
      <PanelHeading kicker="Plan scope, not data" title={<span id="plan-gaps-heading">Data still missing after the plan</span>} />
      <p className="max-w-prose text-sm text-text-muted">
        Documentation copied from the free-data-sources plan. It describes scope the free sources are not expected to fill and contains no live figure; the measured state of each dataset is in the atlas above.
      </p>
      <div className="relative overflow-x-auto">
        <table className="w-full min-w-[44rem] text-xs">
          <caption className="sr-only">Datasets the plan expects to remain missing or uneven</caption>
          <thead>
            <tr className="border-b border-border text-left text-[11px] tracking-[0.08em] text-text-muted uppercase">
              <th scope="col" className="px-2 py-1.5 font-medium">
                Missing or uneven dataset
              </th>
              <th scope="col" className="px-2 py-1.5 font-medium">
                Best free path already available
              </th>
              <th scope="col" className="px-2 py-1.5 font-medium">
                Product consequence
              </th>
            </tr>
          </thead>
          <tbody>
            {PLAN_GAPS.map((g) => (
              <tr key={g.dataset} className="border-b border-border-soft align-top last:border-0">
                <th scope="row" className="max-w-[16rem] px-2 py-1.5 text-left font-medium">
                  {g.dataset}
                </th>
                <td className="px-2 py-1.5 text-text-muted">{g.path}</td>
                <td className="px-2 py-1.5">{g.consequence}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-text-muted">The owner prioritises later free-source additions by affected portfolio value x decision impact x missing-data severity.</p>
    </Blueprint>
  );
}
