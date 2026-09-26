import { ALERT_KIND_LABELS, alertEventLabel } from "@/lib/activity/activity";
import type { AlertKind, ReplayChange, ReplayOutcome, ReplayPreview } from "@/lib/api/client";
import { CHANNEL_LABELS } from "@/lib/config/news-forms";
import { formatDateTime } from "@/lib/format";
import { eventLabel, SEVERITY, type SeverityLevel } from "@/lib/news/labels";

/*
 * The replay preview of a rule change (`POST /config/event-rules/preview`, `POST
 * /config/notifications/preview`): the last 7 days of stored events routed under the active
 * config and under the draft, and every event whose alert outcome differs. The editors show
 * this diff before a save is confirmed.
 */

export const PREVIEW_DOMAIN_LABELS: Record<ReplayPreview["domain"], string> = {
  event_rules: "event rules",
  notifications: "notification rules",
};

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** "Replayed 42 events from 18 Sep to 25 Sep: 5 alerts → 7 alerts (+2); 3 events change." */
export function previewSummary(preview: ReplayPreview, locale?: string): string {
  const delta = preview.alerts_after - preview.alerts_before;
  const deltaText = delta === 0 ? "no change in count" : `${delta > 0 ? "+" : "−"}${Math.abs(delta)}`;
  const changes = preview.changes.length;
  return (
    `Replayed ${plural(preview.events, "event")} from ${formatDateTime(preview.window_start, { locale })} to ${formatDateTime(preview.window_end, { locale })}: ` +
    `${plural(preview.alerts_before, "alert")} → ${plural(preview.alerts_after, "alert")} (${deltaText}); ` +
    (changes === 0 ? "no event's outcome changes." : `${plural(changes, "event")} ${changes === 1 ? "changes" : "change"}.`)
  );
}

export function severityText(severity: number): string {
  const level = Math.max(0, Math.min(3, severity)) as SeverityLevel;
  return `${severity} · ${SEVERITY[level].label}`;
}

export function channelsText(channels: readonly string[]): string {
  if (channels.length === 0) return "no channel";
  return channels.map((c) => CHANNEL_LABELS[c as keyof typeof CHANNEL_LABELS] ?? c).join(", ");
}

/** Whether routing would send (or hold) an alert for this outcome. */
export function alerts(outcome: ReplayOutcome): boolean {
  return outcome.channels.length > 0 && !outcome.suppressed;
}

export type OutcomeView = {
  severity: string;
  decision: string;
  channels: string;
  heldUntil: string | null;
  suppressed: string | null;
};

export function outcomeView(outcome: ReplayOutcome, locale?: string): OutcomeView {
  return {
    severity: severityText(outcome.severity),
    decision: outcome.decision,
    channels: channelsText(outcome.channels),
    heldUntil: outcome.held_until ? formatDateTime(outcome.held_until, { locale }) : null,
    suppressed: outcome.suppressed ?? null,
  };
}

export type ChangeField = "severity" | "channels" | "held_until" | "suppressed" | "decision";

/** The fields that differ between the two outcomes, in display order. */
export function changedFields(change: Pick<ReplayChange, "before" | "after">): ChangeField[] {
  const { before: b, after: a } = change;
  const fields: ChangeField[] = [];
  if (b.severity !== a.severity) fields.push("severity");
  if ([...b.channels].sort().join() !== [...a.channels].sort().join()) fields.push("channels");
  if ((b.held_until ?? null) !== (a.held_until ?? null)) fields.push("held_until");
  if ((b.suppressed ?? null) !== (a.suppressed ?? null)) fields.push("suppressed");
  if (b.decision !== a.decision) fields.push("decision");
  return fields;
}

export type ChangeEffect = "new_alert" | "no_longer_alerts" | "changed";

/** "Would now alert", "Would no longer alert" or "Alert changes". */
export function changeEffect(change: Pick<ReplayChange, "before" | "after">): { kind: ChangeEffect; label: string } {
  const before = alerts(change.before);
  const after = alerts(change.after);
  if (!before && after) return { kind: "new_alert", label: "Would now alert" };
  if (before && !after) return { kind: "no_longer_alerts", label: "Would no longer alert" };
  return { kind: "changed", label: "Alert changes" };
}

export type ChangeRow = {
  key: string;
  at: string;
  headline: string;
  ticker: string | null;
  event: string;
  effect: { kind: ChangeEffect; label: string };
  changed: ChangeField[];
  before: OutcomeView;
  after: OutcomeView;
};

/** A story's event type; for another alert its kind and event ("Monitoring · Missed run"). */
export function eventText(change: Pick<ReplayChange, "key" | "kind" | "event_type">): string {
  if (change.key.startsWith("story:")) return eventLabel(change.event_type);
  const kind = change.kind as AlertKind;
  const label = ALERT_KIND_LABELS[kind] ?? change.kind.replaceAll("_", " ");
  if (!change.event_type) return label;
  const event = alertEventLabel({ kind, event_type: change.event_type });
  return event === label ? label : `${label} · ${event}`;
}

/** One row per changed event, newest first. */
export function changeRows(preview: ReplayPreview, locale?: string): ChangeRow[] {
  return [...preview.changes]
    .sort((x, y) => y.at.localeCompare(x.at) || x.key.localeCompare(y.key))
    .map((c) => ({
      key: c.key,
      at: formatDateTime(c.at, { locale }),
      headline: c.headline,
      ticker: c.ticker,
      event: eventText(c),
      effect: changeEffect(c),
      changed: changedFields(c),
      before: outcomeView(c.before, locale),
      after: outcomeView(c.after, locale),
    }));
}
