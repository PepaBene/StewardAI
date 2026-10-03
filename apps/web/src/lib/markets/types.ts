import type { components } from "@/lib/api/schema";

/*
 * Types of the Phase 3 price-coverage views. Typed endpoints use the generated schema; the
 * untyped ones (corporate actions, returns, exchange sessions, listing) are parsed from
 * `unknown` in parse.ts into the shapes below.
 */

type Schemas = components["schemas"];
export type PriceHistory = Schemas["PriceHistory"];
export type PriceBarOut = Schemas["PriceBarOut"];
export type PriceDiagnostic = Schemas["PriceDiagnostic"];
export type HoldingsPrices = Schemas["HoldingsPrices"];
export type HoldingPrices = Schemas["HoldingPrices"];
export type PortfolioCorrelation = Schemas["PortfolioCorrelation"];
export type PortfolioRisk = Schemas["PortfolioRisk"];
export type ExchangeCalendar = Schemas["ExchangeCalendar"];
export type PortfolioHoldings = Schemas["HoldingsResponse"];

/** The result of parsing an untyped body: a typed value, or the reason the shape was rejected. */
export type Parsed<T> = { ok: true; data: T } | { ok: false; reason: string };

export type ActionType = "split" | "cash_dividend" | "stock_dividend" | "symbol_change" | "merger" | "spin_off" | "delisting";

export const ACTION_TYPES: readonly ActionType[] = ["split", "cash_dividend", "stock_dividend", "symbol_change", "merger", "spin_off", "delisting"];

export const ACTION_LABELS: Record<ActionType, string> = {
  split: "Split",
  cash_dividend: "Cash dividend",
  stock_dividend: "Stock dividend",
  symbol_change: "Symbol change",
  merger: "Merger",
  spin_off: "Spin-off",
  delisting: "Delisting",
};

/** One-letter marker glyph per action type (letters, not colours, tell them apart). */
export const ACTION_GLYPHS: Record<ActionType, string> = {
  split: "S",
  cash_dividend: "D",
  stock_dividend: "B",
  symbol_change: "N",
  merger: "M",
  spin_off: "O",
  delisting: "X",
};

export type ActionStatus = "suggested" | "confirmed" | "rejected" | "disputed";

export type CorporateAction = {
  id: number;
  symbol: string;
  type: ActionType;
  /** Ex-date: the session from which the price trades without the entitlement. */
  exDate: string;
  recordDate: string | null;
  paymentDate: string | null;
  /** Split / stock-dividend ratio (new shares per old share). */
  ratio: number | null;
  cashAmount: number | null;
  /** Currency or minor unit of the cash amount ("GBp" is pence). */
  currency: string | null;
  source: string;
  sourceUrl: string | null;
  knownAt: string | null;
  evidenceId: number | null;
  status: ActionStatus;
  decidedAt: string | null;
  decisionReason: string | null;
  /** `raw.per`: "ordinary_share" when the amount is per ordinary share of an ADS. */
  per: string | null;
  /** `raw.amount_basis`: "split_adjusted" when a provider already restated the amount. */
  amountBasis: string | null;
};

/** One basis of a window return, with the backend's reason when it cannot be computed. */
export type ReturnFigure = { value: number | null; reason: string | null };

export type ReturnsResult = {
  symbol: string;
  source: string;
  currency: string | null;
  mic: string | null;
  priceReturn: ReturnFigure;
  rawPriceReturn: ReturnFigure;
  totalReturn: ReturnFigure;
  /** "available" only when the owner verified corporate-action coverage through the window end. */
  actionCoverage: "available" | "incomplete";
  /** Observed sessions over sessions the exchange actually held. */
  coverage: number | null;
  expectedSessions: number | null;
  observedSessions: number | null;
  calendarBasis: string | null;
  missingActions: { priceRaw: string[]; priceAdjusted: string[]; totalReturn: string[] };
  start: string;
  end: string;
};

export type ReturnsReport = {
  ticker: string;
  knownAsOf: string | null;
  /** Null with `reason` when the holding has no stored series with a known quote currency. */
  result: ReturnsResult | null;
  reason: string | null;
};

export type ListingOverview = {
  ticker: string;
  status: string;
  score: number | null;
  decidedAt: string | null;
  listing: { id: number; mic: string | null; ticker: string | null; currency: string | null; status: string | null; isin: string | null } | null;
  providerSymbols: { provider: string; identifier: string; matchStatus: string | null }[];
};

export type ExchangeSessionRow = {
  mic: string;
  sessionDate: string;
  state: "open" | "half_day" | "closed";
  source: string;
  confirmed: boolean;
  reason: string | null;
  evidenceUrl: string | null;
  knownAt: string | null;
};

export type ExpectedSessions = {
  mic: string;
  start: string;
  end: string;
  count: number;
  /** confirmed_calendar | partial_confirmed_calendar | weekdays_minus_configured_closures */
  basis: string;
  confirmedDays: number;
  days: string[];
};

export type SessionStatus =
  | "trading"
  | "half_day"
  | "exchange_holiday"
  | "national_holiday_open"
  | "exchange_closure"
  | "missing_bar"
  | "stale_session"
  | "upcoming"
  | "before_history";

export type SessionCell = { date: string; status: SessionStatus; note: string | null };
