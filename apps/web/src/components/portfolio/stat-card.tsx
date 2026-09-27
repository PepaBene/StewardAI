import { ArrowDownIcon, ArrowUpIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Card, CardContent } from "@/components/ui/card";
import { SparkAreaChart } from "@/components/tremor/spark-chart";
import { cn } from "@/lib/utils";

const TREND_ICON = { gain: ArrowUpIcon, loss: ArrowDownIcon, flat: null } as const;
const TREND_CLASS = { gain: "text-gain", loss: "text-loss", flat: "" } as const;
/** De-emphasis grey by default (visualization E); the trend colour only when the delta
 * itself is the story, i.e. a trend was actually passed. */
const SPARK_COLOR = { gain: "gain", loss: "loss", flat: "neutral" } as const;

/**
 * A stat tile: label, one headline figure in proportional (non-tabular) numerals — this is
 * a standalone hero number, not a column that needs to align — and an optional line of
 * context. Pass `trend` for a signed delta so gain/loss carries its arrow, not just colour.
 * Pass `sparkline` (up to ~12 points, oldest first) for a Tremor Raw SparkAreaChart next to
 * the figure — only the trend's own colour when `trend` is gain/loss, grey otherwise.
 */
export function StatCard({
  label,
  value,
  sub,
  valueClassName,
  trend,
  sparkline,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  valueClassName?: string;
  /** When set, `sub` is treated as the signed delta and gets a matching arrow icon. */
  trend?: "gain" | "loss" | "flat";
  /** Point values, oldest first; rendered as a de-emphasis sparkline beside the figure. */
  sparkline?: readonly number[];
}) {
  const TrendIcon = trend ? TREND_ICON[trend] : null;
  return (
    <Card className="gap-0 py-4">
      <CardContent className="flex items-center justify-between gap-3 px-4">
        <div className="min-w-0 space-y-1">
          <p className="text-sm text-text-muted">{label}</p>
          <p className={cn("text-xl font-semibold tracking-tight", valueClassName)}>{value}</p>
          {sub ? (
            <div className={cn("num flex items-center gap-1 text-xs text-text-muted", trend && TREND_CLASS[trend])}>
              {TrendIcon ? <TrendIcon aria-hidden="true" className="size-3" /> : null}
              {sub}
            </div>
          ) : null}
        </div>
        {sparkline && sparkline.length > 1 ? (
          <SparkAreaChart
            aria-hidden="true"
            className="h-10 w-20 shrink-0"
            data={sparkline.map((value, i) => ({ i, value }))}
            index="i"
            categories={["value"]}
            colors={[SPARK_COLOR[trend ?? "flat"]]}
            autoMinValue
          />
        ) : null}
      </CardContent>
    </Card>
  );
}
