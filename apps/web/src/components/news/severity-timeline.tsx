import { SEVERITY, type SeverityLevel } from "@/lib/news/labels";
import { cn } from "@/lib/utils";

const DOT_CLASS = {
  neutral: "bg-neutral-signal",
  info: "bg-info-signal",
  warning: "bg-warning-signal",
} as const;

/**
 * A severity timeline's marker: shape never encodes severity (shape is reserved for
 * filtering), only colour + the row's own label does — the same neutral/info/warning tones
 * SeverityBadge uses, so a dot and its badge always agree. Decorative; the row's text and
 * SeverityBadge remain the accessible source of truth.
 */
export function SeverityDot({ severity, className }: { severity: number | null; className?: string }) {
  const level = severity === null ? null : (Math.max(0, Math.min(3, severity)) as SeverityLevel);
  const tone = level === null ? "neutral" : SEVERITY[level].tone;
  return (
    <span
      aria-hidden="true"
      className={cn("size-2.5 shrink-0 rounded-full", DOT_CLASS[tone as keyof typeof DOT_CLASS], className)}
    />
  );
}
