import type { components } from "@/lib/api/schema";

export type AutoDecision = components["schemas"]["AutoDecision"];
export type NeedsYouItem = components["schemas"]["NeedsYouItem"];
export type KindSummary = components["schemas"]["KindSummary"];
export type KindCalibration = components["schemas"]["KindCalibration"];

export const AUTOPILOT_TABS = [
  "needs-you",
  "decisions",
  "kinds",
  "calibration",
] as const;
export type AutopilotTab = (typeof AUTOPILOT_TABS)[number];

export const TAB_LABELS: Record<AutopilotTab, string> = {
  "needs-you": "Needs you",
  decisions: "Decisions",
  kinds: "What it handles",
  calibration: "How sure vs right",
};

/** The tab named in the URL, "needs-you" when it is missing or unknown. */
export function parseTab(value: string | null): AutopilotTab {
  return (AUTOPILOT_TABS as readonly string[]).includes(value ?? "")
    ? (value as AutopilotTab)
    : "needs-you";
}

const MODE_LABELS: Record<KindSummary["mode"], string> = {
  off: "Off",
  shadow: "Watching",
  auto: "Acting",
};

const MODE_HELP: Record<KindSummary["mode"], string> = {
  off: "Not run.",
  shadow:
    "Decides and logs, changes nothing. Checked against your own calls and a second model before it may act.",
  auto: "Applies decisions it is confident about. Every one can be undone.",
};

export function modeLabel(mode: KindSummary["mode"]): string {
  return MODE_LABELS[mode];
}

export function modeHelp(mode: KindSummary["mode"]): string {
  return MODE_HELP[mode];
}

const STATUS_LABELS: Record<AutoDecision["status"], string> = {
  applied: "Applied",
  shadow: "Logged only",
  escalated: "Waiting for you",
  reverted: "Undone",
  closed: "Settled elsewhere",
};

export function statusLabel(status: AutoDecision["status"]): string {
  return STATUS_LABELS[status];
}

/** "rule:listing-exact-match" -> "Rule listing-exact-match"; "model:gpt-6-luna" -> "gpt-6-luna". */
export function deciderLabel(decidedBy: string): string {
  if (decidedBy.startsWith("rule:")) return `Rule ${decidedBy.slice(5)}`;
  if (decidedBy.startsWith("model:")) return decidedBy.slice(6);
  if (decidedBy === "ttl") return "Expired unanswered";
  if (decidedBy === "owner-accepted") return "You accepted";
  return decidedBy;
}

/** 0.93 -> "93%"; null (a rule or an expiry has no confidence) -> "—". */
export function confidenceLabel(confidence: number | null): string {
  return confidence === null ? "—" : `${Math.round(confidence * 100)}%`;
}

/** Share of reviewed decisions that were right: "96% of 52 checked", or a plain "Not checked yet". */
export function precisionLabel(kind: {
  precision: number | null;
  reviewed: number;
}): string {
  if (kind.precision === null || kind.reviewed === 0) return "Not checked yet";
  return `${Math.round(kind.precision * 100)}% of ${kind.reviewed} checked`;
}

/** An escalated decision can be undone never; only an applied one that kept its revert data. */
export function canUndo(
  d: Pick<AutoDecision, "status" | "revert_payload">,
): boolean {
  return d.status === "applied" && d.revert_payload !== null;
}

/** "today", "1 day", "5 days". */
export function waitingLabel(ageDays: number): string {
  if (ageDays <= 0) return "today";
  return ageDays === 1 ? "1 day" : `${ageDays} days`;
}
