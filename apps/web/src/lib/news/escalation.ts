import type { EscalationBody, EscalationOut, EscalationSource, NoteSentence } from "@/lib/api/client";

import { hostOf, type CitationPreview } from "./digest";

/*
 * Display rules for escalations (impact notes on a confirmed severity-2+ story or a large
 * price move): numbered citations, the thesis link, whether a model assessed it, and the
 * price move that triggered it.
 */

export const ESCALATIONS_HREF = "/escalations";

export function escalationHref(id: number): string {
  return `${ESCALATIONS_HREF}/${id}`;
}

export function escalationsForTicker(ticker: string): string {
  return `${ESCALATIONS_HREF}?ticker=${encodeURIComponent(ticker)}`;
}

export const TRIGGER_LABELS: Record<EscalationBody["trigger"], string> = {
  story: "Confirmed story",
  price_move: "Price move",
};

export const SOURCE_KIND_LABELS: Record<NonNullable<EscalationSource["kind"]>, string> = {
  story: "Story source",
  search: "Found by search",
  price: "Price data",
};

export type EscalationStatus =
  | { kind: "assessed"; label: string }
  | { kind: "not_assessed"; label: string; reason: string };

/**
 * Whether a model wrote the note. Without one (no API key, or the call failed) the note
 * only states the event and its sources, and says why.
 */
export function escalationStatus(body: Pick<EscalationBody, "writer" | "thesis_link" | "rationale">): EscalationStatus {
  if (body.writer) return { kind: "assessed", label: `Assessed by ${body.writer}` };
  const reason = body.thesis_link.replace(/^not assessed:\s*/i, "") || body.rationale || "no model note";
  return { kind: "not_assessed", label: "Not assessed", reason };
}

export type Citation = { url: string; number: number | null; title: string };

/** A sentence's sources, numbered by their place in the note's source list. */
export function sentenceCitations(sentence: Pick<NoteSentence, "source_urls">, sources: EscalationSource[]): Citation[] {
  const index = new Map(sources.map((s, i) => [s.url, i]));
  return (sentence.source_urls ?? []).map((url) => {
    const i = index.get(url);
    const source = i === undefined ? undefined : sources[i];
    return {
      url,
      number: i === undefined ? null : i + 1,
      title: source ? [source.headline, source.outlet].filter(Boolean).join(" · ") || url : url,
    };
  });
}

/** The preview of an impact-note citation: the stored snippet the sentence was checked against. */
export function noteCitationPreview(sources: EscalationSource[], url: string): CitationPreview | null {
  const source = sources.find((s) => s.url === url);
  if (!source) return null;
  return {
    kind: "note",
    title: source.headline?.trim() || source.outlet || hostOf(url),
    snippet: source.snippet?.trim() || null,
    tier: typeof source.tier === "number" ? source.tier : null,
    tierLabel: typeof source.tier === "number" ? `Tier ${source.tier}` : "Tier unknown",
    source: source.outlet ?? hostOf(url),
    url,
    relation: null,
    provenance: null,
  };
}

export type ThesisLink =
  | { kind: "none"; text: string }
  | { kind: "linked"; kpis: { id: string; description: string }[]; exitConditions: string[] };

export function thesisLink(body: Pick<EscalationBody, "none_applies" | "kpis" | "exit_conditions" | "thesis_link">): ThesisLink {
  const kpis = (body.kpis ?? [])
    .filter((k) => typeof k.id === "string")
    .map((k) => ({ id: k.id, description: k.description ?? "" }));
  if (body.none_applies || (kpis.length === 0 && body.exit_conditions.length === 0)) {
    const text = body.thesis_link.replace(/^none:\s*/i, "");
    return { kind: "none", text: text ? text.charAt(0).toUpperCase() + text.slice(1) : "No thesis KPI or exit condition applies" };
  }
  return { kind: "linked", kpis, exitConditions: body.exit_conditions };
}

export type PriceMove = { date: string; movePct: number; symbol: string | null };

