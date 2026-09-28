"use client";

import { Treemap } from "@/components/charts";
import type { Holding } from "@/lib/api/client";
import { TREEMAP_MODES, treemapItems, type TreemapMode } from "@/lib/portfolio/treemap";
import { cn } from "@/lib/utils";

/**
 * The portfolio as a size/colour map: tile area is the position's weight, tile colour the
 * chosen move on the diverging heat scale — today's close-to-close move, or unrealised P/L
 * since purchase. An alternate view beside the holdings table, never a replacement: the
 * table still states exactly what each position is worth.
 */
export function PortfolioTreemap({
  holdings,
  mode,
  dayChange,
  aspectRatio = 4,
  className,
}: {
  holdings: readonly Holding[];
  mode: TreemapMode;
  dayChange: (ticker: string) => number | null | undefined;
  aspectRatio?: number;
  className?: string;
}) {
  const m = TREEMAP_MODES[mode];
  return (
    <Treemap
      items={treemapItems(holdings, mode, dayChange)}
      cap={m.cap}
      aspectRatio={aspectRatio}
      label={`${m.title}: ${m.description}`}
      className={cn("min-w-[36rem]", className)}
    />
  );
}

/** Segmented "Day move | Unrealised P/L" switch for the treemap colour. */
export function TreemapModeSwitch({ value, onChange }: { value: TreemapMode; onChange: (mode: TreemapMode) => void }) {
  return (
    <div role="group" aria-label="Colour tiles by" className="inline-flex border border-border text-xs">
      {(Object.keys(TREEMAP_MODES) as TreemapMode[]).map((mode, i) => (
        <button
          key={mode}
          type="button"
          aria-pressed={value === mode}
          onClick={() => onChange(mode)}
          className={cn(
            "px-2.5 py-1 font-medium",
            i > 0 && "border-l border-border",
            value === mode ? "bg-accent text-accent-foreground" : "text-text-muted hover:bg-border-soft hover:text-text",
          )}
        >
          {TREEMAP_MODES[mode].label}
        </button>
      ))}
    </div>
  );
}
