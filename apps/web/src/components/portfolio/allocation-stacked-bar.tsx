import { formatPercent } from "@/lib/format";
import { categoricalClasses } from "@/lib/portfolio/categorical";
import { cn } from "@/lib/utils";

import type { AllocationItem } from "./allocation-bars";

/**
 * Allocation as a single-row stacked bar (visualization B of the redesign): segments side
 * by side with a 2px surface gap, categorical hues in first-seen order — deliberately not
 * a donut, so shares are comparable by length, not estimated angle, and no legend-eating
 * centre hole is needed. A direct label (name + percentage) is shown per segment once
 * there are 4 or more, per the mark spec; below that the shape alone reads clearly.
 */
export function AllocationStackedBar({ items, label }: { items: AllocationItem[]; label: string }) {
  const classes = categoricalClasses(items);
  const visible = items.filter((item) => (item.weight ?? 0) > 0);
  const showLabels = visible.length >= 4;
  return (
    <div className="space-y-2">
      <div role="img" aria-label={label} className="flex h-6 gap-0.5 overflow-hidden rounded-md">
        {items.map((item, i) => {
          const width = Math.max(0, Math.min(1, item.weight ?? 0)) * 100;
          if (width <= 0) return null;
          return (
            <div
              key={item.key}
              title={`${item.label}: ${formatPercent(item.weight)}`}
              className={cn("h-full first:rounded-l-md last:rounded-r-md", classes[i])}
              style={{ width: `${width}%` }}
            />
          );
        })}
      </div>
      {showLabels ? (
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-muted">
          {visible.map((item) => {
            const i = items.indexOf(item);
            return (
              <li key={item.key} className="inline-flex items-center gap-1.5">
                <span aria-hidden="true" className={cn("size-2 rounded-full", classes[i])} />
                {item.label} <span className="num">{formatPercent(item.weight)}</span>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
