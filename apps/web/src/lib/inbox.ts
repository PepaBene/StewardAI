import type { components } from "@/lib/api/schema";

export type InboxItem = components["schemas"]["InboxItem"];
export type AgentNote = components["schemas"]["AgentNote"];
export type RouteStat = components["schemas"]["RouteStat"];

export type MissedEvent = components["schemas"]["MissedEvent"];
export type NoteVersion = components["schemas"]["NoteVersion"];

const KIND_LABELS: Record<string, string> = {
  escalation: "Escalation note",
  alert: "Thesis alert",
  disclosure: "Disclosure alert",
  breach: "Portfolio breach",
  verdict: "KPI breach",
};

/** What kind of event started the run, in plain words. */
export function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind;
}

export type NoteSuggestion = {
  text: string;
  status: "suggested" | "accepted" | "declined";
};

/** The working note the agent suggested on this item and what the owner did with it. */
export function noteSuggestionOf(
  item: Pick<InboxItem, "body">,
): NoteSuggestion | null {
  const body = item.body as { suggested_note?: unknown; note_status?: unknown };
  if (typeof body.suggested_note !== "string" || !body.suggested_note.trim())
    return null;
  const status = body.note_status;
  return {
    text: body.suggested_note,
    status:
      status === "accepted" || status === "declined" ? status : "suggested",
  };
}

export const INBOX_TABS = ["new", "all", "dismissed"] as const;
export type InboxTab = (typeof INBOX_TABS)[number];

export const TAB_LABELS: Record<InboxTab, string> = {
  new: "To look at",
  all: "All",
  dismissed: "Dismissed",
};

/** The tab named in the URL, "new" when it is missing or unknown. */
export function parseTab(value: string | null): InboxTab {
  return (INBOX_TABS as readonly string[]).includes(value ?? "")
    ? (value as InboxTab)
    : "new";
}

/** The API `status` filter for a tab: "To look at" is the items still new. */
export function tabStatus(tab: InboxTab): "new" | "dismissed" | undefined {
  return tab === "all" ? undefined : tab;
}

const ACTION_LABELS: Record<string, string> = {
  research: "Run a research report",
  earnings_check: "Re-run the earnings check",
  portfolio_sync: "Run the portfolio sync",
};

/** What confirming the item's proposed action will start, in plain words. */
export function actionLabel(
  action: Record<string, unknown> | null,
): string | null {
  if (!action) return null;
  const kind = String(action.kind ?? "");
  const params = (action.params ?? {}) as Record<string, unknown>;
  const base = ACTION_LABELS[kind] ?? kind;
  const ticker =
    typeof params.ticker === "string" ? ` for ${params.ticker}` : "";
  const profile =
    typeof params.profile === "string" ? ` (${params.profile})` : "";
  return `${base}${ticker}${profile}`;
}

export type ActionState = "open" | "confirmed" | "declined" | "none";

export function actionState(
  item: Pick<InboxItem, "proposed_action" | "action_status">,
): ActionState {
  if (!item.proposed_action || !item.action_status) return "none";
  return item.action_status === "proposed" ? "open" : item.action_status;
}

export interface BodyStatement {
  text: string;
  source_ids: string[];
}

/** The checked statements of an item's body, tolerating a body that predates a field. */
export function statementsOf(item: Pick<InboxItem, "body">): BodyStatement[] {
  const raw = (item.body as { statements?: unknown }).statements;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((s): s is BodyStatement => typeof s?.text === "string")
    .map((s) => ({
      text: s.text,
      source_ids: Array.isArray(s.source_ids) ? s.source_ids.map(String) : [],
    }));
}

export interface BodySource {
  id: string;
  title: string;
  url: string | null;
  app_path: string | null;
}

export function sourcesOf(
  item: Pick<InboxItem, "body">,
): Map<string, BodySource> {
  const raw = (item.body as { sources?: unknown }).sources;
  const out = new Map<string, BodySource>();
  if (!Array.isArray(raw)) return out;
  for (const s of raw) {
    if (typeof s?.id === "string") {
      out.set(s.id, {
        id: s.id,
        title: String(s.title ?? s.id),
        url: s.url ?? null,
        app_path: s.app_path ?? null,
      });
    }
  }
  return out;
}

/** The agent's own words on the item: assessment, or why there is none. */
export function assessmentOf(item: Pick<InboxItem, "body">): string {
  const text = (item.body as { assessment?: unknown }).assessment;
  return typeof text === "string" ? text : "";
}

export function touchesOf(item: Pick<InboxItem, "body">): string[] {
  const raw = (item.body as { touches?: unknown }).touches;
  return Array.isArray(raw) ? raw.map(String) : [];
}

export function formatUsd(value: number | null): string {
  if (value === null) return "none";
  return value < 0.01 && value > 0
    ? "<$0.01"
    : `$${value.toFixed(value < 1 ? 3 : 2)}`;
}

export function formatSeconds(value: number | null): string {
  if (value === null) return "none";
  if (value < 90) return `${Math.round(value)} s`;
  return `${Math.round(value / 60)} min`;
}
