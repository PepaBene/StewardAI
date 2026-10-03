import type { components } from "@/lib/api/schema";
import { toNumber } from "@/lib/source-coverage/parse";
import type { CoverageEntry } from "@/lib/source-coverage/types";

import { VALUATION_METRICS } from "./metrics";
import type { HeldInput, LedgerGap, PriceInput, RatioFigure, ValuationInput, MetricId } from "./types";

type Schemas = components["schemas"];

export function adaptHeld(h: Schemas["Holding"]): HeldInput {
  return { ticker: h.ticker, name: h.name ?? null, weight: toNumber(h.weight), pplPct: toNumber(h.ppl_pct) };
}

export function adaptPrice(p: Schemas["HoldingPrices"]): PriceInput {
  return { ticker: p.ticker, dayChangePct: toNumber(p.day_change_pct), asOf: p.as_of ?? null };
}

/** `GET /holdings/{ticker}/valuation` -> the figures a screen can use. */
export function adaptValuation(v: Schemas["HoldingValuation"]): ValuationInput {
  const ratios: Partial<Record<MetricId, RatioFigure>> = {};
  for (const id of VALUATION_METRICS) {
    const r = (v as unknown as Record<string, Schemas["ValuationRatio"] | undefined>)[id];
    if (r) ratios[id] = { value: toNumber(r.value), reason: r.reason ?? null };
  }
  const errors = Object.entries(v.errors ?? {}).map(([k, e]) => `${k}: ${typeof e === "string" ? e : JSON.stringify(e)}`);
  const anyRatio = Object.values(ratios).some((r) => r.value !== null);
  return {
    kind: "ok",
    ratios,
    priceStale: v.price ? v.price.stale : null,
    epsStale: v.eps_ttm ? v.eps_ttm.stale : null,
    fundStale: v.fund ? v.fund.stale : null,
    bvpsStale: v.book_value_per_share ? v.book_value_per_share.stale : null,
    dpsStale: v.dividend_per_share ? v.dividend_per_share.stale : null,
    priceDate: v.price?.date ?? null,
    checkedAt: v.checked_at ?? null,
    sourceLabel: v.fundamentals?.source_label ?? null,
    errors,
    hasAnyData: v.price !== null && v.price !== undefined ? true : v.fundamentals !== null && v.fundamentals !== undefined ? true : anyRatio,
  };
}

/**
 * Coverage-ledger entries in a gap state, per upper-cased security entity key. A security's
 * ledger key is its Trading212 ticker or its price symbol, so `aliases` (price symbol -> ticker,
 * both upper-cased) files an entry stored under the price symbol beside the ticker's.
 */
export function ledgerGaps(entries: readonly CoverageEntry[], aliases: ReadonlyMap<string, string> = new Map()): Map<string, LedgerGap[]> {
  const map = new Map<string, LedgerGap[]>();
  for (const e of entries) {
    if (e.entityKind !== "security" || (e.rawState !== "paid_only" && e.rawState !== "not_covered")) continue;
    const raw = e.entityKey.toUpperCase();
    const key = aliases.get(raw) ?? raw;
    map.set(key, [...(map.get(key) ?? []), { provider: e.provider, dataset: e.dataset, rawState: e.rawState, reason: e.reason }]);
  }
  return map;
}
