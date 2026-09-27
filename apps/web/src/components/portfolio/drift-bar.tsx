import { cn } from "@/lib/utils";

/**
 * A diverging bar around a zero baseline: cool (underweight, "as expected") extends left,
 * warm (overweight, "look here") extends right. Decorative only — the signed percentage-
 * point figure next to it is the source of truth, this is a shape to scan quickly.
 */
export function DriftBar({ drift, scale = 0.1, className }: { drift: number | null; scale?: number; className?: string }) {
  const width = drift === null ? 0 : Math.max(-1, Math.min(1, drift / scale)) * 50;
  return (
    <div aria-hidden="true" className={cn("relative h-2 w-20 rounded-full bg-surface-muted", className)}>
      <div className="absolute inset-y-0 left-1/2 w-px bg-border" />
      <div
        className={cn(
          "absolute inset-y-0 rounded-full",
          width >= 0 ? "left-1/2 bg-diverging-over" : "right-1/2 bg-diverging-under",
        )}
        style={{ width: `${Math.abs(width)}%` }}
      />
    </div>
  );
}
