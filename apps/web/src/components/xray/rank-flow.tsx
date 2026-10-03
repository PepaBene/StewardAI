import { SrTable } from "@/components/charts";
import { quarterLabel, type RankFlow } from "@/lib/xray/ownership";
import { compact, date, truncate } from "@/lib/xray/format";
import { cn } from "@/lib/utils";

const W = 900;
const ROW = 26;
const TOP = 54;
const X0 = 150;
const X1 = 560;

function lagText(lag: number | null): string {
  return lag === null ? "filing lag unknown" : `filed ${lag} d after quarter end`;
}

/**
 * Slopegraph of the top holders' rank: the previous loaded quarter on the left, the latest on
 * the right. Only managers in the latest top list appear (those that dropped out of it are
 * not in the data), so a rank here is a rank within that list. Direction is carried by line
 * style and the arrow in the table, not colour alone: solid thick = moved up, dashed = moved
 * down, thin = unchanged; a manager with no earlier position has no line.
 */
export function RankFlowChart({ flow, label, className }: { flow: RankFlow; label: string; className?: string }) {
  const n = flow.rows.length;
  const beforeRanks = flow.rows.filter((r) => r.rankBefore !== null).map((r) => r.rankBefore as number);
  const nBefore = beforeRanks.length;
  const H = TOP + Math.max(n, nBefore) * ROW + 12;
  const yNow = (rank: number) => TOP + (rank - 1) * ROW + ROW / 2;
  const yBefore = yNow;
  const prevQ = flow.previousPeriod ? quarterLabel(flow.previousPeriod) : "Previous quarter";
  const nowQ = flow.latestPeriod ? quarterLabel(flow.latestPeriod) : "Latest quarter";
  const byBefore = [...flow.rows].filter((r) => r.rankBefore !== null).sort((a, b) => (a.rankBefore as number) - (b.rankBefore as number));
  return (
    <figure className={cn("m-0 relative overflow-x-auto", className)}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} className="block h-auto w-full min-w-[640px]">
        <text x={X0} y={14} textAnchor="end" fontSize={12} className="fill-text font-semibold">
          {prevQ}
        </text>
        <text x={X0} y={28} textAnchor="end" fontSize={11} className="num fill-text-muted">
          {flow.previousPeriod ? `quarter end ${date(flow.previousPeriod)}` : "not loaded"}
        </text>
        <text x={X0} y={41} textAnchor="end" fontSize={11} className="num fill-text-faint">
          {lagText(flow.previousLagDays)}
        </text>
        <text x={X1} y={14} fontSize={12} className="fill-text font-semibold">
          {nowQ}
        </text>
        <text x={X1} y={28} fontSize={11} className="num fill-text-muted">
          {flow.latestPeriod ? `quarter end ${date(flow.latestPeriod)}` : ""}
        </text>
        <text x={X1} y={41} fontSize={11} className="num fill-text-faint">
          {lagText(flow.latestLagDays)}
        </text>
        {flow.rows.map((r) => {
          const up = r.move !== null && r.move > 0;
          const down = r.move !== null && r.move < 0;
          return (
            <g key={r.cik}>
              <title>{`${r.name}: rank ${r.rankBefore ?? "none"} → ${r.rankNow}`}</title>
              {r.rankBefore !== null ? (
                <line
                  x1={X0 + 8}
                  x2={X1 - 8}
                  y1={yBefore(r.rankBefore)}
                  y2={yNow(r.rankNow)}
                  className={up ? "stroke-chart-line" : down ? "stroke-chart-line" : "stroke-chart-benchmark"}
                  strokeWidth={up ? 2.6 : down ? 1.8 : 1}
                  strokeDasharray={down ? "5 3" : undefined}
                />
              ) : null}
              <circle cx={X1 - 8} cy={yNow(r.rankNow)} r={3.5} className="fill-chart-line" />
              <text x={X1 + 4} y={yNow(r.rankNow) + 4} fontSize={12} className="fill-text">
                <tspan className="num font-semibold">{r.rankNow}</tspan> {truncate(r.name, 34)}
                <tspan className="num fill-text-muted"> · {compact(r.shares)} sh</tspan>
                {r.rankBefore === null ? <tspan className="fill-text-muted"> · no earlier position</tspan> : null}
              </text>
            </g>
          );
        })}
        {byBefore.map((r) => (
          <g key={`b-${r.cik}`}>
            <circle cx={X0 + 8} cy={yBefore(r.rankBefore as number)} r={3.5} className="fill-chart-benchmark" />
            <text x={X0} y={yBefore(r.rankBefore as number) + 4} textAnchor="end" fontSize={12} className="num fill-text-muted">
              {r.rankBefore}
            </text>
          </g>
        ))}
      </svg>
      <SrTable
        caption={label}
        columns={["Manager", `Rank ${nowQ}`, `Rank ${prevQ}`, "Move", "Shares now", "Shares before"]}
        rows={flow.rows.map((r) => [
          r.name,
          String(r.rankNow),
          r.rankBefore === null ? "no earlier position" : String(r.rankBefore),
          r.move === null ? "—" : r.move > 0 ? `up ${r.move}` : r.move < 0 ? `down ${-r.move}` : "unchanged",
          compact(r.shares),
          compact(r.prevShares),
        ])}
      />
    </figure>
  );
}
