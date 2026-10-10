"use client";

import { useSearchParams } from "next/navigation";
import { useMemo } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { asOfQuery, parseAsOf } from "@/lib/xray/as-of";
import { activeBar, pointsLabel, type BenchmarkData, type BenchmarkDim } from "@/lib/xray/xray";

import { Fact, Notes, Pct, TD, TH, TH_LEFT, Toned } from "../shared";
import { FundsAgainstBenchmark } from "./funds-against-benchmark";
import { IndexWeightsPanel } from "./index-weights-panel";

const DIM_TITLE: Record<string, string> = {
  sector: "Sector",
  country: "Country",
  currency: "Trading currency",
  issuer: "Issuer",
};

export function BenchmarkView() {
  const asOf = parseAsOf(useSearchParams().get("as_of"));
  const fetcher = useMemo(
    () => () => getBrowserApiClient().GET("/portfolio/xray/benchmark", { params: { query: asOfQuery(asOf) } }),
    [asOf],
  );
  const { state } = useApiResource(fetcher);
  return (
    <div className="flex flex-col gap-5">
      <ResourceGate state={state} label="benchmark comparison">
        {(data) => <Benchmark data={data} />}
      </ResourceGate>
      <FundsAgainstBenchmark />
      <IndexWeightsPanel />
    </div>
  );
}

/** Active weight as a bar growing left (under) or right (over) from a centre line. */
function DivergingBar({ active, max }: { active: number; max: number }) {
  const bar = activeBar(active, max);
  return (
    <div aria-hidden="true" className="relative mx-auto grid h-2 w-40 grid-cols-2 bg-border-soft">
      <div className="flex justify-end">
        {bar.side === "under" ? <div className="h-full bg-diverging-under" style={{ width: `${bar.width}%` }} /> : null}
      </div>
      <div className="border-l border-border">
        {bar.side === "over" ? <div className="h-full bg-diverging-over" style={{ width: `${bar.width}%` }} /> : null}
      </div>
    </div>
  );
}

