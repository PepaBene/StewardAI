import type { components } from "@/lib/api/schema";

type Schemas = components["schemas"];

/*
 * Holdings presets (insight plan I9.2-I9.3): one registry of every column the holdings table can
 * show, the fixed presets built from it, and the owner's custom order. The ids match
 * `COLUMN_IDS` in the backend `holdings_views` config, which validates a saved custom view.
 */

export type HoldingInsight = Schemas["HoldingInsight"];
export type HoldingsInsight = Schemas["HoldingsInsight"];
export type HoldingNote = Schemas["HoldingNote"];

export type ColumnFormat = "percent" | "multiple" | "money" | "text" | "custom";
export type ColumnGroup = "Position" | "Check-in" | "Income" | "Score" | "Fundamentals" | "Value";

export type ColumnDef = {
  id: ColumnId;
  label: string;
  group: ColumnGroup;
  align: "left" | "right" | "center";
  /** The insight metric the cell reads, when it is a plain number. */
  metric?: string;
  format: ColumnFormat;
  sortable: boolean;
  /** One sentence for the column picker. */
  help: string;
};

export const COLUMN_IDS = [
  "holding", "quantity", "avg_price", "price", "day_move", "spark", "value", "weight", "drift", "ppl",
  "last_event", "fair_vs_price", "fair_value", "thesis", "fair_spark",
  "yield", "yield_on_cost", "next_payment", "income_12m", "dividend_quality",
  "mark", "score_value", "score_future", "score_past", "score_health", "score_dividend",
  "net_margin", "operating_margin", "gross_margin", "past_growth", "forecast_growth", "revenue_growth",
  "debt_to_equity", "net_debt_to_equity", "roce", "roe", "roic",
  "pe", "forward_pe", "pb", "ps", "fcf_yield", "ev_ebit", "earnings_yield",
  "return_1y", "volatility", "momentum", "share_change", "market_cap", "notes",
] as const;
export type ColumnId = (typeof COLUMN_IDS)[number];

const c = (
  id: ColumnId,
  label: string,
  group: ColumnGroup,
  format: ColumnFormat,
  help: string,
  extra: Partial<ColumnDef> = {},
): ColumnDef => ({ id, label, group, format, help, align: format === "text" ? "left" : "right", sortable: true, ...extra });

