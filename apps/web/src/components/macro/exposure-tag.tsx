import { BASIS_HINTS, BASIS_LABELS } from "@/lib/macro/logic";
import type { BasisLabel } from "@/lib/macro/types";
import { cn } from "@/lib/utils";

/** Border style differs per basis so the three kinds read apart without colour. */
const STYLE: Record<BasisLabel, string> = {
  reported: "border-solid border-text",
  estimated: "border-dashed border-text",
  user_assigned: "border-dotted border-text",
  unclassified: "border-solid border-border",
};

export function ExposureTag({ basis, className }: { basis: BasisLabel; className?: string }) {
  return (
    <span
      data-basis={basis}
      title={BASIS_HINTS[basis]}
      className={cn("inline-flex items-center border-2 px-1 font-mono text-[10px] tracking-wide whitespace-nowrap text-text uppercase", STYLE[basis], className)}
    >
      {BASIS_LABELS[basis]}
    </span>
  );
}
