"use client";

import Link from "next/link";

import { ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { COVERAGE_LABEL, type PortfolioConsensus } from "@/lib/insight/consensus";
import { num } from "@/lib/insight/insight";
import { cn } from "@/lib/utils";

/* Analyst-target coverage per holding (insight I8.4): complete, partial or unavailable with the reason. */

const fetchConsensus = () => getBrowserApiClient().GET("/portfolio/consensus");

const TONE = { complete: "bg-gain-subtle", partial: "bg-warning-signal-subtle", unavailable: "bg-surface-muted text-text-muted" } as const;
const AGREEMENT = { close: "close", moderate: "moderate", wide: "wide" } as const;

function Table({ d }: { d: PortfolioConsensus }) {
  return (
    <>
      <p className="text-sm">
        {d.complete} complete, {d.partial} partial, {d.unavailable} unavailable. {d.note}
      </p>
      <div className="overflow-x-auto border border-border">
        <table className="w-full min-w-[40rem] text-sm">
          <caption className="sr-only">Analyst target coverage per holding</caption>
          <thead>
            <tr className="bg-surface-muted text-xs text-text-muted">
              {["Holding", "Coverage", "Mean target", "Against the price", "Agreement", "Source"].map((c) => (
                <th key={c} scope="col" className="px-2 py-1.5 text-right font-medium first:text-left">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {d.holdings.map((h) => (
              <tr key={h.ticker} className="border-t border-border-soft align-top">
                <th scope="row" className="px-2 py-1.5 text-left font-normal">
                  <Link href={`/companies/${encodeURIComponent(h.ticker)}/consensus`} className="hover:text-accent-ink hover:underline">
                    {h.name ?? h.ticker}
                  </Link>
                </th>
                <td className="px-2 py-1.5 text-right">
                  <span className={cn("px-1.5 text-xs", TONE[h.coverage])} title={h.reason}>
                    {COVERAGE_LABEL[h.coverage]}
                  </span>
                  {h.coverage !== "complete" ? <span className="block max-w-64 text-left text-[11px] text-text-muted sm:ml-auto">{h.reason}</span> : null}
                </td>
                <td className="num px-2 py-1.5 text-right">{num(h.mean)?.toFixed(2) ?? "—"}</td>
                <td className="num px-2 py-1.5 text-right">{num(h.upside) === null ? "—" : `${num(h.upside)! > 0 ? "+" : ""}${(num(h.upside)! * 100).toFixed(1)}%`}</td>
                <td className="px-2 py-1.5 text-right">{h.agreement ? AGREEMENT[h.agreement] : "—"}</td>
                <td className="px-2 py-1.5 text-right text-xs text-text-muted">{h.source ? `${h.source}, ${formatDate(h.as_of!)}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function ConsensusCoverage() {
  const { state } = useApiResource(fetchConsensus);
  return (
    <Blueprint className="space-y-3 px-[22px] py-5">
      <PanelHeading kicker="Stored snapshots only; none synthesised" title="Analyst targets per holding" />
      <ResourceGate state={state} label="the analyst target coverage">
        {(d) => <Table d={d} />}
      </ResourceGate>
    </Blueprint>
  );
}
