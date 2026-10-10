import { formatDate } from "@/lib/format";
import { seriesDomain, seriesPath, type Point } from "@/lib/performance/performance";
import { cn } from "@/lib/utils";

export type ChartSeries = {
  name: string;
  points: ReadonlyArray<Point>;
  tone: "primary" | "benchmark";
  dashed?: boolean;
};

const W = 720;
const H = 240;
const BOX = { left: 52, right: W - 14, top: 12, bottom: H - 26 };

/**
 * Dated line chart in the app's chart colours. A series with a gap (null value) breaks the
 * line instead of bridging it; a legend names every line, so colour is never the only cue.
 */
export function LineChart({
  series,
  baseline,
  format,
  label,
}: {
  series: ChartSeries[];
  /** A reference level drawn as a solid rule (100 for an index, 0 for a return). */
  baseline?: number;
  format: (value: number) => string;
  label: string;
}) {
  const drawn = series.filter((s) => s.points.length > 1);
  const dates = drawn.flatMap((s) => s.points.map((p) => p.date)).sort();
  const domain = seriesDomain(
    drawn.map((s) => s.points),
    baseline === undefined ? [] : [baseline],
  );
  if (drawn.length === 0 || !domain || dates.length < 2) {
    return <p className="text-sm text-text-muted">Too few stored days to draw a chart.</p>;
  }
  const span = { first: dates[0], last: dates[dates.length - 1] };
  const y = (n: number) => BOX.top + (1 - (n - domain.lo) / (domain.hi - domain.lo)) * (BOX.bottom - BOX.top);
  const ticks = [domain.lo, domain.hi, ...(baseline === undefined ? [] : [baseline])];
  return (
    <figure className="space-y-2">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full max-w-3xl" role="img" aria-label={label}>
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={BOX.left}
              x2={BOX.right}
              y1={y(t)}
              y2={y(t)}
              className={t === baseline ? "stroke-border" : "stroke-chart-grid"}
              strokeDasharray={t === baseline ? undefined : "2 3"}
            />
            <text x={BOX.left - 6} y={y(t) + 3} textAnchor="end" className="fill-text-muted text-[10px]">
              {format(t)}
            </text>
          </g>
        ))}
        {drawn.map((s) => (
          <path
            key={s.name}
            d={seriesPath(s.points, BOX, span, domain)}
            fill="none"
            className={s.tone === "primary" ? "stroke-chart-line" : "stroke-chart-benchmark"}
            strokeWidth={s.tone === "primary" ? 2.25 : 1.25}
            strokeDasharray={s.dashed ? "4 3" : undefined}
          />
        ))}
        <text x={BOX.left} y={H - 8} className="fill-text-muted text-[10px]">
          {formatDate(span.first)}
        </text>
        <text x={BOX.right} y={H - 8} textAnchor="end" className="fill-text-muted text-[10px]">
          {formatDate(span.last)}
        </text>
      </svg>
      <figcaption className="flex flex-wrap gap-x-5 text-xs text-text-muted">
        {drawn.map((s) => (
          <span key={s.name}>
            <span
              className={cn(
                "mr-1.5 inline-block w-5 align-middle",
                s.dashed ? "h-0 border-t border-dashed" : "h-0.5",
                s.tone === "primary" ? "border-chart-line bg-chart-line" : "border-chart-benchmark bg-chart-benchmark",
                s.dashed && "bg-transparent",
              )}
            />
            {s.name}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}
