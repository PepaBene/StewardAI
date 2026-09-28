import type { ReactNode } from "react";

import { SparkArea } from "@/components/charts";
import { BlueprintCorners } from "@/components/ui/blueprint";
import { cn } from "@/lib/utils";

/**
 * The Overview stat tile: small muted label, big condensed figure, one sub-line, and an
 * area sparkline along the bottom (decorative: the figure says the same thing).
 */
export function StatTile({
  label,
  value,
  sub,
  subClassName,
  spark,
  sparkNote,
  className,
}: {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  subClassName?: string;
  /** Values oldest first; fewer than two draws nothing. */
  spark?: readonly (number | null)[];
  /** Tooltip for the sparkline, e.g. "Invested value, 1Y". */
  sparkNote?: string;
  className?: string;
}) {
  return (
    <div className={cn("blueprint flex min-w-0 flex-col gap-1.5 p-4.5", className)}>
      <BlueprintCorners />
      <div className="text-xs text-text-muted">{label}</div>
      <div className="font-heading text-[30px] leading-[1.05] font-semibold tracking-[-0.01em]">{value}</div>
      {sub ? <div className={cn("text-xs text-text-muted", subClassName)}>{sub}</div> : null}
      {spark && spark.filter((v) => v !== null).length > 1 ? (
        <div className="mt-auto pt-2" title={sparkNote}>
          <SparkArea values={spark} width={200} height={60} className="h-16" />
        </div>
      ) : null}
    </div>
  );
}
