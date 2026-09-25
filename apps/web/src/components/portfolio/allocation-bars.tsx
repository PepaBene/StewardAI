import { formatMoney, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

export type AllocationItem = {
  key: string;
  label: string;
  sublabel?: string;
  value: number;
  weight: number | null;
  /** Cash and "Unclassified" are drawn in the neutral colour. */
  muted?: boolean;
};

/**
 * Horizontal bar list in the style of Tremor's BarList: one row per item, bar length is
 * the weight, value and percentage right-aligned in tabular numerals. The bars are
 * decorative (aria-hidden); every row states its figures in text.
 */
export function AllocationBars({
  items,
  currency,
  label,
}: {
  items: AllocationItem[];
  currency: string;
  label: string;
}) {
  return (
    <ul aria-label={label} className="space-y-2.5">
      {items.map((item) => {
        const width = Math.max(0, Math.min(1, item.weight ?? 0)) * 100;
        return (
          <li key={item.key} className="space-y-1">
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className="min-w-0 truncate">
                <span className="text-text">{item.label}</span>
                {item.sublabel ? (
                  <span className="ml-1.5 font-mono text-xs text-text-muted">{item.sublabel}</span>
                ) : null}
              </span>
              <span className="num shrink-0 text-right">
                <span className="text-text-muted">{formatMoney(item.value, currency, { decimals: 0 })}</span>
                <span className="ml-3 inline-block w-14 font-medium">{formatPercent(item.weight)}</span>
              </span>
            </div>
            <div aria-hidden="true" className="h-2 overflow-hidden rounded-full bg-surface-muted">
              <div
                className={cn("h-full rounded-full", item.muted ? "bg-neutral-signal" : "bg-chart-bar")}
                style={{ width: `${width}%` }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
