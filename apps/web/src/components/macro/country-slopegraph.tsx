import { ChartFrame } from "@/components/data-state";
import { SVG_BASE } from "@/components/charts/shared";
import { formatNumber, formatSigned } from "@/lib/charts/format";
import { slopeRows, type SlopeRow } from "@/lib/macro/logic";
import type { CountryComparison } from "@/lib/macro/types";
import { cn } from "@/lib/utils";

const W = 640;
const ROW = 26;
const TOP = 28;
const X_LEFT = 230;
const X_RIGHT = 410;

function stroke(r: SlopeRow): string {
  if (r.rankChange === null) return "stroke-border";
  if (r.rankChange > 0) return "stroke-accent";
  if (r.rankChange < 0) return "stroke-diverging-over";
  return "stroke-text-faint";
}

function describeMove(r: SlopeRow): string {
  if (r.rankChange === null) return "not ranked at both ends";
  if (r.rankChange === 0) return "unchanged rank";
  return `${r.rankChange > 0 ? "up" : "down"} ${Math.abs(r.rankChange)}`;
}

/**
 * Ranked slopegraph of one indicator across countries at two dates. Position is rank, not
 * value, so countries compare on one scale even when levels differ; the values sit beside the
 * names and in the table. A country with no value at an end is listed under the ranking as
 * "not covered" and gets no line, instead of dropping to the bottom as a zero.
 */