/** The free-form `price_move` record the price-history job writes ({date, move_pct, symbol}). */
export function parsePriceMove(raw: EscalationBody["price_move"]): PriceMove | null {
  if (!raw || typeof raw !== "object") return null;
  const move = raw.move_pct;
  if (typeof raw.date !== "string" || typeof move !== "number" || !Number.isFinite(move)) return null;
  return { date: raw.date, movePct: move, symbol: typeof raw.symbol === "string" && raw.symbol ? raw.symbol : null };
}

/** "+9.2 %" / "−8.4 %" from a value already in percent. */
export function formatMovePct(pct: number, locale?: string): string {
  const text = new Intl.NumberFormat(locale, {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
    signDisplay: "exceptZero",
  }).format(pct);
  return `${text} %`;
}

// ---------------------------------------------------------------------------
// Lifecycle: open -> acknowledged -> resolved (and back to open)
// ---------------------------------------------------------------------------

export type EscalationState = EscalationOut["status"];

export const ESCALATION_STATES = ["open", "acknowledged", "resolved"] as const satisfies readonly EscalationState[];

export const ESCALATION_STATE_LABELS: Record<EscalationState, string> = {
  open: "Open",
  acknowledged: "Acknowledged",
  resolved: "Resolved",
};

export type StatusTab = EscalationState | "all";

/** The status tab from the URL (?status=…); open by default, as in the design. */
export function parseStatusTab(value: string | null): StatusTab {
  return value === "all" || (ESCALATION_STATES as readonly string[]).includes(value ?? "") ? (value as StatusTab) : "open";
}

export type EscalationAction = { to: EscalationState; label: string };

/** What the owner can do next from each state. */
export function escalationActions(status: EscalationState): EscalationAction[] {
  switch (status) {
    case "open":
      return [
        { to: "acknowledged", label: "Acknowledge" },
        { to: "resolved", label: "Resolve" },
      ];
    case "acknowledged":
      return [
        { to: "resolved", label: "Resolve" },
        { to: "open", label: "Reopen" },
      ];
    case "resolved":
      return [{ to: "open", label: "Reopen" }];
  }
}

/** Compact age: "45m", "3h", "2d". */
export function ageLabel(createdAt: string, now: Date): string {
  const minutes = Math.max(0, Math.floor((now.getTime() - Date.parse(createdAt)) / 60_000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

/** Minutes as "2h 10m", "45m", "3d 4h". */
export function formatMinutes(minutes: number): string {
  const m = Math.max(0, Math.round(minutes));
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return m % 60 ? `${h}h ${m % 60}m` : `${h}h`;
  const d = Math.floor(h / 24);
  return h % 24 ? `${d}d ${h % 24}h` : `${d}d`;
}

export type AckTime = { id: number; createdAt: string; minutes: number };

/** Time from written to first acknowledged, per escalation that has one, oldest first. */
export function ackTimes(items: readonly Pick<EscalationOut, "id" | "created_at" | "acknowledged_at">[]): AckTime[] {
  return items
    .filter((e) => e.acknowledged_at)
    .map((e) => ({
      id: e.id,
      createdAt: e.created_at,
      minutes: Math.max(0, (Date.parse(e.acknowledged_at as string) - Date.parse(e.created_at)) / 60_000),
    }))
    .sort((a, b) => a.createdAt.localeCompare(b.createdAt) || a.id - b.id);
}

export type WeekTotal = { start: string; opened: number; acknowledged: number };

/**
 * Sums the stats' per-day counts into blocks of `size` days ending on the last day, so the
 * newest block is always a full week; a shorter oldest block is dropped.
 */
export function weeklyTotals(
  perDay: readonly { date: string; opened: number; acknowledged: number }[],
  size = 7,
): WeekTotal[] {
  const out: WeekTotal[] = [];
  for (let end = perDay.length; end - size >= 0; end -= size) {
    const block = perDay.slice(end - size, end);
    out.unshift({
      start: block[0].date,
      opened: block.reduce((n, d) => n + d.opened, 0),
      acknowledged: block.reduce((n, d) => n + d.acknowledged, 0),
    });
  }
  return out;
}

/** Replace one escalation in a list (after a PATCH), keeping the order. */
export function replaceEscalation<T extends { id: number }>(items: readonly T[], updated: T): T[] {
  return items.map((e) => (e.id === updated.id ? updated : e));
}
