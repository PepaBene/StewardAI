import { CheckCircle2Icon, MessageSquareIcon, XCircleIcon } from "lucide-react";

import type { EvidenceRelation } from "@/lib/api/client";
import { EVIDENCE_RELATION } from "@/lib/evidence/confidence";
import { cn } from "@/lib/utils";

const ICONS = {
  supports: CheckCircle2Icon,
  mentions: MessageSquareIcon,
  contradicts: XCircleIcon,
} as const satisfies Record<EvidenceRelation, unknown>;

const TONE_CLASS = {
  "diverging-under": {
    text: "text-diverging-under",
    border: "border-diverging-under/40",
    bg: "bg-diverging-under-subtle",
  },
  "diverging-neutral": {
    text: "text-text-muted",
    border: "border-border",
    bg: "bg-surface-muted",
  },
  "diverging-over": {
    text: "text-diverging-over",
    border: "border-diverging-over/40",
    bg: "bg-diverging-over-subtle",
  },
} as const;

/**
 * Whether a citation supports, contradicts, or merely mentions the claim it's attached to —
 * shown next to (never instead of) its ConfidenceBadge: confidence is how sure we are the
 * value is right, relation is how this particular source treats it.
 */
export function EvidenceRelationBadge({ relation, className }: { relation: EvidenceRelation; className?: string }) {
  const { label, tone, description } = EVIDENCE_RELATION[relation];
  const Icon = ICONS[relation];
  const classes = TONE_CLASS[tone];
  return (
    <span
      title={description}
      data-relation={relation}
      className={cn(
        "inline-flex items-center gap-1 border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap",
        classes.border,
        classes.bg,
        classes.text,
        className,
      )}
    >
      <Icon aria-hidden="true" className="size-3.5" />
      {label}
    </span>
  );
}
