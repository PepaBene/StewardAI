import type { Confidence } from "@/lib/api/client";
import { CONFIDENCE } from "@/lib/evidence/confidence";
import { cn } from "@/lib/utils";

/** Fixed left-to-right order: the confidence scale is ordinal, not categorical. */
const ORDER = ["verified", "corroborated", "unconfirmed", "disputed"] as const satisfies readonly Confidence[];

const SEGMENT_CLASS = {
  verified: "bg-verified",
  corroborated: "bg-corroborated",
  unconfirmed: "bg-unconfirmed",
  disputed: "bg-disputed",
} as const satisfies Record<Confidence, string>;

/**
 * A 100%-stacked bar of verdict/fact confidence, always in Verified -> Corroborated ->
 * Unconfirmed -> Disputed order with a 2px surface gap between segments, so trustworthiness
 * reads as a shape (how far the bar gets before it turns amber) before anyone reads a count.
 * Decorative (aria-hidden); pass `label` for the accessible summary.
 */
export function ConfidenceBar({
  counts,
  label,
  className,
}: {
  counts: Record<Confidence, number>;
  label: string;
  className?: string;
}) {
  const total = ORDER.reduce((sum, k) => sum + counts[k], 0);
  if (total === 0) {
    return (
      <span className={cn("inline-block h-2 w-24 rounded-full border border-dashed border-border", className)} title={label} />
    );
  }
  return (
    <span role="img" aria-label={label} title={label} className={cn("flex h-2 w-24 gap-0.5", className)}>
      {ORDER.filter((k) => counts[k] > 0).map((k) => (
        <span
          key={k}
          className={cn("h-full first:rounded-l-full last:rounded-r-full", SEGMENT_CLASS[k])}
          style={{ width: `${(counts[k] / total) * 100}%` }}
          title={`${counts[k]} ${CONFIDENCE[k].label.toLowerCase()}`}
        />
      ))}
    </span>
  );
}