export const COLUMNS: Record<ColumnId, ColumnDef> = {
  holding: c("holding", "Holding", "Position", "custom", "Name and ticker; always shown first.", { align: "left" }),
  quantity: c("quantity", "Qty", "Position", "custom", "Shares held."),
  avg_price: c("avg_price", "Avg price", "Position", "custom", "Average price paid, in the instrument's currency."),
  price: c("price", "Price", "Position", "custom", "Latest price with today's move."),
  day_move: c("day_move", "Today", "Position", "percent", "Today's move against the previous close."),
  spark: c("spark", "30 days", "Position", "custom", "Daily closes over the last 30 days.", { align: "center", sortable: false }),
  value: c("value", "Value", "Position", "custom", "Market value in the base currency."),
  weight: c("weight", "Weight vs target", "Position", "custom", "Share of the account (cash included, unless the strategy weighs positions only) against the target weight.", { align: "left" }),
  drift: c("drift", "Drift", "Position", "custom", "Distance from the target weight, with its band."),
  ppl: c("ppl", "Unrealised P/L", "Position", "custom", "Gain or loss against cost."),
  last_event: c("last_event", "Last event", "Check-in", "custom", "The newest verdict, company event, dividend or news story.", { align: "left" }),
  fair_vs_price: c("fair_vs_price", "Price vs fair value", "Value", "custom", "How far the price sits below (or above) the chosen fair value."),
  fair_value: c("fair_value", "Fair value", "Value", "custom", "The chosen fair-value basis, in the price currency."),
  thesis: c("thesis", "Thesis", "Check-in", "text", "The first sentence of why you own it.", { sortable: false }),
  fair_spark: c("fair_spark", "Price and fair value", "Value", "custom", "30 days of closes with the recorded fair value as a step line.", { align: "center", sortable: false }),
  yield: c("yield", "Yield", "Income", "percent", "Forward dividend yield on today's price."),
  yield_on_cost: c("yield_on_cost", "Yield on cost", "Income", "percent", "Forward dividend yield on what you paid."),
  next_payment: c("next_payment", "Next payment", "Income", "custom", "Date and amount of the next dividend (announced or estimated)."),
  income_12m: c("income_12m", "Income, next 12 m", "Income", "custom", "Dividends expected over the next twelve months."),
  dividend_quality: c("dividend_quality", "Dividend checks", "Income", "custom", "How many of the dividend quality checks pass."),
  mark: c("mark", "Score", "Score", "custom", "The five-axis score mark.", { align: "center", sortable: false }),
  score_value: c("score_value", "Value", "Score", "custom", "Value checks passed, of 6."),
  score_future: c("score_future", "Future", "Score", "custom", "Future checks passed, of 6."),
  score_past: c("score_past", "Past", "Score", "custom", "Past checks passed, of 6."),
  score_health: c("score_health", "Health", "Score", "custom", "Health checks passed, of 6."),
  score_dividend: c("score_dividend", "Dividend", "Score", "custom", "Dividend checks passed, of 6."),
  net_margin: c("net_margin", "Net margin", "Fundamentals", "percent", "Net income over revenue, last twelve months.", { metric: "net_margin" }),
  operating_margin: c("operating_margin", "Operating margin", "Fundamentals", "percent", "Operating income over revenue.", { metric: "operating_margin" }),
  gross_margin: c("gross_margin", "Gross margin", "Fundamentals", "percent", "Gross profit over revenue.", { metric: "gross_margin" }),
  past_growth: c("past_growth", "Past EPS growth", "Fundamentals", "percent", "Earnings growth over the past five years, a year.", { metric: "past_growth" }),
  forecast_growth: c("forecast_growth", "Forecast growth", "Fundamentals", "percent", "Forecast annual earnings growth, where estimates are stored.", { metric: "forecast_growth" }),
  revenue_growth: c("revenue_growth", "Revenue growth", "Fundamentals", "percent", "Revenue growth over the last year.", { metric: "revenue_growth" }),
  debt_to_equity: c("debt_to_equity", "Debt / equity", "Fundamentals", "percent", "Total debt over shareholders' equity.", { metric: "debt_to_equity" }),
  net_debt_to_equity: c("net_debt_to_equity", "Net debt / equity", "Fundamentals", "percent", "Debt less cash over equity.", { metric: "net_debt_to_equity" }),
  roce: c("roce", "ROCE", "Fundamentals", "percent", "Return on capital employed.", { metric: "roce" }),
  roe: c("roe", "ROE", "Fundamentals", "percent", "Return on equity.", { metric: "roe" }),
  roic: c("roic", "ROIC", "Fundamentals", "percent", "Return on invested capital.", { metric: "roic" }),
  pe: c("pe", "P/E", "Value", "multiple", "Price over trailing earnings per share.", { metric: "pe_ratio" }),
  forward_pe: c("forward_pe", "Forward P/E", "Value", "multiple", "Price over next year's estimated earnings.", { metric: "forward_pe" }),
  pb: c("pb", "P/B", "Value", "multiple", "Price over book value per share.", { metric: "price_to_book" }),
  ps: c("ps", "P/S", "Value", "multiple", "Market value over revenue.", { metric: "price_to_sales" }),
  fcf_yield: c("fcf_yield", "FCF yield", "Value", "percent", "Free cash flow over market value.", { metric: "fcf_yield" }),
  ev_ebit: c("ev_ebit", "EV / EBIT", "Value", "multiple", "Enterprise value over operating profit.", { metric: "ev_ebit" }),
  earnings_yield: c("earnings_yield", "Earnings yield", "Value", "percent", "Operating profit over enterprise value.", { metric: "earnings_yield" }),
  return_1y: c("return_1y", "1y return", "Fundamentals", "percent", "Price return over the last year.", { metric: "return_1y" }),
  volatility: c("volatility", "Volatility", "Fundamentals", "percent", "Annualised volatility of daily returns, one year.", { metric: "volatility_1y" }),
  momentum: c("momentum", "Momentum 12-1", "Fundamentals", "percent", "Twelve-month return excluding the last month.", { metric: "momentum_12_1" }),
  share_change: c("share_change", "Share count change", "Fundamentals", "percent", "Change in shares outstanding over a year (positive is dilution).", { metric: "share_change_1y" }),
  market_cap: c("market_cap", "Market cap", "Fundamentals", "custom", "Market value of the company.", { metric: "market_cap" }),
  notes: c("notes", "Notes", "Check-in", "custom", "Your notes on the holding; opens them.", { align: "center" }),
};

export type PresetKey = "check_in" | "holding" | "income" | "growth" | "value" | "score" | "fundamentals" | "custom";