function Dimension({ dim, benchmark }: { dim: BenchmarkDim; benchmark: string }) {
  const max = Math.max(...dim.lines.map((l) => Math.abs(l.active)), 0);
  const title = DIM_TITLE[dim.dimension] ?? dim.dimension;
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading
        kicker={dim.like_for_like ? "Compared like for like" : "Not compared"}
        title={title}
        action={
          dim.like_for_like && (dim.portfolio_unlabelled > 0 || dim.benchmark_unlabelled > 0) ? (
            <span className="num text-xs text-text-muted">
              unlabelled: portfolio {formatPercent(dim.portfolio_unlabelled, { decimals: 1 })}, benchmark {formatPercent(dim.benchmark_unlabelled, { decimals: 1 })}
            </span>
          ) : undefined
        }
      />
      {!dim.like_for_like ? (
        <div className="mt-3">
          <AlertMessage title="Withheld: the labels are not the same thing">{dim.reason}</AlertMessage>
        </div>
      ) : (
        <>
          {dim.notes.map((n) => (
            <p key={n} className="mt-2 text-xs text-text-muted">{n}</p>
          ))}
          <div className="mt-3 overflow-x-auto border border-border">
            <table className="w-full text-sm">
              <caption className="sr-only">{title}: portfolio equity against {benchmark}</caption>
              <thead>
                <tr className="bg-surface-muted">
                  <th scope="col" className={TH_LEFT}>{title}</th>
                  <th scope="col" className={TH}>Portfolio equity</th>
                  <th scope="col" className={TH}>{benchmark}</th>
                  <th scope="col" className="px-3 py-1.5 text-center font-medium">Underweight · overweight</th>
                  <th scope="col" className={TH}>Difference</th>
                </tr>
              </thead>
              <tbody>
                {dim.lines.map((l) => (
                  <tr key={l.key} className="border-t border-border-soft">
                    <th scope="row" className="px-3 py-1.5 text-left font-normal">{l.label}</th>
                    <td className={TD}>{formatPercent(l.portfolio_weight, { decimals: 1 })}</td>
                    <td className={TD}>{formatPercent(l.benchmark_weight, { decimals: 1 })}</td>
                    <td className="px-3 py-1.5"><DivergingBar active={l.active} max={max} /></td>
                    <td className={TD}><Toned value={l.active}>{pointsLabel(l.active)}</Toned></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Blueprint>
  );
}

function Missing({ title, note, items, weight }: { title: string; note: string; items: BenchmarkData["portfolio_only"]; weight: number }) {
  return (
    <div>
      <p className="text-sm font-medium">{title}</p>
      <p className="text-xs text-text-muted">{note}</p>
      <p className="num mt-1 text-lg font-semibold">{formatPercent(weight, { decimals: 1 })}</p>
      {items.length === 0 ? (
        <p className="mt-1 text-sm text-text-muted">None.</p>
      ) : (
        <ul className="mt-1 space-y-0.5 text-sm">
          {items.map((i) => (
            <li key={i.key} className="flex justify-between gap-3">
              <span>{i.label}</span>
              <span className="num text-text-muted">{formatPercent(i.weight, { decimals: 1 })}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Benchmark({ data }: { data: BenchmarkData }) {
  if (!data.available) {
    return (
      <Blueprint className="px-[22px] py-4">
        <PanelHeading kicker="Against a benchmark" title="No comparison available" />
        <div className="mt-3">
          <AlertMessage title="Why">{data.reason ?? data.definition}</AlertMessage>
        </div>
        <p className="mt-3 max-w-2xl text-sm text-text-muted">{data.definition}</p>
      </Blueprint>
    );
  }
  const label = data.label ?? "Benchmark";
  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="px-[22px] py-4">
        <PanelHeading kicker="The benchmark is a proxy" title={`${label}, as ${data.proxy_fund} holds it`} />
        <p className="mt-1 max-w-3xl text-sm text-text-muted">{data.definition}</p>
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-3 md:grid-cols-4">
          <Fact label="Proxy holdings as of" note={data.age_days !== null && data.age_days !== undefined ? `${data.age_days} days old` : undefined}>
            {data.as_of ? <span className="num">{formatDate(data.as_of)}</span> : "—"}
          </Fact>
          <Fact label="Compared" note="Portfolio equity, renormalised to 100%">
            <Pct value={data.equity_weight} />
          </Fact>
          <Fact label="Outside the comparison" note="Cash, other assets, unknown fund weight">
            <Pct value={data.outside_weight} />
          </Fact>
          <Fact label="Issuer overlap" note="Sum of the smaller weight per shared issuer">
            <Pct value={data.overlap} />
          </Fact>
        </dl>
        <div className="mt-4 grid gap-6 md:grid-cols-2">
          <Missing
            title="You hold, the proxy does not"
            note="Share of your equity in issuers absent from the proxy"
            items={data.portfolio_only}
            weight={data.portfolio_only_weight}
          />
          <Missing
            title="The proxy holds, you do not"
            note="Share of the proxy in issuers you do not hold"
            items={data.benchmark_only}
            weight={data.benchmark_only_weight}
          />
        </div>
        {data.proxy_unidentified_weight > 0 ? (
          <p className="mt-3 text-sm text-text-muted">
            {formatPercent(data.proxy_unidentified_weight, { decimals: 1 })} of the proxy has neither an ISIN nor a name, so it cannot be matched to an
            issuer and counts as missing constituents.
          </p>
        ) : null}
        {data.proxy_rows_weight !== null && data.proxy_rows_weight !== undefined && Math.abs(data.proxy_rows_weight - 1) > 0.02 ? (
          <p className="mt-1 text-sm text-text-muted">
            The proxy fund&apos;s published rows add up to {formatPercent(data.proxy_rows_weight, { decimals: 1 })} of its net assets.
          </p>
        ) : null}
      </Blueprint>
      {data.dimensions.map((d) => (
        <Dimension key={d.dimension} dim={d} benchmark={label} />
      ))}
      <Notes notes={data.notes} />
    </div>
  );
}
