import { cn } from "@/lib/utils";

/**
 * Where the portfolio sits between "spread evenly" and "one position" (§5.3). Deliberately
 * not the diverging over/under-weight visual language: concentration has no "as expected"
 * or "look here" side to take — a concentrated portfolio isn't wrong, so this is a plain
 * position marker on a neutral track, a fact rather than an alarm.
 */
export function ConcentrationScale({ position, className }: { position: number; className?: string }) {
  const pct = Math.max(0, Math.min(1, position)) * 100;
  return (
    <div aria-hidden="true" className={cn("relative h-2 rounded-full bg-surface-muted", className)}>
      <div
        className="absolute inset-y-0 w-1 -translate-x-1/2 rounded-full bg-accent"
        style={{ left: `${pct}%` }}
      />
    </div>
  );
}
