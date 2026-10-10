"use client";

import { useSearchParams } from "next/navigation";
import { useMemo } from "react";

import { Absent, ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { asOfQuery, parseAsOf } from "@/lib/xray/as-of";
import type { FundRelativeData } from "@/lib/xray/owner-input";

import { Notes, TD, TH, TH_LEFT, Toned } from "../shared";

/** Each fund you hold against the benchmark you recorded for it, so a fund that strays from its
 * index shows beside the portfolio comparison above. Only same-basis, same-currency differences
 * get a number; every other fund says why not. */
export function FundsAgainstBenchmark() {
  const asOf = parseAsOf(useSearchParams().get("as_of"));
  const fetcher = useMemo(
    () => () => getBrowserApiClient().GET("/portfolio/xray/funds-vs-benchmark", { params: { query: asOfQuery(asOf) } }),
    [asOf],
  );
  const { state } = useApiResource(fetcher);
  return (
    <ResourceGate state={state} label="funds against their benchmarks">
      {(data) => <Funds data={data} />}
    </ResourceGate>
  );
}

function Funds({ data }: { data: FundRelativeData }) {
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading
        kicker="Fund by fund"
        title="How far each fund strays from its own benchmark"
        action={
          data.average !== null && data.average !== undefined ? (
            <span className="num text-xs text-text-muted">
              weighted average {formatPercent(data.average, { decimals: 2 })} a year over {formatPercent(data.covered_weight, { decimals: 1 })} of the portfolio
            </span>
          ) : undefined
        }
      />
      {data.rows.length === 0 ? (
        <p className="mt-3 text-sm text-text-muted">You hold no funds.</p>
      ) : (
        <div className="mt-3 overflow-x-auto border border-border">
          <table className="w-full text-sm">
            <caption className="sr-only">Tracking difference of each fund against the benchmark you recorded for it</caption>
            <thead>
              <tr className="bg-surface-muted">
                <th scope="col" className={TH_LEFT}>Fund</th>
                <th scope="col" className={TH}>Portfolio weight</th>
                <th scope="col" className={TH_LEFT}>Benchmark you recorded</th>
                <th scope="col" className={TH}>Tracking difference, a year</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.isin} className="border-t border-border-soft">
                  <th scope="row" className="px-3 py-1.5 text-left font-normal">
                    {r.name ?? r.ticker ?? r.isin}
                    {r.is_proxy ? <span className="ml-2 text-xs text-text-muted">benchmark proxy</span> : null}
                  </th>
                  <td className={TD}>{r.weight === null || r.weight === undefined ? "—" : formatPercent(r.weight, { decimals: 1 })}</td>
                  <td className="px-3 py-1.5">{r.benchmark ?? <span className="text-text-muted">none recorded</span>}</td>
                  <td className={TD}>
                    {r.tracking_difference === null || r.tracking_difference === undefined ? (
                      <Absent reason={`missing: ${r.reason ?? "no figure"}`} />
                    ) : (
                      <Toned value={r.tracking_difference}>{formatPercent(r.tracking_difference, { decimals: 2 })}</Toned>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Notes notes={data.notes} />
    </Blueprint>
  );
}
