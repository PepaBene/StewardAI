"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { Absent, ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { asOfQuery, parseAsOf } from "@/lib/xray/as-of";
import { GRAPH, layoutGraph, type SupplyChainData } from "@/lib/xray/owner-input";

import { Notes, TD, TH, TH_LEFT } from "../shared";
import { EdgeForm } from "./edge-form";

export function SupplyChainView() {
  const asOf = parseAsOf(useSearchParams().get("as_of"));
  const [version, setVersion] = useState(0);
  const fetcher = useMemo(
    // `version` makes the load run again after a link is added or removed
    () => () => getBrowserApiClient().GET("/portfolio/xray/supply-chain", { params: { query: asOfQuery(asOf) } }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [asOf, version],
  );
  const { state } = useApiResource(fetcher);
  return (
    <div className="flex flex-col gap-5">
      <ResourceGate state={state} label="supply chain">
        {(data) => <Graph data={data} onChanged={() => setVersion((v) => v + 1)} />}
      </ResourceGate>
      <EdgeForm onSaved={() => setVersion((v) => v + 1)} />
    </div>
  );
}

function Diagram({ data }: { data: SupplyChainData }) {
  const { positions, width, height } = layoutGraph(data.nodes);
  return (
    <div className="overflow-x-auto border border-border bg-surface">
      <svg role="img" aria-label="Supply chain: suppliers on the left, their customers to the right" width={width} height={height} className="block">
        <defs>
          <marker id="supply-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0 0 L8 4 L0 8 z" className="fill-text-muted" />
          </marker>
        </defs>
        {data.edges.map((e) => {
          const a = positions.get(e.supplier);
          const b = positions.get(e.customer);
          if (!a || !b) return null;
          const x1 = a.x + GRAPH.nodeW;
          const y1 = a.y + GRAPH.nodeH / 2;
          const x2 = b.x;
          const y2 = b.y + GRAPH.nodeH / 2;
          const mid = (x1 + x2) / 2;
          return (
            <path
              key={`${e.supplier}>${e.customer}`}
              d={`M${x1} ${y1} C${mid} ${y1} ${mid} ${y2} ${x2} ${y2}`}
              className="fill-none stroke-text-muted"
              strokeWidth={e.customer_share ? 1 + Math.min(e.customer_share, 1) * 4 : 1}
              markerEnd="url(#supply-arrow)"
            >
              <title>{`${e.supplier} supplies ${e.customer}: ${e.product}`}</title>
            </path>
          );
        })}
        {data.nodes.map((n) => {
          const p = positions.get(n.symbol);
          if (!p) return null;
          return (
            <g key={n.symbol} transform={`translate(${p.x} ${p.y})`}>
              <rect
                width={GRAPH.nodeW}
                height={GRAPH.nodeH}
                className={n.held ? "fill-surface-muted stroke-accent" : "fill-surface stroke-border"}
                strokeWidth={n.held ? 2 : 1}
              />
              <text x={8} y={16} className="fill-text text-[12px] font-semibold">{n.symbol}</text>
              <text x={8} y={31} className="fill-text-muted text-[11px]">
                {n.held ? `${(n.weight * 100).toFixed(1)}% of portfolio` : "not held"}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function Graph({ data, onChanged }: { data: SupplyChainData; onChanged: () => void }) {
  const [problem, setProblem] = useState<string | null>(null);
  async function remove(id: number) {
    const res = await getBrowserApiClient().DELETE("/portfolio/xray/inputs/{input_id}", { params: { path: { input_id: id } } });
    if (!res.response.ok) return setProblem("That link could not be removed.");
    setProblem(null);
    onChanged();
  }
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading
        kicker="From annual reports and your entries"
        title="Who supplies whom among the companies you follow"
        action={
          data.edges.length > 0 ? (
            <span className="num text-xs text-text-muted">{formatPercent(data.held_in_graph, { decimals: 1 })} of the portfolio is in these companies</span>
          ) : undefined
        }
      />
      {data.edges.length === 0 ? (
        <p className="mt-3 max-w-2xl text-sm text-text-muted">
          Nothing to draw yet. Links come from customers and suppliers named in the annual reports of companies you hold, or from ones you add below with where you read them. Held companies are outlined, and a link where you own both ends is listed under the picture.
        </p>
      ) : (
        <div className="mt-3 flex flex-col gap-4">
          <Diagram data={data} />
          {data.linked.length > 0 ? (
            <div className="overflow-x-auto border border-border">
              <table className="w-full text-sm">
                <caption className="sr-only">Links where the portfolio holds both the supplier and the customer</caption>
                <thead>
                  <tr className="bg-surface-muted">
                    <th scope="col" className={TH_LEFT}>You hold both ends</th>
                    <th scope="col" className={TH_LEFT}>What is supplied</th>
                    <th scope="col" className={TH}>Customer&apos;s share of supplier revenue</th>
                    <th scope="col" className={TH}>Held together</th>
                  </tr>
                </thead>
                <tbody>
                  {data.linked.map((l) => (
                    <tr key={`${l.supplier}>${l.customer}`} className="border-t border-border-soft">
                      <th scope="row" className="px-3 py-1.5 text-left font-normal">{l.supplier} to {l.customer}</th>
                      <td className="px-3 py-1.5">{l.product}</td>
                      <td className={TD}>{l.customer_share === null || l.customer_share === undefined ? <Absent reason="missing: not entered" /> : formatPercent(l.customer_share, { decimals: 0 })}</td>
                      <td className={TD}>{formatPercent(l.combined_weight, { decimals: 1 })}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm text-text-muted">No link has both its supplier and its customer in your portfolio.</p>
          )}
          <ul className="divide-y divide-border-soft border border-border">
            {data.edges.map((e) => (
              <li key={`${e.supplier}>${e.customer}`} className="flex flex-wrap items-center justify-between gap-2 px-3 py-1.5 text-sm">
                <span>
                  {e.supplier} supplies {e.customer}: {e.product}
                  <span className="ml-2 text-xs text-text-muted">
                    {e.origin === "filing" ? `from the annual report${e.fiscal_period ? `, ${e.fiscal_period}` : ""}` : "entered by you"}, as of {formatDate(e.as_of)}
                  </span>
                </span>
                <span className="flex items-center gap-3">
                  <Link href={e.source_url} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">Source</Link>
                  {e.id !== null && e.id !== undefined ? (
                    <button type="button" onClick={() => void remove(e.id as number)} className="text-text-muted hover:text-text">Remove</button>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
          {problem ? <p className="text-sm text-loss" role="alert">{problem}</p> : null}
        </div>
      )}
      <Notes notes={data.notes} />
    </Blueprint>
  );
}
