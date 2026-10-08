"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMemo } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { asOfQuery, parseAsOf } from "@/lib/xray/as-of";
import type { IndexComparisonData, IndexComparisonsData } from "@/lib/xray/owner-input";

import { Fact, Notes, TD, TH, TH_LEFT } from "../shared";

/** Active share against index weights you entered. They are yours, with the source and date you
 * gave, not an official feed, and the panel says so beside every figure. */
export function IndexWeightsPanel() {
  const asOf = parseAsOf(useSearchParams().get("as_of"));
  const fetcher = useMemo(
    () => () => getBrowserApiClient().GET("/portfolio/xray/index-weights", { params: { query: asOfQuery(asOf) } }),
    [asOf],
  );
  const { state } = useApiResource(fetcher);
  return (
    <ResourceGate state={state} label="index weights">
      {(data) => <Comparisons data={data} />}
    </ResourceGate>
  );
}

function DifferenceTable({ title, rows }: { title: string; rows: IndexComparisonData["overweights"] }) {
  return (
    <div className="overflow-x-auto border border-border">
      <table className="w-full text-sm">
        <caption className="sr-only">{title}</caption>
        <thead>
          <tr className="bg-surface-muted">
            <th scope="col" className={TH_LEFT}>{title}</th>
            <th scope="col" className={TH}>Portfolio</th>
            <th scope="col" className={TH}>Index</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((d) => (
            <tr key={d.name} className="border-t border-border-soft">
              <th scope="row" className="px-3 py-1.5 text-left font-normal">{d.name}</th>
              <td className={TD}>{formatPercent(d.portfolio_weight, { decimals: 1 })}</td>
              <td className={TD}>{formatPercent(d.index_weight, { decimals: 1 })}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Comparison({ c }: { c: IndexComparisonData }) {
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker="Weights you entered" title={`Active share against ${c.label}`} />
      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-3 md:grid-cols-4">
        <Fact label={c.partial ? "Active share, approximate" : "Active share"} note="Half the summed weight differences, on equity">
          {c.active_share === null || c.active_share === undefined ? "—" : <span className="num">{formatPercent(c.active_share, { decimals: 1 })}</span>}
        </Fact>
        <Fact label="Index weight you hold" note="In names the portfolio also holds">
          <span className="num">{formatPercent(c.index_weight_held, { decimals: 1 })}</span>
        </Fact>
        <Fact label="Weights entered" note={`${c.constituents} names, ${c.unmatched} matched no holding`}>
          <span className="num">{formatPercent(c.index_weight_listed, { decimals: 1 })}</span>
        </Fact>
        <Fact label="Source and date" note={`as of ${formatDate(c.as_of)}`}>
          <a href={c.source_url} target="_blank" rel="noreferrer" className="text-sm text-accent-ink underline underline-offset-2">
            Open the source
          </a>
        </Fact>
      </dl>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <DifferenceTable title="You hold more than the index" rows={c.overweights} />
        <DifferenceTable title="You hold less than the index" rows={c.underweights} />
      </div>
      <Notes notes={c.notes} />
    </Blueprint>
  );
}

function Comparisons({ data }: { data: IndexComparisonsData }) {
  if (data.comparisons.length === 0) {
    return (
      <Blueprint className="px-[22px] py-4">
        <PanelHeading kicker="Index weights" title="Active share against an index" />
        <p className="mt-2 max-w-3xl text-sm text-text-muted">
          No provider here supplies an index&apos;s constituents. Paste them from the index provider&apos;s factsheet, with its address and date, and the
          portfolio is compared with them.{" "}
          <Link href="/xray/inputs" className="text-accent-ink underline underline-offset-2">
            Enter index weights
          </Link>
        </p>
      </Blueprint>
    );
  }
  return (
    <>
      {data.comparisons.map((c) => (
        <Comparison key={c.label} c={c} />
      ))}
    </>
  );
}
