import type { ClaimCheckSummary, SentenceCheck } from "@/lib/api/client";

/*
 * The claim verifier's verdict on each model-written sentence (Defence layer 4): supported,
 * unsupported (kept only in "mark" mode) or unchecked (the verifier could not run). Shown
 * next to each sentence with a word and an icon, never by colour alone.
 */

export const CHECK_LABELS: Record<SentenceCheck, { label: string; description: string }> = {
  supported: { label: "Supported", description: "The claim verifier found it in the snippets it cites." },
  unsupported: {
    label: "Unsupported",
    description: "The claim verifier did not find it in the snippets it cites; kept and marked (mark mode).",
  },
  unchecked: { label: "Unchecked", description: "The claim verifier could not check it." },
};

export const MODE_LABELS: Record<ClaimCheckSummary["mode"], string> = {
  strip: "strip mode: unsupported sentences are removed",
  mark: "mark mode: unsupported sentences are kept and marked",
};

export type SentenceCheckView = {
  kind: SentenceCheck | "none";
  label: string;
  /** Title / screen-reader text: the label, the description and the verifier's reason. */
  detail: string;
  /** Unsupported and unchecked sentences are shown distinctly; supported ones quietly. */
  prominent: boolean;
};

export function sentenceCheck(sentence: { check?: SentenceCheck | null; check_reason?: string | null }): SentenceCheckView {
  if (!sentence.check) return { kind: "none", label: "Not checked", detail: "Not checked by the claim verifier.", prominent: false };
  const { label, description } = CHECK_LABELS[sentence.check];
  const reason = sentence.check_reason?.trim();
  return {
    kind: sentence.check,
    label,
    detail: `${label}: ${reason || description}`,
    prominent: sentence.check !== "supported",
  };
}

/** "4 supported · 1 unsupported · 0 unchecked · 2 removed" (removed only when some were). */
export function claimsSummaryText(claims: ClaimCheckSummary): string {
  const parts = [
    `${claims.supported} supported`,
    `${claims.unsupported} unsupported`,
    `${claims.unchecked} unchecked`,
  ];
  if (claims.removed > 0) parts.push(`${claims.removed} removed`);
  return parts.join(" · ");
}

/** Whether the summary needs the owner's attention (anything not supported). */
export function claimsNeedAttention(claims: ClaimCheckSummary): boolean {
  return claims.unsupported > 0 || claims.unchecked > 0 || claims.removed > 0;
}
