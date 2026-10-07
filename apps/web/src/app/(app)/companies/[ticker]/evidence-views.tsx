"use client";

import { useCallback, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Absent, ResourceGate, SourceLink } from "@/components/company/primitives";
import { InsiderTradesSection } from "@/components/reference/insider-trades-section";
import { OwnershipSection, useHoldingOwnership } from "@/components/reference/ownership-section";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatFigure, toNumber, type CapitalAllocation, type Estimates, type Governance, type Network } from "@/lib/company/company";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";

const fig = (v: number | string | null | undefined) => formatFigure(v, "money");
const pct = (v: number | string | null | undefined) => formatFigure(v, "fraction");

function Cell({ text, reason }: { text: string | null; reason?: string }) {
  return <td className="num px-2 py-1 text-right">{text ?? <Absent reason={reason ?? "missing: not filed"} />}</td>;
}

export function AllocationView({ ticker }: { ticker: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/capital-allocation", { params: { path: { ticker } } }), [ticker]);
  const { state } = useApiResource(fetcher);
  return (
    <ResourceGate state={state} label="capital allocation">
      {(d: CapitalAllocation) => (
        <div className="flex flex-col gap-5">
          <Blueprint aria-labelledby="alloc-heading" className="space-y-4 px-[22px] py-5">
            <PanelHeading kicker="Cash flow statement, by fiscal year" title={<span id="alloc-heading">Where the cash went</span>} />
            {d.warnings.map((w) => (
              <AlertMessage key={w} title="Note">{w}</AlertMessage>
            ))}
            {d.years.length === 0 ? (
              <p className="text-sm text-text-muted">No annual cash-flow statements are stored for this company.</p>
            ) : (
              <div className="overflow-x-auto border border-border">
                <table className="w-full border-collapse text-sm">
                  <thead>
                    <tr className="bg-surface-sunken text-right">
                      <th scope="col" className="px-2 py-1.5 text-left font-medium">Year</th>
                      {["Free cash flow", "Capex", "Dividends", "Buybacks", "Debt issued", "Debt repaid", "Net debt change", "Returned / FCF", "Shares", "FCF per share growth"].map((h) => (
                        <th key={h} scope="col" className="px-2 py-1.5 font-medium">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {d.years.map((y) => (
                      <tr key={y.column} className="border-t border-border-soft">
                        <th scope="row" className="px-2 py-1 text-left font-normal">{y.label}</th>
                        <Cell text={fig(y.fcf)} />
                        <Cell text={fig(y.capex)} />
                        <Cell text={fig(y.dividends)} />
                        <Cell text={fig(y.buybacks)} />
                        <Cell text={fig(y.debt_issued)} />
                        <Cell text={fig(y.debt_repaid)} />
                        <Cell text={fig(y.net_debt_change)} />
                        <Cell text={pct(y.returned_share_of_fcf)} reason="n/m: free cash flow is not positive" />
                        <Cell text={fig(y.share_count)} />
                        <Cell text={pct(y.fcf_per_share_growth)} reason="n/m: needs positive free cash flow in both years" />
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="text-sm text-text-muted">{d.observed_note}</p>
          </Blueprint>
          <Blueprint aria-labelledby="stated-heading" className="space-y-3 px-[22px] py-5">
            <PanelHeading kicker="What management said or announced" title={<span id="stated-heading">Stated actions</span>} />
            {d.stated.length === 0 ? (
              <p className="text-sm text-text-muted">No confirmed buyback, dividend or financing announcements are stored.</p>
            ) : (
              <ul className="space-y-1.5 text-sm">
                {d.stated.map((s) => (
                  <li key={`${s.kind}-${s.date}-${s.detail}`} className="flex flex-wrap gap-x-3">
                    <span className="num w-28 shrink-0">{s.date ? formatDate(s.date) : "undated"}</span>
                    <span className="w-24 shrink-0 text-text-muted">{s.kind}</span>
                    <span className="min-w-0 flex-1">{s.detail}{s.state ? <span className="text-text-muted"> ({s.state})</span> : null}</span>
                    <SourceLink href={s.source_url}>source</SourceLink>
                  </li>
                ))}
              </ul>
            )}
          </Blueprint>
        </div>
      )}
    </ResourceGate>
  );
}

export function EstimatesView({ ticker }: { ticker: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/estimates", { params: { path: { ticker } } }), [ticker]);
  const { state } = useApiResource(fetcher);
  return (
    <ResourceGate state={state} label="estimates">
      {(d: Estimates) => (
        <Blueprint aria-labelledby="est-heading" className="space-y-4 px-[22px] py-5">
          <PanelHeading kicker={`Access: ${d.access}`} title={<span id="est-heading">Analyst estimates and company guidance</span>} />
          <p className="max-w-[70ch] text-sm text-text-muted">{d.access_note}</p>
          {d.notes.map((n) => (
            <AlertMessage key={n} title="Note">{n}</AlertMessage>
          ))}
          {d.points.length === 0 ? (
            <p className="text-sm text-text-muted">No estimates or guidance are available. Nothing is projected from the company&apos;s own history here.</p>
          ) : (
            <div className="overflow-x-auto border border-border">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="bg-surface-sunken text-left">
                    {["Kind", "Metric", "Period", "Value", "Low to high", "Analysts", "Provider", "Known at", "Source"].map((h) => (
                      <th key={h} scope="col" className="px-2 py-1.5 font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {d.points.map((p) => (
                    <tr key={`${p.kind}-${p.metric}-${p.fiscal_period}-${p.provider}-${p.vintage}`} className="border-t border-border-soft">
                      <td className="px-2 py-1">{p.kind_label}</td>
                      <td className="px-2 py-1">{p.metric}</td>
                      <td className="px-2 py-1">{p.fiscal_period}</td>
                      <td className="num px-2 py-1 text-right">
                        {fig(p.value) ?? <Absent reason="missing: no value" />} {p.currency ?? ""}
                        {p.surprise !== null && p.surprise !== undefined ? <span className="block text-xs text-text-muted">surprise {pct(p.surprise)}</span> : null}
                      </td>
                      <td className="num px-2 py-1 text-right">{toNumber(p.low) !== null && toNumber(p.high) !== null ? `${fig(p.low)} to ${fig(p.high)}` : ""}</td>
                      <td className="num px-2 py-1 text-right">{p.analyst_count ?? ""}</td>
                      <td className="px-2 py-1">
                        {p.provider ?? "unknown"}
                        {!p.metadata_complete ? <span className="block text-xs text-unconfirmed">basis or vintage missing</span> : null}
                      </td>
                      <td className="px-2 py-1">{formatDate(p.known_at)}</td>
                      <td className="px-2 py-1"><SourceLink href={p.source_url}>source</SourceLink></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="text-xs text-text-muted">Method {d.method_version}.</p>
        </Blueprint>
      )}
    </ResourceGate>
  );
}

export function NetworkView({ ticker }: { ticker: string }) {
  const [since, setSince] = useState("");
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/companies/{ticker}/network", { params: { path: { ticker }, query: since ? { since } : {} } }),
    [ticker, since],
  );
  const { state } = useApiResource(fetcher);
  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="px-[22px] py-4">
        <label className="text-sm">
          <span className="mb-1 block text-xs text-text-muted">Show changes since</span>
          <input type="date" value={since} onChange={(e) => setSince(e.target.value)} className="border border-border bg-surface px-2 py-1.5" />
        </label>
      </Blueprint>
      <ResourceGate state={state} label="the network">
        {(d: Network) => {
          const maxFam = Math.max(...d.patent_by_year.map((y) => y.families), 1);
          return (
            <>
              <Blueprint aria-labelledby="net-edges" className="space-y-3 px-[22px] py-5">
                <PanelHeading kicker={`Since ${formatDate(d.since)}`} title={<span id="net-edges">Confirmed relationships</span>} />
                {d.suggested_count > 0 ? <p className="text-sm text-text-muted">{d.suggested_count} more suggested relationships await review and are not shown here.</p> : null}
                {d.edges.length === 0 ? (
                  <p className="text-sm text-text-muted">No confirmed relationships.</p>
                ) : (
                  <div className="overflow-x-auto border border-border">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="bg-surface-sunken text-left">
                          {["Counterparty", "Type", "Direction", "Evidence", "Valid", "Known at", "Source"].map((h) => (
                            <th key={h} scope="col" className="px-2 py-1.5 font-medium">{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {d.edges.map((e) => (
                          <tr key={`${e.edge_type}-${e.counterparty}-${e.valid_from}`} className="border-t border-border-soft align-top">
                            <td className="px-2 py-1">{e.counterparty}</td>
                            <td className="px-2 py-1">{e.edge_type}</td>
                            <td className="px-2 py-1">{e.direction}</td>
                            <td className="px-2 py-1">{e.evidence_tier}{e.status !== "confirmed" ? <span className="text-unconfirmed"> · {e.status}</span> : null}</td>
                            <td className="px-2 py-1">{e.valid_from ? formatDate(e.valid_from) : "?"} to {e.valid_to ? formatDate(e.valid_to) : "now"}</td>
                            <td className="px-2 py-1">{formatDate(e.known_at)}</td>
                            <td className="px-2 py-1"><SourceLink href={e.source_url}>{e.source}</SourceLink></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Blueprint>
              <Blueprint aria-labelledby="net-events" className="space-y-3 px-[22px] py-5">
                <PanelHeading kicker="Dated" title={<span id="net-events">Corporate events</span>} />
                {d.events.length === 0 ? (
                  <p className="text-sm text-text-muted">No events in this window.</p>
                ) : (
                  <ul className="space-y-1 text-sm">
                    {d.events.map((e) => (
                      <li key={`${e.event_type}-${e.announced_at}-${e.counterparty}`} className="flex flex-wrap gap-x-3">
                        <span className="num w-28 shrink-0">{e.announced_at ? formatDate(e.announced_at) : "undated"}</span>
                        <span className="w-32 shrink-0">{e.event_type}</span>
                        <span className="min-w-0 flex-1">{e.counterparty ?? ""} <span className="text-text-muted">({e.event_state}{e.status !== "confirmed" ? `, ${e.status}` : ""})</span></span>
                        <SourceLink href={e.source_url}>source</SourceLink>
                      </li>
                    ))}
                  </ul>
                )}
                <p className="text-xs text-text-muted">{d.ownership_note}</p>
              </Blueprint>
              <Blueprint aria-labelledby="net-patents" className="space-y-3 px-[22px] py-5">
                <PanelHeading kicker={`${d.patent_total} patent families`} title={<span id="net-patents">Patents</span>} />
                {d.patent_total === 0 ? (
                  <p className="text-sm text-text-muted">No patent families are matched to this company. Absence here is not evidence the company has none: matching depends on the assignee names the app has linked.</p>
                ) : (
                  <div className="grid gap-6 lg:grid-cols-2">
                    <ul className="space-y-1 text-sm" aria-label="Families by filing year">
                      {d.patent_by_year.map((y) => (
                        <li key={y.year} className="grid grid-cols-[3rem_1fr_3rem] items-center gap-2">
                          <span className="num">{y.year}</span>
                          <span className="h-2 bg-border-soft" aria-hidden="true"><span className="block h-full bg-chart-line" style={{ width: `${(y.families / maxFam) * 100}%` }} /></span>
                          <span className="num text-right">{y.families}</span>
                        </li>
                      ))}
                    </ul>
                    <ul className="space-y-1 text-sm" aria-label="Families by technology class">
                      {d.patent_by_tech.map((t) => (
                        <li key={t.cpc} className="flex justify-between gap-3"><span className="font-mono text-xs">{t.cpc}</span><span className="num">{t.families}</span></li>
                      ))}
                    </ul>
                  </div>
                )}
              </Blueprint>
              {d.caveats.length > 0 ? (
                <ul className="space-y-1 text-xs text-text-muted">
                  {d.caveats.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              ) : null}
            </>
          );
        }}
      </ResourceGate>
    </div>
  );
}

export function GovernanceView({ ticker }: { ticker: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/governance", { params: { path: { ticker } } }), [ticker]);
  const { state } = useApiResource(fetcher);
  const ownership = useHoldingOwnership(ticker);
  return (
    <div className="flex flex-col gap-5">
      <ResourceGate state={state} label="governance">
        {(d: Governance) => (
          <>
            <Blueprint aria-labelledby="gov-roles" className="space-y-3 px-[22px] py-5">
              <PanelHeading kicker="From filings, with dates" title={<span id="gov-roles">Executives and tenure</span>} />
              {d.roles.length === 0 ? (
                <p className="text-sm text-text-muted">No dated executive roles are confirmed.</p>
              ) : (
                <div className="overflow-x-auto border border-border">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="bg-surface-sunken text-left">
                        {["Person", "From", "To", "Status", "Source"].map((h) => (
                          <th key={h} scope="col" className="px-2 py-1.5 font-medium">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {d.roles.map((r) => (
                        <tr key={`${r.person}-${r.valid_from}`} className="border-t border-border-soft">
                          <td className="px-2 py-1">{r.person}</td>
                          <td className="px-2 py-1">{r.valid_from ? formatDate(r.valid_from) : <Absent reason="missing: start date not stated" />}</td>
                          <td className="px-2 py-1">{r.valid_to ? formatDate(r.valid_to) : "current or unstated"}</td>
                          <td className="px-2 py-1">{r.status}</td>
                          <td className="px-2 py-1"><SourceLink href={r.source_url}>filing</SourceLink></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Blueprint>
            <Blueprint aria-labelledby="gov-rel" className="space-y-3 px-[22px] py-5">
              <PanelHeading kicker="Disclosed customers and suppliers" title={<span id="gov-rel">Relationship disclosures</span>} />
              {d.related_party.length === 0 ? (
                <p className="text-sm text-text-muted">No confirmed customer or supplier disclosures.</p>
              ) : (
                <ul className="space-y-1 text-sm">
                  {d.related_party.map((r, i) => (
                    <li key={i}>
                      {String(r.relationship)}: {String(r.counterparty_name ?? "undisclosed")}
                      {r.reported_share !== null && r.reported_share !== undefined ? ` (${pct(r.reported_share as string | number)} of the reported base)` : ""}
                      {r.fiscal_period ? `, ${String(r.fiscal_period)}` : ""} <SourceLink href={r.source_url as string | null}>source</SourceLink>
                    </li>
                  ))}
                </ul>
              )}
              {d.caveats.map((c) => (
                <p key={c} className="text-xs text-text-muted">{c}</p>
              ))}
            </Blueprint>
          </>
        )}
      </ResourceGate>
      <InsiderTradesSection ticker={ticker} />
      <OwnershipSection resource={ownership} />
    </div>
  );
}