export const PRESETS: { key: PresetKey; label: string; description: string; columns: readonly ColumnId[] }[] = [
  {
    key: "check_in",
    label: "Check-in",
    description: "What changed: the latest event, price against fair value and your thesis line.",
    columns: ["holding", "day_move", "last_event", "fair_vs_price", "thesis", "fair_spark", "notes"],
  },
  {
    key: "holding",
    label: "Holding",
    description: "The position: size, price path, weight against target and gain or loss.",
    columns: ["holding", "quantity", "avg_price", "price", "spark", "value", "weight", "drift", "ppl"],
  },
  {
    key: "income",
    label: "Income",
    description: "Dividends: yield on price and cost, the next payment and the year ahead.",
    columns: ["holding", "value", "yield", "yield_on_cost", "next_payment", "income_12m", "dividend_quality"],
  },
  {
    key: "growth",
    label: "Growth",
    description: "Past and forecast growth with the margins and returns behind them.",
    columns: ["holding", "price", "past_growth", "forecast_growth", "revenue_growth", "net_margin", "roe"],
  },
  {
    key: "value",
    label: "Value",
    description: "Price against fair value and the usual multiples.",
    columns: ["holding", "price", "fair_vs_price", "fair_value", "fair_spark", "pe", "forward_pe", "pb", "fcf_yield"],
  },
  {
    key: "score",
    label: "Score",
    description: "The five-axis score mark and each axis's checks passed.",
    columns: ["holding", "mark", "score_value", "score_future", "score_past", "score_health", "score_dividend"],
  },
  {
    key: "fundamentals",
    label: "Fundamentals",
    description: "Margins, past and forecast growth, leverage and returns on capital.",
    columns: ["holding", "market_cap", "net_margin", "past_growth", "forecast_growth", "debt_to_equity", "roce", "roe"],
  },
  { key: "custom", label: "Custom", description: "Your own columns, in your order.", columns: [] },
];

export const DEFAULT_CUSTOM: readonly ColumnId[] = ["holding", "price", "day_move", "value", "weight", "fair_vs_price", "mark", "notes"];
export const MAX_CUSTOM = 20;

export function isColumnId(v: string): v is ColumnId {
  return (COLUMN_IDS as readonly string[]).includes(v);
}

/** The columns a preset shows; custom falls back to the default order when nothing valid is saved. */
export function presetColumns(key: PresetKey, custom: readonly string[] | null | undefined): ColumnId[] {
  if (key !== "custom") return [...(PRESETS.find((p) => p.key === key)?.columns ?? [])];
  const valid = (custom ?? []).filter(isColumnId);
  const unique = valid.filter((id, i) => valid.indexOf(id) === i);
  if (!unique.includes("holding")) unique.unshift("holding");
  return unique.length > 1 ? unique.slice(0, MAX_CUSTOM) : [...DEFAULT_CUSTOM];
}

/** Move a column one step left or right; "holding" stays first. */
export function moveColumn(cols: readonly ColumnId[], id: ColumnId, step: -1 | 1): ColumnId[] {
  const out = [...cols];
  const i = out.indexOf(id);
  const j = i + step;
  if (i <= 0 || j <= 0 || j >= out.length) return out;
  [out[i], out[j]] = [out[j], out[i]];
  return out;
}

/** Drop `id` before `target` (drag and drop); "holding" stays first. */
export function dropColumn(cols: readonly ColumnId[], id: ColumnId, target: ColumnId): ColumnId[] {
  if (id === "holding" || id === target) return [...cols];
  const out = cols.filter((x) => x !== id);
  const at = Math.max(1, out.indexOf(target));
  out.splice(target === "holding" ? 1 : at, 0, id);
  return out;
}

export function toggleColumn(cols: readonly ColumnId[], id: ColumnId): ColumnId[] {
  if (id === "holding") return [...cols];
  if (cols.includes(id)) return cols.filter((x) => x !== id);
  return cols.length >= MAX_CUSTOM ? [...cols] : [...cols, id];
}

export function toNum(v: number | string | null | undefined): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

const SCORE_AXIS: Partial<Record<ColumnId, string>> = {
  score_value: "value",
  score_future: "future",
  score_past: "past",
  score_health: "health",
  score_dividend: "dividend",
};

