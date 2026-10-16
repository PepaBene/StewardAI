import { activityLevel, calendarWeeks } from "@/lib/charts/calendar";
import { cn } from "@/lib/utils";

/** One hue, light to dark (dataviz: sequential = magnitude). Level 0 is the empty track. */
const LEVEL_BG = ["bg-chart-track", "bg-accent/25", "bg-accent/50", "bg-accent/75", "bg-accent"] as const;
const WEEKDAY = ["Mon", "", "Wed", "", "Fri", "", ""];

export type ActivityCalendarProps = {
  counts: ReadonlyMap<string, number>;
  /** Last day shown (ISO). */
  end: string;
  weeks?: number;
  /** What one unit is, for the tooltip: "transaction" gives "3 transactions". */
  unit?: string;
  label: string;
  className?: string;
};

/**
 * Activity calendar: one square per day, weeks across, weekdays down, shaded by how much
 * happened that day. Scrolls sideways on narrow screens rather than shrinking the cells.
 */
export function ActivityCalendar({ counts, end, weeks = 26, unit = "item", label, className }: ActivityCalendarProps) {
  const grid = calendarWeeks(counts, end, weeks);
  const inRange = grid.flatMap((w) => w.days.filter((d) => d.inRange));
  const max = Math.max(0, ...inRange.map((d) => d.count));
  const total = inRange.reduce((s, d) => s + d.count, 0);
  const active = inRange.filter((d) => d.count > 0).length;
  const plural = (n: number) => `${n} ${unit}${n === 1 ? "" : "s"}`;
  return (
    <figure className={cn("space-y-2", className)}>
      <div className="overflow-x-auto">
        <div className="inline-grid grid-flow-col grid-rows-7 gap-[3px]" role="img" aria-label={`${label}: ${plural(total)} on ${active} days over ${weeks} weeks`}>
          {WEEKDAY.map((d, i) => (
            <span key={`wd-${i}`} aria-hidden="true" className="h-3 pr-1 text-xs leading-3 text-text-faint">
              {d}
            </span>
          ))}
          {grid.flatMap((w) =>
            w.days.map((d) =>
              d.inRange ? (
                <span key={d.date} title={`${d.date}: ${d.count === 0 ? `no ${unit}s` : plural(d.count)}`} className={cn("size-3 rounded-[3px]", LEVEL_BG[activityLevel(d.count, max)])} />
              ) : (
                <span key={d.date} aria-hidden="true" className="size-3" />
              ),
            ),
          )}
        </div>
      </div>
      <figcaption className="flex flex-wrap items-center justify-between gap-2 text-xs text-text-muted">
        <span>
          {plural(total)} on {active} {active === 1 ? "day" : "days"}
        </span>
        <span className="inline-flex items-center gap-1" aria-hidden="true">
          Less
          {LEVEL_BG.map((bg) => (
            <span key={bg} className={cn("size-3 rounded-[3px]", bg)} />
          ))}
          More
        </span>
      </figcaption>
    </figure>
  );
}
