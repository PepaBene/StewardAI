import { CircleDashedIcon, ShieldAlertIcon, ShieldCheckIcon, ShieldQuestionIcon } from "lucide-react";

import type { ClaimCheckSummary, SentenceCheck } from "@/lib/api/client";
import { claimsNeedAttention, claimsSummaryText, MODE_LABELS, sentenceCheck } from "@/lib/verify/claims";
import { cn } from "@/lib/utils";

type Checked = { check?: SentenceCheck | null; check_reason?: string | null };

/** Text styling for a sentence the claim verifier did not support (a word badge goes with it). */
export function checkedTextClass(sentence: Checked): string | undefined {
  if (sentence.check === "unsupported") return "underline decoration-warning-signal decoration-wavy underline-offset-4";
  if (sentence.check === "unchecked") return "underline decoration-text-muted decoration-dotted underline-offset-4";
  return undefined;
}

/**
 * The claim verifier's result next to a sentence: a quiet shield for supported, and for
 * unsupported (mark mode) or unchecked a visible word, an icon and the verifier's reason.
 */
export function SentenceCheckMark({ sentence, className }: { sentence: Checked; className?: string }) {
  const view = sentenceCheck(sentence);
  if (view.kind === "none") return null;
  if (view.kind === "supported") {
    return (
      <span title={view.detail} className={cn("ml-1 inline-flex align-middle", className)}>
        <ShieldCheckIcon aria-hidden="true" className="size-3.5 text-neutral-signal" />
        <span className="sr-only">{view.detail}</span>
      </span>
    );
  }
  const Icon = view.kind === "unsupported" ? ShieldAlertIcon : CircleDashedIcon;
  return (
    <span className={cn("ml-1 inline-flex flex-wrap items-center gap-1 align-middle", className)}>
      <span
        title={view.detail}
        className={cn(
          "inline-flex items-center gap-1 rounded-sm border px-1 text-xs font-medium whitespace-nowrap text-text",
          view.kind === "unsupported" ? "border-warning-signal/40 bg-warning-signal-subtle" : "border-dashed border-border bg-surface-muted",
        )}
      >
        <Icon aria-hidden="true" className={cn("size-3.5", view.kind === "unsupported" ? "text-warning-signal" : "text-text-muted")} />
        {view.label}
      </span>
      {sentence.check_reason ? <span className="text-xs text-text-muted">{sentence.check_reason}</span> : null}
    </span>
  );
}

/** "Claim check (mark mode…): 4 supported · 1 unsupported · 0 unchecked", with its note. */
export function ClaimsSummaryLine({ claims, className }: { claims: ClaimCheckSummary | null | undefined; className?: string }) {
  if (!claims) {
    return (
      <p className={cn("flex items-center gap-1.5 text-xs text-text-muted", className)}>
        <ShieldQuestionIcon aria-hidden="true" className="size-3.5" />
        The claim verifier did not check this report.
      </p>
    );
  }
  const attention = claimsNeedAttention(claims);
  const Icon = attention ? ShieldAlertIcon : ShieldCheckIcon;
  return (
    <p className={cn("flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs", attention ? "text-text" : "text-text-muted", className)}>
      <Icon aria-hidden="true" className={cn("size-3.5", attention ? "text-warning-signal" : "text-neutral-signal")} />
      <span className="font-medium">Claim check</span>
      <span className="num">{claimsSummaryText(claims)}</span>
      <span className="text-text-muted">({MODE_LABELS[claims.mode]})</span>
      {claims.note ? <span className="text-text-muted">· {claims.note}</span> : null}
    </p>
  );
}