/** A number from the insight for a metric or score column (null when it is missing). */
export function insightNumber(id: ColumnId, row: HoldingInsight | undefined): number | null {
  if (!row) return null;
  const def = COLUMNS[id];
  if (def.metric) return toNum(row.metrics?.[def.metric]);
  const axis = SCORE_AXIS[id];
  if (axis) return toNum(row.mark?.[axis]);
  switch (id) {
    case "fair_vs_price":
      return toNum(row.fair_upside);
    case "fair_value":
      return toNum(row.fair_value);
    case "yield":
      return toNum(row.yield_on_price);
    case "yield_on_cost":
      return toNum(row.yield_on_cost);
    case "income_12m":
      return toNum(row.income_12m);
    case "dividend_quality":
      return row.dividend_passes ?? null;
    case "notes":
      return row.notes;
    default:
      return null;
  }
}

/** Why a metric cell is empty, as the API stated it. */
export function missingReason(id: ColumnId, row: HoldingInsight | undefined): string | null {
  if (!row) return "Insight not loaded";
  const def = COLUMNS[id];
  if (def.metric) return row.metric_reasons?.[def.metric] ?? null;
  if (SCORE_AXIS[id]) return "Checks not computed yet";
  if (id === "fair_vs_price" || id === "fair_value") return row.fair_reason ?? null;
  if (id === "yield" || id === "yield_on_cost" || id === "income_12m")
    return row.pays_dividend === false ? "Pays no dividend" : "No dividend record";
  return null;
}

/**
 * Price closes joined with the fair value in force on each date (a step: the last recorded value
 * on or before the date). Without closes, the recorded fair values alone.
 */
export function priceWithFair(
  bars: readonly { date: string; close: number }[],
  history: readonly { as_of: string; value: number | string }[],
): { date: string; value: number | null; fair: number | null }[] {
  const steps = [...history].sort((a, b) => a.as_of.localeCompare(b.as_of));
  if (bars.length < 2) return steps.map((s) => ({ date: s.as_of, value: null, fair: toNum(s.value) }));
  let i = -1;
  return bars.map((b) => {
    while (i + 1 < steps.length && steps[i + 1].as_of <= b.date) i++;
    return { date: b.date, value: b.close, fair: i >= 0 ? toNum(steps[i].value) : null };
  });
}

export type SortState ={ id: ColumnId; dir: "asc" | "desc" };

export type RowSource = {
  ticker: string;
  name: string | null;
  quantity: number;
  avg_price: number;
  current_price: number;
  value_base: number;
  weight: number | null;
  drift: number | null;
  ppl: number;
};

/** The value a column sorts by: position fields from the holding, the rest from the insight. */
export function sortValue(
  id: ColumnId,
  h: RowSource,
  row: HoldingInsight | undefined,
  day: number | null,
): number | string | null {
  switch (id) {
    case "holding":
      return (h.name ?? h.ticker).toLowerCase();
    case "quantity":
      return h.quantity;
    case "avg_price":
      return h.avg_price;
    case "price":
    case "day_move":
      return day;
    case "value":
      return h.value_base;
    case "weight":
      return h.weight;
    case "drift":
      return h.drift;
    case "ppl":
      return h.ppl;
    case "last_event":
      return row?.last_event?.date ?? null;
    case "next_payment":
      return row?.next_payment?.date ?? null;
    default:
      return insightNumber(id, row);
  }
}

/** Sort rows by a column; missing values always sink to the bottom, whatever the direction. */
export function sortRows<T extends RowSource>(
  rows: readonly T[],
  sort: SortState,
  insight: ReadonlyMap<string, HoldingInsight>,
  day: (ticker: string) => number | null,
): T[] {
  const keyed = rows.map((h) => ({ h, v: sortValue(sort.id, h, insight.get(h.ticker), day(h.ticker)) }));
  const sign = sort.dir === "asc" ? 1 : -1;
  keyed.sort((a, b) => {
    if (a.v === null && b.v === null) return a.h.ticker.localeCompare(b.h.ticker);
    if (a.v === null) return 1;
    if (b.v === null) return -1;
    const cmp = typeof a.v === "string" || typeof b.v === "string" ? String(a.v).localeCompare(String(b.v)) : a.v - b.v;
    return cmp === 0 ? a.h.ticker.localeCompare(b.h.ticker) : cmp * sign;
  });
  return keyed.map((k) => k.h);
}

export function nextSortState(current: SortState, id: ColumnId): SortState {
  if (current.id !== id) return { id, dir: id === "holding" ? "asc" : "desc" };
  return { id, dir: current.dir === "asc" ? "desc" : "asc" };
}
