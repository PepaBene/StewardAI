"use client";

import { ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { spreadLabel, type GroupStats, type SectorMap } from "@/lib/insights/insights";

import { Notes } from "../../xray/shared";

const fetchSectors = () => getBrowserApiClient().GET("/insights/sectors");

export function SectorsView() {
  const { state } = useApiResource(fetchSectors);
  return (
    <ResourceGate state={state} label="the sector map">
      {(map) => <Body map={map} />}
    </ResourceGate>
  );
}

const pct = (n: number) => formatPercent(n, { decimals: 0 });
const multiple = (n: number) => `${n.toFixed(1)}x`;

function Body({ map }: { map: SectorMap }) {
  return (
    <div className="flex flex-col gap-5">
      <p className="max-w-prose text-sm text-text-muted">
        {map.universe} companies have a stored profile. Each statistic shows how many of the group stand behind it; a median needs at least three.
      </p>
      <Table title="Sectors" groups={map.sectors} />
      <Table title="Industries" groups={map.industries} />
      {map.unclassified.length > 0 ? (
        <p className="max-w-prose text-sm">
          Held but not classified, so left out of every group: <span className="font-semibold">{map.unclassified.join(", ")}</span>.
        </p>
      ) : null}
      <Notes notes={[...map.not_covered, map.method]} title="Not covered, and how this works" />
    </div>
  );
}

function Table({ title, groups }: { title: string; groups: GroupStats[] }) {
  const industries = title === "Industries";
  return (
    <Blueprint className="p-4">
      <PanelHeading level={3} title={title} />
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[56rem] text-sm">
          <caption className="sr-only">{title}: companies, markets, revenue growth, operating margin and price/earnings</caption>
          <thead>
            <tr className="text-left text-xs text-text-muted">
              <th scope="col" className="py-1 font-medium">{industries ? "Industry" : "Sector"}</th>
              <th scope="col" className="py-1 text-right font-medium">Companies</th>
              <th scope="col" className="py-1 font-medium">Markets</th>
              <th scope="col" className="py-1 font-medium">Revenue growth</th>
              <th scope="col" className="py-1 font-medium">Operating margin</th>
              <th scope="col" className="py-1 font-medium">Price/earnings</th>
              <th scope="col" className="py-1 text-right font-medium">Held</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <tr key={`${g.parent ?? ""}-${g.name}`} className="border-t border-border-soft align-top">
                <th scope="row" className="py-1.5 pr-3 text-left font-normal">
                  {g.name}
                  {g.parent ? <div className="text-xs text-text-muted">{g.parent}</div> : null}
                  <div className="text-xs text-text-muted">{g.tickers.map((t) => t.split("_")[0]).join(", ")}</div>
                </th>
                <td className="num py-1.5 text-right">{g.companies}</td>
                <td className="py-1.5 pr-3 text-xs">{g.markets.join(", ") || "Not stated"}</td>
                <td className="py-1.5 pr-3 text-xs">{spreadLabel(g.revenue_growth, pct)}</td>
                <td className="py-1.5 pr-3 text-xs">{spreadLabel(g.operating_margin, pct)}</td>
                <td className="py-1.5 pr-3 text-xs">{spreadLabel(g.pe, multiple)}</td>
                <td className="num py-1.5 text-right">
                  {g.held > 0 ? `${formatPercent(g.held_weight, { decimals: 1 })} (${g.held})` : "Not held"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Blueprint>
  );
}
