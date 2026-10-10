"use client";

import { useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { asOfQuery, parseAsOf } from "@/lib/xray/as-of";
import { pointsLabel, type IntersectionData } from "@/lib/xray/xray";

import { Fact, Notes, Pct, TD, TH, TH_LEFT } from "../shared";

export function OverlapView() {
  const asOf = parseAsOf(useSearchParams().get("as_of"));
  const fetcher = useMemo(
    () => () => getBrowserApiClient().GET("/portfolio/xray/overlap", { params: { query: asOfQuery(asOf) } }),
    [asOf],
  );
  const { state } = useApiResource(fetcher);
  return (
    <ResourceGate state={state} label="overlap">
      {(data) => <Overlap data={data} />}
    </ResourceGate>
  );
}

type Issuer = IntersectionData["issuers"][number];

function IssuerRow({ issuer }: { issuer: Issuer }) {
  const shared = issuer.channels.length > 1;
  return (
    <li className="border-t border-border-soft first:border-t-0">
      <details className="group">
        <summary className="grid cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 gap-y-0.5 px-3 py-2 sm:grid-cols-[minmax(0,2fr)_6rem_6rem_6rem_7rem]">
          <span className="min-w-0">
            <span className="font-medium">{issuer.label}</span>
            {issuer.merged_securities > 1 ? (
              <span className="ml-2 text-xs text-text-muted">{issuer.merged_securities} securities merged</span>
            ) : null}
            {shared ? <span className="ml-2 border border-border px-1 text-[11px] text-text-muted">{issuer.channels.length} channels</span> : null}
          </span>
          <span className="num text-right">{formatPercent(issuer.weight, { decimals: 2 })}</span>
          <span className="num hidden text-right text-text-muted sm:block">
            <span className="sr-only">Direct </span>
            {formatPercent(issuer.direct_weight, { decimals: 2 })}
          </span>
          <span className="num hidden text-right text-text-muted sm:block">
            <span className="sr-only">Through funds </span>
            {formatPercent(issuer.fund_weight, { decimals: 2 })}
          </span>
          <span className="num hidden text-right sm:block">
            <span className="sr-only">Overlap </span>
            {issuer.overlap_weight > 0 ? formatPercent(issuer.overlap_weight, { decimals: 2 }) : <span className="text-text-faint">—</span>}
          </span>
        </summary>
        <div className="space-y-3 border-t border-border-soft bg-surface-muted/50 px-3 py-3 text-sm">
          <div>
            <p className="text-xs font-medium text-text-muted">Where it comes from</p>
            <ul className="mt-1 space-y-0.5">
              {issuer.channels.map((c) => (
                <li key={`${c.kind}-${c.isin ?? c.ticker ?? c.label}`} className="flex justify-between gap-3">
                  <span>{c.kind === "direct" ? "Held directly" : `Through ${c.label}`}</span>
                  <span className="num">{formatPercent(c.weight, { decimals: 2 })}</span>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <p className="text-xs font-medium text-text-muted">The securities behind it</p>
            <ul className="mt-1 space-y-2">
              {issuer.securities.map((s) => (
                <li key={s.isin ?? s.name}>
                  <p>
                    {s.name}
                    <span className="num ml-2 text-xs text-text-muted">{[s.isin, s.ticker].filter(Boolean).join(" · ")}</span>
                    <span className="num ml-2">{formatPercent(s.weight, { decimals: 2 })}</span>
                  </p>
                  <ul className="ml-4 mt-0.5 text-xs text-text-muted">
                    {s.via.map((v) => (
                      <li key={`${v.kind}-${v.label}`}>
                        {v.kind === "direct" ? "held directly" : `in ${v.label}`}: <span className="num">{formatPercent(v.weight, { decimals: 2 })}</span>
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          </div>
          <p className="text-xs text-text-muted">
            {[issuer.sector, issuer.country, issuer.currency].filter(Boolean).join(" · ") || "No sector, country or currency label"}
          </p>
        </div>
      </details>
    </li>
  );
}

function Removal({ data }: { data: IntersectionData }) {
  const [isin, setIsin] = useState(data.removal[0]?.isin ?? "");
  const removal = data.removal.find((r) => r.isin === isin);
  if (data.removal.length === 0) return null;
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker="If you sold a fund" title="What its removal does to concentration" />
      <p className="mt-1 max-w-3xl text-sm text-text-muted">
        The fund&apos;s holdings leave the portfolio and the proceeds sit in cash; nothing else changes. Measured on the issuers the app can identify.
      </p>
      <div className="mt-3">
        <label className="text-sm" htmlFor="removal-fund">
          Fund{" "}
        </label>
        <select
          id="removal-fund"
          value={isin}
          onChange={(e) => setIsin(e.target.value)}
          className="h-9 border border-border bg-surface px-2 text-sm"
        >
          {data.removal.map((r) => (
            <option key={r.isin} value={r.isin}>
              {r.label} ({formatPercent(r.weight, { decimals: 1 })})
            </option>
          ))}
        </select>
      </div>
      {removal ? (
        <div className="mt-4 grid gap-5 lg:grid-cols-2">
          <table className="w-full text-sm">
            <caption className="sr-only">Issuer concentration before and after removing {removal.label}</caption>
            <thead>
              <tr className="border-b border-border text-xs text-text-muted">
                <th scope="col" className={TH_LEFT}>Issuer concentration</th>
                <th scope="col" className={TH}>Now</th>
                <th scope="col" className={TH}>Without {removal.label}</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-b border-border-soft">
                <th scope="row" className="px-3 py-1.5 text-left font-normal">Effective number of issuers</th>
                <td className={TD}>{removal.effective_before?.toFixed(1) ?? "—"}</td>
                <td className={TD}>{removal.effective_after?.toFixed(1) ?? "—"}</td>
              </tr>
              <tr className="border-b border-border-soft">
                <th scope="row" className="px-3 py-1.5 text-left font-normal">HHI</th>
                <td className={TD}>{removal.hhi_before?.toFixed(3) ?? "—"}</td>
                <td className={TD}>{removal.hhi_after?.toFixed(3) ?? "—"}</td>
              </tr>
              <tr className="border-b border-border-soft">
                <th scope="row" className="px-3 py-1.5 text-left font-normal">Ten largest issuers</th>
                <td className={TD}>{formatPercent(removal.top10_before, { decimals: 1 })}</td>
                <td className={TD}>{formatPercent(removal.top10_after, { decimals: 1 })}</td>
              </tr>
              <tr className="border-b border-border-soft">
                <th scope="row" className="px-3 py-1.5 text-left font-normal">Largest issuer</th>
                <td className="px-3 py-1.5 text-right text-sm">
                  {removal.largest_before ? `${removal.largest_before.label} ${formatPercent(removal.largest_before.after, { decimals: 1 })}` : "—"}
                </td>
                <td className="px-3 py-1.5 text-right text-sm">
                  {removal.largest_after ? `${removal.largest_after.label} ${formatPercent(removal.largest_after.after, { decimals: 1 })}` : "—"}
                </td>
              </tr>
              <tr>
                <th scope="row" className="px-3 py-1.5 text-left font-normal">Issuers still in 2+ channels</th>
                <td className={TD}>{data.shared_count}</td>
                <td className={TD}>{removal.shared_after}</td>
              </tr>
            </tbody>
          </table>
          <div>
            <p className="text-xs font-medium text-text-muted">
              Largest drops ({removal.issuers_touched} issuers touched
              {removal.unknown_removed > 0 ? `, ${formatPercent(removal.unknown_removed, { decimals: 1 })} unknown weight leaves with it` : ""})
            </p>
            {removal.biggest_drops.length === 0 ? (
              <p className="mt-2 text-sm text-text-muted">Nothing the app can identify is held through this fund, so no issuer changes.</p>
            ) : (
              <ul className="mt-2 space-y-1 text-sm">
                {removal.biggest_drops.map((d) => (
                  <li key={d.key} className="flex justify-between gap-3">
                    <span>{d.label}</span>
                    <span className="num">
                      {formatPercent(d.before, { decimals: 2 })} → {formatPercent(d.after, { decimals: 2 })}
                      <span className="ml-2 text-loss">{pointsLabel(d.after - d.before, 2)}</span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      ) : null}
    </Blueprint>
  );
}

function Overlap({ data }: { data: IntersectionData }) {
  const [sharedOnly, setSharedOnly] = useState(false);
  const rows = sharedOnly ? data.issuers.filter((i) => i.channels.length > 1) : data.issuers;
  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="px-[22px] py-4">
        <PanelHeading kicker="Stock intersection" title="The same company reached more than once" />
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-3 md:grid-cols-4">
          <Fact label="Issuers identified">
            <span className="num">{data.issuer_count}</span>
          </Fact>
          <Fact label="Reached through 2+ channels" note="Direct plus a fund, or several funds">
            <span className="num">{data.shared_count}</span>
          </Fact>
          <Fact label="Combined weight of those" note="Their whole exposure, all channels">
            <Pct value={data.shared_weight} />
          </Fact>
          <Fact label="Redundant exposure" note="Beyond the largest single channel">
            <Pct value={data.redundant_weight} />
          </Fact>
        </dl>
        {data.unknown && data.unknown.unknown_fund_weight > 0 ? (
          <p className="mt-3 text-sm text-text-muted">
            <span className="num text-text">{formatPercent(data.unknown.unknown_fund_weight, { decimals: 1 })}</span> of the portfolio is unknown fund
            weight. It stays its own bucket and is not counted as overlap or as any issuer.
          </p>
        ) : null}
      </Blueprint>

      <Blueprint className="px-[22px] py-4">
        <PanelHeading
          kicker="By issuer"
          title="Combined weight and where each part comes from"
          action={
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={sharedOnly} onChange={(e) => setSharedOnly(e.target.checked)} className="size-4 accent-[var(--accent)]" />
              Shared issuers only
            </label>
          }
        />
        <div className="mt-3 border border-border">
          <div
            aria-hidden="true"
            className="hidden grid-cols-[minmax(0,2fr)_6rem_6rem_6rem_7rem] gap-x-4 bg-surface-muted px-3 py-1.5 text-xs font-medium text-text-muted sm:grid"
          >
            <span>Issuer</span>
            <span className="text-right">Combined</span>
            <span className="text-right">Direct</span>
            <span className="text-right">Funds</span>
            <span className="text-right">Overlap</span>
          </div>
          <ul className="m-0 list-none p-0">
            {rows.map((issuer) => (
              <IssuerRow key={issuer.key} issuer={issuer} />
            ))}
          </ul>
          {rows.length === 0 ? <p className="px-3 py-3 text-sm text-text-muted">No issuer is reached through more than one channel.</p> : null}
        </div>
      </Blueprint>

      <Removal data={data} />
      <Notes notes={data.notes} />
    </div>
  );
}
