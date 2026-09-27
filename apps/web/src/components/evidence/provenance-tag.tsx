import type { Provenance } from "@/lib/evidence/provenance";
import { PROVENANCE } from "@/lib/evidence/provenance";
import { cn } from "@/lib/utils";

/**
 * How a figure was produced — verbatim, an owner correction, or computed — as a small,
 * quiet, mono-font tag distinct from ConfidenceBadge: this is never a confidence claim, so
 * it deliberately carries no hue and no icon (principle 9 of the Ledger II proposal).
 */
export function ProvenanceTag({ provenance, className }: { provenance: Provenance; className?: string }) {
  const { label, description } = PROVENANCE[provenance];
  return (
    <span
      title={description}
      data-provenance={provenance}
      className={cn(
        "inline-flex items-center rounded-sm border border-border px-1 font-mono text-[10px] tracking-wide text-text-muted uppercase",
        className,
      )}
    >
      {label}
    </span>
  );
}
