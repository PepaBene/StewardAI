import { StatusDot } from "@/components/ui/status-dot";
import { SEVERITY, type SeverityLevel } from "@/lib/news/labels";

/**
 * A severity timeline's marker: the same neutral/info/warning tone SeverityBadge uses, so
 * a dot and its badge always agree. Decorative; the row's text and SeverityBadge remain the
 * accessible source of truth.
 */
export function SeverityDot({ severity, className }: { severity: number | null; className?: string }) {
  const level = severity === null ? null : (Math.max(0, Math.min(3, severity)) as SeverityLevel);
  const tone = level === null ? "neutral" : SEVERITY[level].tone;
  return <StatusDot tone={tone as "neutral" | "info" | "warning"} className={className} />;
}
