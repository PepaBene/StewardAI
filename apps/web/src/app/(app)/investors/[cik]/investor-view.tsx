"use client";

import Link from "next/link";
import { useCallback, useMemo } from "react";

import { AlertMessage } from "@/components/alert-message";
import { DivergingBar, Treemap } from "@/components/charts";
import { EntityName } from "@/components/entity-name";
import { PageHeader } from "@/components/page-header";
import { getBrowserApiClient } from "@/lib/api/client";
import { useEntityNames } from "@/lib/entities/use-entity-names";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { changeWords, overlapWithHoldings, positionMoves, signedPercent, toNumber, valueChange } from "@/lib/investors/investors";

function MoveList({ title, items, empty }: { title: string; items: { key: string; name: string; value: string }[]; empty: string }) {
  return (
    <div className="rounded-card bg-surface-muted px-4 py-3">
      <h3 className="text-sm font-medium">{title}</h3>
      {items.length === 0 ? (
        <p className="mt-1 text-sm text-text-muted">{empty}</p>
      ) : (
        <ul className="mt-1.5 space-y-1 text-sm">
          {items.map((i) => (
            <li key={i.key} className="flex justify-between gap-3">
              <span className="truncate">{i.name}</span>
              <span className="num shrink-0 text-text-muted">{i.value}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function money(value: number | string | null | undefined, compact = false) {
  return value == null ? "—" : new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", notation: compact ? "compact" : "standard", maximumFractionDigits: compact ? 1 : 0 }).format(Number(value));
}

const TOP = 30;

export function InvestorView({ cik }: { cik: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/investors/{cik}", { params: { path: { cik } } }), [cik]);
  const { state } = useApiResource(fetcher);
  const names = useEntityNames();
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;

  const positions = useMemo(
    () => (data?.positions_detail ?? []).filter((p) => (toNumber(p.value_usd) ?? 0) > 0).sort((a, b) => (toNumber(b.value_usd) ?? 0) - (toNumber(a.value_usd) ?? 0)),
    [data],
  );
  const total = toNumber(data?.reported_value_usd) ?? positions.reduce((s, p) => s + (toNumber(p.value_usd) ?? 0), 0);
  const tiles = useMemo(
    () =>
      positions.slice(0, TOP).map((p) => ({
        id: `${p.cusip}:${p.title_of_class}`,
        label: p.issuer_name,
        weight: total > 0 ? (toNumber(p.value_usd) ?? 0) / total : 0,
        change: null,
      })),
    [positions, total],
  );
  const overlap = useMemo(() => overlapWithHoldings(positions, names, total), [positions, names, total]);
  const change = data ? valueChange(data.reported_value_usd, data.previous_value_usd) : null;
  const moves = useMemo(() => positionMoves(positions), [positions]);

  return (
    <div className="space-y-5">
      <Link href="/investors" className="text-sm text-accent-ink hover:underline">
        All 13F investors
      </Link>
      {data ? (
        <PageHeader title={data.name} description={`Quarter ended ${data.period ? formatDate(data.period) : "unknown"}, filed ${data.filed ? formatDate(data.filed) : "unknown"}.`} />
      ) : (
        <PageHeader title="13F manager" description="Loading the manager's filing…" />
      )}
      {state.kind === "error" ? <AlertMessage title="Could not load this manager">{state.detail}</AlertMessage> : null}
      {data ? (
        <>
          <dl className="blueprint grid gap-4 px-5 py-4 sm:grid-cols-3">
            <div>
              <dt className="text-xs text-text-muted">Value in your holdings</dt>
              <dd className="num font-heading mt-1 text-2xl">{money(data.reported_value_usd, true)}</dd>
            </div>
            <div>
              <dt className="text-xs text-text-muted">Your holdings owned</dt>
              <dd className="num font-heading mt-1 text-2xl">{data.positions}</dd>
            </div>
            <div>
              <dt className="text-xs text-text-muted">Against the previous quarter</dt>
              <dd className="num font-heading mt-1 text-2xl">{signedPercent(change)}</dd>
            </div>
          </dl>

          <section aria-labelledby="map-heading" className="blueprint space-y-3 px-5 py-4">
            <h2 id="map-heading" className="font-heading text-lg leading-tight font-semibold">
              What the manager holds
            </h2>
            {tiles.length > 0 ? (
              <>
                <Treemap items={tiles} scale={false} label={`Largest ${tiles.length} reported positions, sized by share of reported value`} formatWeight={(w) => `${(w * 100).toFixed(1)}% of reported value`} />
                <p className="text-xs text-text-muted">Tile size is the share of the manager&rsquo;s reported value. The largest {tiles.length} positions are shown.</p>
              </>
            ) : (
              <p className="text-sm text-text-muted">No stock positions are loaded for this manager yet. Load new 13F filings from the investors page.</p>
            )}
          </section>

          <section aria-labelledby="delta-heading" className="blueprint space-y-3 px-5 py-4">
            <h2 id="delta-heading" className="font-heading text-lg leading-tight font-semibold">
              Change since the previous quarter
            </h2>
            {data.earlier_period ? (
              <>
                <p className="text-sm text-text-muted">
                  {moves.sentence} Compared with the quarter ended {formatDate(data.earlier_period)}
                  {change !== null ? `; total reported value ${signedPercent(change)}.` : "."}
                </p>
                {moves.bars.length > 0 ? (
                  <DivergingBar
                    rows={moves.bars}
                    colorMode="direction"
                    label="Change in shares held, largest moves among the top positions"
                    formatValue={(v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(0)}%`}
                  />
                ) : null}
                <div className="grid gap-4 sm:grid-cols-2">
                  <MoveList title="New positions" items={moves.opened.map((p) => ({ key: p.cusip, name: p.issuer_name, value: money(p.value_usd, true) }))} empty="No new position among the largest 50." />
                  <MoveList
                    title="Sold out"
                    items={(data.sold_out ?? []).map((p) => ({ key: p.cusip, name: p.issuer_name, value: `was ${money(p.previous_value_usd, true)}` }))}
                    empty="Nothing held last quarter was sold out."
                  />
                </div>
              </>
            ) : (
              <p className="text-sm text-text-muted">There is no earlier quarter loaded to compare with, so position changes appear after the next filing.</p>
            )}
            <p className="text-xs text-text-muted">Changes are counted in shares, so a price move alone never reads as buying or selling. {data.performance_note}</p>
          </section>

          <section aria-labelledby="overlap-heading" className="blueprint space-y-3 px-5 py-4">
            <h2 id="overlap-heading" className="font-heading text-lg leading-tight font-semibold">
              Overlap with your holdings
            </h2>
            {overlap.length > 0 ? (
              <ul className="m-0 list-none divide-y divide-border p-0 text-sm" aria-label="Positions this manager shares with you">
                {overlap.map((o) => (
                  <li key={o.ticker} className="flex items-center justify-between gap-3 py-2">
                    <EntityName id={o.ticker} />
                    <span className="num text-text-muted">
                      {money(o.position.value_usd, true)}
                      {o.share !== null ? `, ${(o.share * 100).toFixed(1)}% of their book` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-text-muted">None of this manager&rsquo;s reported positions match a company you hold, by name.</p>
            )}
          </section>

          <section aria-labelledby="table-heading" className="space-y-2">
            <h2 id="table-heading" className="font-heading text-lg leading-tight font-semibold">
              Largest reported positions
            </h2>
            {positions.length > 0 ? (
              <div className="overflow-x-auto rounded-xl border border-border bg-surface-raised">
                <table className="w-full min-w-[32rem] text-sm">
                  <caption className="sr-only">Largest positions in the latest 13F</caption>
                  <thead>
                    <tr className="border-b border-border text-xs text-text-muted">
                      <th scope="col" className="px-3 py-2 text-left font-medium">
                        Company
                      </th>
                      <th scope="col" className="px-3 py-2 text-left font-medium">
                        Class
                      </th>
                      <th scope="col" className="px-3 py-2 text-right font-medium">
                        Shares
                      </th>
                      <th scope="col" className="px-3 py-2 text-right font-medium">
                        Reported value
                      </th>
                      <th scope="col" className="px-3 py-2 text-right font-medium">
                        Shares since last quarter
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {positions.slice(0, 50).map((row) => (
                      <tr key={`${row.cusip}:${row.title_of_class}`} className="border-b border-border last:border-0">
                        <th scope="row" className="px-3 py-2 text-left font-medium">
                          {row.issuer_name}
                        </th>
                        <td className="px-3 py-2">{row.title_of_class}</td>
                        <td className="num px-3 py-2 text-right">{row.shares == null ? "—" : Number(row.shares).toLocaleString()}</td>
                        <td className="num px-3 py-2 text-right">{money(row.value_usd)}</td>
                        <td className="num px-3 py-2 text-right">{changeWords(row.change, row.share_change_pct)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </section>
          <footer className="text-xs text-text-muted">13F filings list selected US securities at quarter end and may be filed up to 45 days later. Values are not returns.</footer>
        </>
      ) : null}
    </div>
  );
}
