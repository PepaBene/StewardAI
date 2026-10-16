import { cn } from "@/lib/utils";

/*
 * Skeletons (spec A.5): surface-muted blocks in the final shape with a slow shimmer, never a
 * spinner inside a content area. `motion-safe` keeps them still under reduced motion.
 */

export function Skeleton({ className }: { className?: string }) {
  return <span aria-hidden="true" className={cn("block rounded-control bg-surface-muted motion-safe:animate-pulse", className)} />;
}

/** A chart-shaped placeholder: three faint bars at the plot height. */
export function SkeletonPlot({ height = 220, className }: { height?: number; className?: string }) {
  return (
    <div role="status" aria-label="Loading chart" className={cn("flex items-end gap-3", className)} style={{ height }}>
      {[45, 70, 55].map((pct) => (
        <span key={pct} aria-hidden="true" className="flex-1 rounded-t-mark bg-surface-muted motion-safe:animate-pulse" style={{ height: `${pct}%` }} />
      ))}
    </div>
  );
}

/** A KPI strip placeholder: n tiles of label + figure. */
export function SkeletonStrip({ tiles = 4, className }: { tiles?: number; className?: string }) {
  return (
    <div role="status" aria-label="Loading" className={cn("blueprint grid grid-cols-2 gap-px overflow-hidden md:grid-cols-4", className)}>
      {Array.from({ length: tiles }, (_, i) => (
        <div key={i} className="space-y-2 px-5 py-5">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-7 w-32" />
        </div>
      ))}
    </div>
  );
}

/** Table rows placeholder. */
export function SkeletonRows({ rows = 5, className }: { rows?: number; className?: string }) {
  return (
    <div role="status" aria-label="Loading" className={cn("space-y-3", className)}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-3">
          <Skeleton className="size-6 rounded-full" />
          <Skeleton className="h-4 flex-1" />
          <Skeleton className="h-4 w-16" />
        </div>
      ))}
    </div>
  );
}