export function CountrySlopegraph({ data }: { data: CountryComparison }) {
  const rows = slopeRows(data.rows);
  const ranked = rows.length;
  const rankedFrom = rows.filter((r) => r.fromRank !== null).length;
  const rankedTo = rows.filter((r) => r.toRank !== null).length;
  const height = TOP + Math.max(rankedFrom, rankedTo) * ROW + 24;
  const yAt = (index: number) => TOP + index * ROW + ROW / 2;

  const left = [...rows].filter((r) => r.fromRank !== null).sort((a, b) => a.fromRank! - b.fromRank! || a.country.localeCompare(b.country));
  const right = [...rows].filter((r) => r.toRank !== null).sort((a, b) => a.toRank! - b.toRank! || a.country.localeCompare(b.country));
  // Vertical position is the order within each column, so tied countries do not overlap;
  // the printed rank still shows the shared (competition) rank.
  const yLeft = new Map(left.map((r, i) => [r.country, yAt(i)]));
  const yRight = new Map(right.map((r, i) => [r.country, yAt(i)]));
  const unranked = rows.filter((r) => r.toRank === null || r.fromRank === null);
  const risers = rows.filter((r) => (r.rankChange ?? 0) > 0).sort((a, b) => b.rankChange! - a.rankChange!);
  const fallers = rows.filter((r) => (r.rankChange ?? 0) < 0).sort((a, b) => a.rankChange! - b.rankChange!);
  const takeaway = `${right[0]?.countryName ?? "No country"} ranks first in ${data.toLabel}.${
    risers[0] ? ` ${risers[0].countryName} rose most (${describeMove(risers[0])}).` : ""
  }${fallers[0] ? ` ${fallers[0].countryName} fell most (${describeMove(fallers[0])}).` : ""}`;

  return (
    <ChartFrame
      id="country-ranking"
      kicker="Country comparison"
      title={data.title}
      takeaway={takeaway}
      units={`${data.unit} · ${rankedTo} of ${ranked} countries ranked in ${data.toLabel}`}
      provenance={{
        source: data.provenance.source,
        sourceUrl: data.provenance.sourceUrl,
        asOf: data.provenance.asOf,
        state: data.provenance.state,
        reason: data.provenance.reason,
        method: "Rank by value, highest first; ties share the better rank",
        coverage: { covered: rankedTo, total: ranked, unit: "countries" },
      }}
      table={{
        caption: `${data.title}: value and rank per country`,
        columns: ["Country", data.fromLabel, `Rank ${data.fromLabel}`, data.toLabel, `Rank ${data.toLabel}`, "Rank change"],
        rows: rows.map((r) => [
          r.countryName,
          r.from === null ? "not covered" : formatNumber(r.from, 1),
          r.fromRank ?? "—",
          r.to === null ? "not covered" : formatNumber(r.to, 1),
          r.toRank ?? "—",
          r.rankChange === null ? "—" : r.rankChange === 0 ? "0" : formatSigned(r.rankChange, 0),
        ]),
      }}
    >
      <figure className="m-0">
        <svg
          viewBox={`0 0 ${W} ${height}`}
          className={SVG_BASE}
          role="img"
          aria-label={`${data.title}, ${data.fromLabel} to ${data.toLabel}. ${takeaway}`}
        >
          <text x={X_LEFT} y={14} textAnchor="end" fontSize={12} className="fill-text font-semibold">
            {data.fromLabel}
          </text>
          <text x={X_RIGHT} y={14} fontSize={12} className="fill-text font-semibold">
            {data.toLabel}
          </text>
          {rows
            .filter((r) => r.fromRank !== null && r.toRank !== null)
            .map((r) => (
              <line
                key={r.country}
                x1={X_LEFT + 8}
                x2={X_RIGHT - 8}
                y1={yLeft.get(r.country)!}
                y2={yRight.get(r.country)!}
                className={stroke(r)}
                strokeWidth={r.rankChange === 0 ? 1 : 2}
                strokeDasharray={r.rankChange === 0 ? "3 3" : undefined}
              >
                <title>{`${r.countryName}: rank ${r.fromRank} → ${r.toRank} (${describeMove(r)})`}</title>
              </line>
            ))}
          {left.map((r) => (
            <g key={`l-${r.country}`}>
              <circle cx={X_LEFT + 8} cy={yLeft.get(r.country)!} r={3} className="fill-text" />
              <text x={X_LEFT} y={yLeft.get(r.country)! + 4} textAnchor="end" fontSize={12} className="fill-text">
                <tspan className="fill-text-faint font-mono">{r.fromRank}</tspan> {r.countryName}{" "}
                <tspan className="num fill-text-muted">{formatNumber(r.from!, 1)}</tspan>
              </text>
            </g>
          ))}
          {right.map((r) => (
            <g key={`r-${r.country}`}>
              <circle cx={X_RIGHT - 8} cy={yRight.get(r.country)!} r={3} className="fill-text" />
              <text x={X_RIGHT} y={yRight.get(r.country)! + 4} fontSize={12} className="fill-text">
                <tspan className="num fill-text-muted">{formatNumber(r.to!, 1)}</tspan> {r.countryName}{" "}
                <tspan className="fill-text-faint font-mono">{r.toRank}</tspan>
                {r.rankChange !== null && r.rankChange !== 0 ? (
                  <tspan className="fill-text-muted"> {r.rankChange > 0 ? "▲" : "▼"}{Math.abs(r.rankChange)}</tspan>
                ) : null}
              </text>
            </g>
          ))}
        </svg>
        {unranked.length > 0 ? (
          <p className="mt-1 text-xs text-text-muted">
            <span className="font-medium text-text">Not covered at one end:</span>{" "}
            {unranked.map((r, i) => (
              <span key={r.country} className={cn(i > 0 && "before:content-['·'] before:mx-1.5")}>
                {r.countryName} ({r.from === null ? data.fromLabel : data.toLabel})
              </span>
            ))}
          </p>
        ) : null}
        <figcaption className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-text-muted">
          <span className="inline-flex items-center gap-1.5">
            <i aria-hidden="true" className="inline-block h-0.5 w-4 bg-accent" /> Moved up (▲)
          </span>
          <span className="inline-flex items-center gap-1.5">
            <i aria-hidden="true" className="inline-block h-0.5 w-4 bg-diverging-over" /> Moved down (▼)
          </span>
          <span className="inline-flex items-center gap-1.5">
            <i aria-hidden="true" className="inline-block w-4 border-t border-dashed border-text-faint" /> Unchanged rank
          </span>
        </figcaption>
      </figure>
    </ChartFrame>
  );
}
