import type { components } from "@/lib/api/schema";

import { apiToTyped, type FilterDraft, type MetricInfo } from "./discover";

/*
 * Plain-English screens and starter templates (insight I10.3, I10.7): both arrive as filters in
 * API units and become the filter builder's editable conditions, so nothing runs that the owner
 * cannot see and change first.
 */

export type ScreenDraft = components["schemas"]["ScreenDraft"];
export type ParseResult = components["schemas"]["ParseResult"];
export type TemplateOut = components["schemas"]["TemplateOut"];
export type TemplatesOut = components["schemas"]["TemplatesOut"];

type ApiLike = { metric: string; op: FilterDraft["op"]; value: number | string | string[] };

function typed(value: number | string | string[], op: FilterDraft["op"], unit: string | undefined): string {
  if (op === "in" || Array.isArray(value)) return (Array.isArray(value) ? value : [value]).join(", ");
  const n = Number(value);
  return Number.isFinite(n) ? String(apiToTyped(n, unit ?? "")) : String(value);
}

/** API-unit filters as builder conditions (fractions become typed percentages). */
export function toDrafts(filters: readonly ApiLike[], catalogue: readonly MetricInfo[], nextId: () => number): FilterDraft[] {
  return filters.map((f) => {
    const unit = catalogue.find((m) => m.id === f.metric)?.unit;
    return { id: nextId(), metric: f.metric, op: f.op, value: typed(f.value, f.op, unit) };
  });
}

/** The parsed draft's filters in API units: markets and industries as category filters first. */
export function draftFilters(draft: ScreenDraft): ApiLike[] {
  const out: ApiLike[] = [];
  if (draft.markets?.length) out.push({ metric: "country", op: "in", value: draft.markets });
  if (draft.industries?.length) out.push({ metric: "sector", op: "in", value: draft.industries });
  for (const f of draft.filters ?? []) out.push({ metric: f.metric_id, op: f.op, value: f.value as number | string | string[] });
  return out;
}

const OP_WORD = { gte: "at least", lte: "at most", in: "is" } as const;

/** "Dividend yield at least 4 %": one readable chip per condition. */
export function chipText(f: ApiLike, catalogue: readonly MetricInfo[]): string {
  const info = catalogue.find((m) => m.id === f.metric);
  const label = info?.label ?? f.metric;
  const v = typed(f.value, f.op, info?.unit);
  const suffix = info?.unit === "fraction" && f.op !== "in" ? " %" : info?.unit === "multiple" ? "×" : "";
  return `${label} ${OP_WORD[f.op]} ${v}${suffix}`;
}

/** `?template=quality-payers` and `quality_payers` name the same template. */
export function templateId(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const id = raw.trim().toLowerCase().replaceAll("-", "_");
  return /^[a-z0-9_]{2,40}$/.test(id) ? id : null;
}

/** Slider value of a metric's gte/lte conditions, in typed units. */
export function rangeOf(drafts: readonly FilterDraft[], metric: string): [number | null, number | null] {
  const num = (op: "gte" | "lte") => {
    const d = drafts.find((x) => x.metric === metric && x.op === op);
    const n = d && d.value.trim() !== "" ? Number(d.value) : NaN;
    return Number.isFinite(n) ? n : null;
  };
  return [num("gte"), num("lte")];
}

/**
 * Set a metric's range from the slider: the gte and lte conditions are created, updated or
 * removed so the builder always shows exactly what the slider says. The condition the slider
 * belongs to stays (with an empty value) so its row does not vanish under the pointer.
 */
export function setRange(
  drafts: readonly FilterDraft[],
  owner: FilterDraft,
  [lo, hi]: [number | null, number | null],
  nextId: () => number,
): FilterDraft[] {
  let out = [...drafts];
  for (const [op, v] of [["gte", lo], ["lte", hi]] as const) {
    const existing = out.find((x) => x.metric === owner.metric && x.op === op);
    if (v === null) {
      if (existing && existing.id !== owner.id) out = out.filter((x) => x.id !== existing.id);
      else if (existing) out = out.map((x) => (x.id === existing.id ? { ...x, value: "" } : x));
    } else if (existing) {
      out = out.map((x) => (x.id === existing.id ? { ...x, value: String(v) } : x));
    } else {
      const at = out.findIndex((x) => x.id === owner.id);
      out.splice(at + 1, 0, { id: nextId(), metric: owner.metric, op, value: String(v) });
    }
  }
  return out;
}

/** Ticks in typed units (percent for fractions), as the slider shows them. */
export function typedTicks(info: MetricInfo | undefined): number[] | null {
  const raw = info?.ticks;
  if (!raw || raw.length < 2) return null;
  return raw.map((t) => apiToTyped(Number(t), info.unit));
}
