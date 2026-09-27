import { ArrowDownIcon, ArrowUpIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const TREND_ICON = { gain: ArrowUpIcon, loss: ArrowDownIcon, flat: null } as const;
const TREND_CLASS = { gain: "text-gain", loss: "text-loss", flat: "" } as const;

/**
 * A stat tile: label, one headline figure in proportional (non-tabular) numerals — this is
 * a standalone hero number, not a column that needs to align — and an optional line of
 * context. Pass `trend` for a signed delta so gain/loss carries its arrow, not just colour.
 */
export function StatCard({
  label,
  value,
  sub,
  valueClassName,
  trend,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  valueClassName?: string;
  /** When set, `sub` is treated as the signed delta and gets a matching arrow icon. */
  trend?: "gain" | "loss" | "flat";
}) {
  const TrendIcon = trend ? TREND_ICON[trend] : null;
  return (
    <Card className="gap-0 py-4">
      <CardContent className="space-y-1 px-4">
        <p className="text-sm text-text-muted">{label}</p>
        <p className={cn("text-xl font-semibold tracking-tight", valueClassName)}>{value}</p>
        {sub ? (
          <div className={cn("num flex items-center gap-1 text-xs text-text-muted", trend && TREND_CLASS[trend])}>
            {TrendIcon ? <TrendIcon aria-hidden="true" className="size-3" /> : null}
            {sub}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
