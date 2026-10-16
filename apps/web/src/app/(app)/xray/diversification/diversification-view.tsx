"use client";

import { useMemo, useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { HBar } from "@/components/charts/hbar";
import { Sankey } from "@/components/charts/sankey";
import { ChartWithFooter } from "@/components/insight/chart-footer";
import { RangeButtons } from "@/components/portfolio/range-buttons";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import type { components } from "@/lib/api/schema";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

/*
 * Diversification (insight I11.3): how the portfolio's weight flows from sector to industry to
 * holding once funds are looked through, where the revenue is earned, and one sentence on how
 * concentrated it is. Everything not classified stays visible as its own node.
 */

type Data = components["schemas"]["Diversification"];

const fetchDiversification = () => getBrowserApiClient().GET("/portfolio/xray/diversification");

const DEPTHS = [
  { id: "1", label: "Sector" },
  { id: "2", label: "Industry" },
  { id: "3", label: "Holding" },
] as const;
type Depth = (typeof DEPTHS)[number]["id"];

const pct = (v: number) => formatPercent(v, { decimals: 1 });

const BAND_TEXT: Record<Data["concentration"]["band"], string> = {
  concentrated: "Concentrated",
  moderate: "Moderately spread",
  broad: "Broadly spread",
  unknown: "Not measurable",
};

function Flows({ data }: { data: Data }) {
  const [depth, setDepth] = useState<Depth>("2");
  const [focus, setFocus] = useState<string>("");
  const sectors = data.nodes.filter((n) => n.column === 0);
  const { nodes, links } = useMemo(() => {
    const maxColumn = Number(depth) - 1;
    const kept = data.nodes.filter((n) => n.column <= maxColumn);
    const ids = new Set(kept.map((n) => n.id));
    // Sector -> its industries -> their holdings, for the highlight.
    const lit = new Set<string>();
    if (focus) {
      lit.add(focus);
      for (const l of data.links) if (l.source === focus) lit.add(l.target);
      for (const l of data.links) if (lit.has(l.source)) lit.add(l.target);
    }
    return {
      nodes: kept.map((n) => ({ id: n.id, label: n.label, column: n.column })),
      links: data.links
        .filter((l) => ids.has(l.source) && ids.has(l.target))
        .map((l) => ({ source: l.source, target: l.target, value: l.weight, emphasis: !focus || (lit.has(l.source) && lit.has(l.target)) })),
    };
  }, [data, depth, focus]);
  // With one column there are no flows: show the sector shares as ranked bars instead.
  const single = depth === "1";
  return (
    <>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <RangeButtons options={DEPTHS} value={depth} onChange={setDepth} label="Show down to" />
        {!single ? (
          <label className="flex items-center gap-2 text-sm">
            <span className="text-text-muted">Highlight</span>
            <select value={focus} onChange={(e) => setFocus(e.target.value)} className="h-8 border border-border bg-surface px-2 text-sm">
              <option value="">All sectors</option>
              {sectors.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>
      <ChartWithFooter
        className="mt-3"
        chart={
          single ? (
            <ul className="space-y-2">
              {sectors.map((s) => (
                <li key={s.id} className="grid grid-cols-[minmax(8rem,14rem)_1fr_4.5rem] items-center gap-3 text-sm">
                  <span>{s.label}</span>
                  <HBar value={s.weight} min={0} max={Math.max(...sectors.map((x) => x.weight), 0.01)} label={`${s.label} ${pct(s.weight)}`} formatValue={pct} />
                  <span className="num text-right">{pct(s.weight)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <Sankey nodes={nodes} links={links} height={Math.max(300, nodes.filter((n) => n.column === Number(depth) - 1).length * 34)} labelWidth={150} formatValue={pct} label="Portfolio weight by sector, industry and holding" />
          )
        }
        table={
          <table className="w-full text-sm">
            <caption className="sr-only">Portfolio weight per flow</caption>
            <thead>
              <tr className="bg-surface-muted text-left text-xs text-text-muted">
                <th scope="col" className="px-3 py-1.5 font-medium">From</th>
                <th scope="col" className="px-3 py-1.5 font-medium">To</th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">Weight</th>
              </tr>
            </thead>
            <tbody>
              {data.links.map((l) => (
                <tr key={`${l.source}>${l.target}`} className="border-t border-border-soft">
                  <td className="px-3 py-1">{data.nodes.find((n) => n.id === l.source)?.label ?? l.source}</td>
                  <td className="px-3 py-1">{data.nodes.find((n) => n.id === l.target)?.label ?? l.target}</td>
                  <td className="num px-3 py-1 text-right">{pct(l.weight)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        }
        dataset="X-ray leaf set, funds looked through"
        asOf={formatDate(data.as_of)}
        source="Holdings, fund look-through and stored classifications"
      />
      {data.residual.length > 0 ? (
        <p className="mt-2 text-xs text-text-muted">
          Not in the flows ({pct(data.residual_weight)} of the portfolio):{" "}
          {data.residual.map((r) => `${r.label} ${pct(r.weight)}`).join(", ")}.
        </p>
      ) : null}
    </>
  );
}

function Geography({ g, asOf }: { g: Data["geography"]; asOf: string }) {
  const max = Math.max(...g.rows.map((r) => r.share_of_known), 0.01);
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker="Where the revenue is earned" title="Geography" />
      <p className="mt-2 text-sm text-text-muted">{g.coverage_text}</p>
      {g.rows.length === 0 ? (
        <p className="mt-3 text-sm text-text-muted">No holding reports revenue by country yet.</p>
      ) : (
        <ChartWithFooter
          className="mt-3"
          chart={
            <ul className="space-y-1.5">
              {g.rows.slice(0, 15).map((r) => (
                <li key={r.country} className="grid grid-cols-[minmax(7rem,12rem)_1fr_4.5rem] items-center gap-3 text-sm">
                  <span>{r.label}</span>
                  <HBar value={r.share_of_known} min={0} max={max} label={`${r.label} ${pct(r.share_of_known)}`} formatValue={pct} />
                  <span className="num text-right">{pct(r.share_of_known)}</span>
                </li>
              ))}
            </ul>
          }
          table={
            <table className="w-full text-sm">
              <caption className="sr-only">Revenue share by country</caption>
              <thead>
                <tr className="bg-surface-muted text-left text-xs text-text-muted">
                  <th scope="col" className="px-3 py-1.5 font-medium">Country</th>
                  <th scope="col" className="px-3 py-1.5 text-right font-medium">Of the known part</th>
                  <th scope="col" className="px-3 py-1.5 text-right font-medium">Of the portfolio</th>
                </tr>
              </thead>
              <tbody>
                {g.rows.map((r) => (
                  <tr key={r.country} className="border-t border-border-soft">
                    <td className="px-3 py-1">{r.label}</td>
                    <td className="num px-3 py-1 text-right">{pct(r.share_of_known)}</td>
                    <td className="num px-3 py-1 text-right">{pct(r.weight)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          }
          dataset="Reported revenue by country, value-weighted"
          asOf={formatDate(asOf)}
          source="Company filings (segment notes) and your region mappings"
        />
      )}
      {g.rows.length > 15 ? <p className="mt-2 text-xs text-text-muted">{g.rows.length - 15} smaller countries are in the data view.</p> : null}
      {!g.map_allowed && g.rows.length > 0 ? (
        <p className="mt-2 text-xs text-text-muted">Shown as ranked bars: fewer than six countries are known, too few for a map to read well.</p>
      ) : null}
    </Blueprint>
  );
}

function Body({ data }: { data: Data }) {
  const c = data.concentration;
  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="px-[22px] py-4">
        <PanelHeading kicker={BAND_TEXT[c.band]} title={c.sentence} />
        <dl className="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <div><dt className="text-xs text-text-muted">Top three issuers</dt><dd className="num text-lg font-semibold">{c.top3_share === null || c.top3_share === undefined ? "—" : pct(c.top3_share)}</dd></div>
          <div><dt className="text-xs text-text-muted">Effective number of issuers</dt><dd className="num text-lg font-semibold">{c.effective_n === null || c.effective_n === undefined ? "—" : c.effective_n.toFixed(1)}</dd></div>
          <div><dt className="text-xs text-text-muted">Largest</dt><dd className="text-sm font-semibold">{c.largest ?? "—"}{c.largest_weight ? <span className="num block text-text-muted">{pct(c.largest_weight)}</span> : null}</dd></div>
          <div><dt className="text-xs text-text-muted">Issuers counted</dt><dd className="num text-lg font-semibold">{c.holdings}</dd></div>
        </dl>
        <p className="mt-2 text-xs text-text-muted">{c.basis}</p>
      </Blueprint>
      <Blueprint className="px-[22px] py-4">
        <PanelHeading kicker="Weight from sector to industry to holding" title="What you own, by sector" />
        <Flows data={data} />
      </Blueprint>
      <Geography g={data.geography} asOf={data.as_of} />
      {data.notes.length > 0 ? (
        <ul className={cn("list-disc pl-5 text-xs text-text-muted")}>
          {data.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : null}
      <p className="text-xs text-text-muted">Method {data.method_version}.</p>
    </div>
  );
}

export function DiversificationView() {
  const { state } = useApiResource(fetchDiversification);
  return (
    <ResourceGate state={state} label="diversification">
      {(data: Data) => <Body data={data} />}
    </ResourceGate>
  );
}
