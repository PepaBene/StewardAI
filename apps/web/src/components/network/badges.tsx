import { CircleCheckIcon, CircleDashedIcon, CircleHelpIcon, ScaleIcon, XCircleIcon, ExternalLinkIcon, type LucideIcon } from "lucide-react";

import { STATUS_LABEL } from "@/lib/network/logic";
import type { EvidenceTier, RowStatus } from "@/lib/network/types";
import { cn } from "@/lib/utils";

const ICON: Record<RowStatus, LucideIcon> = {
  confirmed: CircleCheckIcon,
  suggested: CircleDashedIcon,
  disputed: ScaleIcon,
  rejected: XCircleIcon,
  unrecognised: CircleHelpIcon,
};

/** Written out in full so Tailwind finds every class. Suggested is dashed: a candidate, not a fact. */
const TONE: Record<RowStatus, string> = {
  confirmed: "border-verified/40 bg-verified-subtle text-text",
  suggested: "border-dashed border-neutral-signal bg-transparent text-text",
  disputed: "border-disputed/40 bg-disputed-subtle text-text",
  rejected: "border-border bg-transparent text-text-muted line-through",
  unrecognised: "border-info-signal/40 bg-info-signal-subtle text-text",
};

export const STATUS_HINT: Record<RowStatus, string> = {
  confirmed: "Confirmed by the owner against evidence. Counts as a fact.",
  suggested: "A candidate from a vendor, filing hit or community source. Not a fact until confirmed.",
  disputed: "Sources or the owner disagree. Not used as a fact.",
  rejected: "Refused by the owner. Not shown again as a suggestion.",
  unrecognised: "The API returned a status this screen does not know. Treated as unconfirmed.",
};

export function RowStatusBadge({ status, className }: { status: RowStatus; className?: string }) {
  const Icon = ICON[status];
  return (
    <span
      data-status={status}
      title={STATUS_HINT[status]}
      className={cn("inline-flex items-center gap-1 border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap", TONE[status], className)}
    >
      <Icon aria-hidden="true" className="size-3.5 shrink-0" />
      {STATUS_LABEL[status]}
    </span>
  );
}

export const TIER_LABEL: Record<EvidenceTier, { label: string; hint: string }> = {
  primary: { label: "Primary", hint: "A filing, registry (GLEIF) or the issuer's own document." },
  regulatory: { label: "Regulator", hint: "A regulator or exchange record." },
  owner_review: { label: "Owner-reviewed", hint: "The owner read the source passage and recorded the decision." },
  vendor: { label: "Vendor", hint: "Derived by a data vendor (for example Diffbot). Needs confirmation." },
  community: { label: "Community", hint: "Community-edited (for example Wikidata). Candidate discovery only." },
  candidate: { label: "Candidate", hint: "An unconfirmed lead without a primary source." },
  unknown: { label: "Tier not stated", hint: "The API did not state an evidence tier." },
};

export function TierBadge({ tier }: { tier: EvidenceTier }) {
  const t = TIER_LABEL[tier];
  return (
    <span title={t.hint} className="inline-flex items-center border border-border-soft px-1.5 py-0.5 text-[11px] text-text-muted">
      {t.label}
    </span>
  );
}

export function SourceLink({ href, children }: { href: string | null; children: React.ReactNode }) {
  if (!href || !/^https?:\/\//i.test(href)) return <span className="text-text">{children}</span>;
  return (
    <a href={href} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-0.5 text-accent-ink hover:underline">
      {children}
      <ExternalLinkIcon aria-hidden="true" className="size-3" />
      <span className="sr-only">(opens in a new tab)</span>
    </a>
  );
}
