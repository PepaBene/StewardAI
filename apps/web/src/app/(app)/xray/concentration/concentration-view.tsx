"use client";

import { useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { asOfQuery, parseAsOf } from "@/lib/xray/as-of";
import { pointsLabel, type ConcentrationData, type ConcentrationLevel } from "@/lib/xray/xray";
import { cn } from "@/lib/utils";

import { Fact, Notes, Pct, TD, TH, TH_LEFT, Toned } from "../shared";

export function ConcentrationView() {
  const asOf = parseAsOf(useSearchParams().get("as_of"));
  const fetcher = useMemo(
    () => () => getBrowserApiClient().GET("/portfolio/xray/concentration", { params: { query: asOfQuery(asOf) } }),
    [asOf],
  );
  const { state } = useApiResource(fetcher);
  return (
    <ResourceGate state={state} label="concentration">
      {(data) => <Concentration data={data} />}
    </ResourceGate>
  );
}

function outside(level: ConcentrationLevel): string {
  const parts: string[] = [];
  if (level.unknown_weight > 0) parts.push(`${formatPercent(level.unknown_weight, { decimals: 1 })} unknown fund weight`);
  if (level.unclassified_weight > 0) parts.push(`${formatPercent(level.unclassified_weight, { decimals: 1 })} unlabelled`);
  return parts.join(", ") || "nothing";
}

function Active({ level, benchmarkNote }: { level: ConcentrationLevel; benchmarkNote: string | null | undefined }) {
  const over = level.overweights ?? [];
  const under = level.underweights ?? [];
  if (level.level === "fund" || level.level === "security") return null;
  if (over.length === 0 && under.length === 0) {
    return (
      <p className="mt-3 text-sm text-text-muted">
        {benchmarkNote ?? "No benchmark-relative figures here: the classification is not like-for-like with the benchmark proxy, or no proxy is stored."}
      </p>
    );
  }
  const rows = [
    ...over.map((a) => ({ ...a, side: "Overweight" })),
    ...under.map((a) => ({ ...a, side: "Underweight" })),
  ];
  return (
    <div className="mt-4">
      <p className="text-xs font-medium text-text-muted">{level.active_note ?? "Against the benchmark proxy"}: the largest differences</p>
      <table className="mt-1 w-full text-sm">
        <caption className="sr-only">Largest overweights and underweights against the benchmark proxy</caption>
        <thead>
          <tr className="border-b border-border text-xs text-text-muted">
            <th scope="col" className={TH_LEFT}>{level.label}</th>
            <th scope="col" className={TH}>Portfolio equity</th>
            <th scope="col" className={TH}>Benchmark</th>
            <th scope="col" className={TH}>Difference</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((a) => (
            <tr key={`${a.side}-${a.key}`} className="border-b border-border-soft">
              <th scope="row" className="px-3 py-1.5 text-left font-normal">
                {a.label}
                <span className="ml-2 text-[11px] text-text-muted">{a.side}</span>
              </th>
              <td className={TD}>{formatPercent(a.portfolio_weight, { decimals: 1 })}</td>
              <td className={TD}>{formatPercent(a.benchmark_weight, { decimals: 1 })}</td>
              <td className={TD}>
                <Toned value={a.active}>{pointsLabel(a.active)}</Toned>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Concentration({ data }: { data: ConcentrationData }) {
  const [level, setLevel] = useState(data.levels.find((l) => l.level === "issuer")?.level ?? data.levels[0]?.level);
  const current = data.levels.find((l) => l.level === level);
  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="px-[22px] py-4">
        <PanelHeading kicker="Look-through concentration" title="How much rides on a few names" />
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-3 md:grid-cols-3">
          <Fact label="Largest single issuer" note={data.single_issuer?.label ?? "No identified issuer"}>
            <Pct value={data.single_issuer?.weight} />
          </Fact>
          <Fact label="Against" note={data.benchmark_note ?? "Over- and underweights are for like-for-like labels only"}>
            {data.benchmark_label ?? "No benchmark"}
          </Fact>
        </dl>
        <div className="mt-4 overflow-x-auto border border-border">
          <table className="w-full text-sm">
            <caption className="sr-only">Concentration at each level</caption>
            <thead>
              <tr className="bg-surface-muted">
                <th scope="col" className={TH_LEFT}>Level</th>
                <th scope="col" className={TH}>Items</th>
                <th scope="col" className={TH_LEFT}>Largest</th>
                <th scope="col" className={TH}>Ten largest</th>
                <th scope="col" className={TH}>Effective number</th>
                <th scope="col" className={TH}>HHI</th>
                <th scope="col" className={TH_LEFT}>Outside the figures</th>
              </tr>
            </thead>
            <tbody>
              {data.levels.map((l) => (
                <tr
                  key={l.level}
                  className={cn("border-t border-border-soft align-top", l.level === level && "bg-accent-soft")}
                >
                  <th scope="row" className="px-3 py-1.5 text-left font-normal">
                    <button
                      type="button"
                      onClick={() => setLevel(l.level)}
                      aria-pressed={l.level === level}
                      className="font-medium underline-offset-2 hover:underline focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none"
                    >
                      {l.label}
                    </button>
                  </th>
                  <td className={TD}>{l.items}</td>
                  <td className="px-3 py-1.5">
                    {l.largest ? (
                      <>
                        {l.largest.label} <span className="num text-text-muted">{formatPercent(l.largest.weight, { decimals: 1 })}</span>
                      </>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className={TD}>{formatPercent(l.top10_weight, { decimals: 1 })}</td>
                  <td className={TD}>{l.effective_n?.toFixed(1) ?? "—"}</td>
                  <td className={TD}>{l.hhi?.toFixed(3) ?? "—"}</td>
                  <td className="px-3 py-1.5 text-xs text-text-muted">{outside(l)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Blueprint>

      {current ? (
        <Blueprint className="px-[22px] py-4">
          <PanelHeading kicker={current.label} title="The largest items and what they are measured on" />
          <p className="mt-1 max-w-3xl text-sm text-text-muted">{current.basis}</p>
          {current.top_items.length === 0 ? (
            <p className="mt-3 text-sm text-text-muted">{current.warnings[0] ?? "Nothing is identified at this level yet."}</p>
          ) : (
            <table className="mt-3 w-full text-sm">
              <caption className="sr-only">The ten largest {current.label.toLowerCase()} items</caption>
              <thead>
                <tr className="border-b border-border text-xs text-text-muted">
                  <th scope="col" className={TH_LEFT}>{current.label}</th>
                  <th scope="col" className={TH}>Share of portfolio</th>
                  <th scope="col" className={TH}>Share of identified</th>
                </tr>
              </thead>
              <tbody>
                {current.top_items.map((item) => (
                  <tr key={item.key} className="border-b border-border-soft">
                    <th scope="row" className="px-3 py-1.5 text-left font-normal">{item.label}</th>
                    <td className={TD}>{formatPercent(item.weight, { decimals: 2 })}</td>
                    <td className={TD}>{formatPercent(item.share_of_known, { decimals: 1 })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {current.warnings.length > 0 && current.top_items.length > 0 ? (
            <ul className="mt-2 space-y-0.5 text-xs text-text-muted">
              {current.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          ) : null}
          <Active level={current} benchmarkNote={data.benchmark_note} />
        </Blueprint>
      ) : null}
      <Notes notes={data.notes} />
    </div>
  );
}
