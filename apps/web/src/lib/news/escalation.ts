import type { EscalationBody, EscalationSource, NoteSentence } from "@/lib/api/client";

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
