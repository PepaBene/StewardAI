import type { DataState } from "@/lib/data-state/states";

/*
 * Typed contracts for the Phase 3 price-coverage views: listing price series with an explicit
 * adjustment basis, a corporate-action ledger, venue session calendars, cross-holding
 * correlation and per-listing source diagnostics.
 */

export type Bar = {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
};

export type AdjustmentBasis = "raw" | "adjusted";

export type ActionType = "split" | "cash_dividend" | "stock_dividend" | "symbol_change" | "merger" | "spin_off" | "delisting";

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

export type CorporateAction = {
  id: string;
  /** Ex-date or effective date. */
  date: string;
  type: ActionType;
  /** "4-for-1", "0.31 USD per share". Null when the amount is not known. */
  detail: string | null;
  /** Split factor (new shares per old share), for splits. */
  factor: number | null;
  /** Issuer announcement, exchange notice or broker ledger: the evidence. */
  source: string;
  sourceUrl: string | null;
  /** Confirmed against an issuer/exchange record, versus only suggested. */
  confirmed: boolean;
};

export type ListingKind = "ordinary" | "adr" | "etf";

export type ListingSeries = {
  listingId: string;
  name: string;
  ticker: string;
  mic: string;
  venue: string;
  kind: ListingKind;
  /** Currency of the quote as the venue prints it: "GBX" is pence. */
  quoteCurrency: string;
  /** For an ADR: "1 ADR = 0.5 ordinary share". */
  ratioNote: string | null;
  source: string;
  sourceUrl: string | null;
  raw: readonly Bar[];
  /** Split/dividend-adjusted closes; null when no reconciled adjustment exists. */
  adjusted: readonly Bar[] | null;
  /** Raw and adjusted series were reconciled against every confirmed action. */
  adjustmentReconciled: boolean;
  actions: readonly CorporateAction[];
  /** Reinvested total-return index (start = 100) or null with the reason. */
  totalReturn: readonly { date: string; value: number }[] | null;
  totalReturnReason: string | null;
  state: DataState;
  reason: string | null;
};

export type SessionStatus =
  | "trading"
  | "half_day"
  | "exchange_holiday"
  | "national_holiday_open"
  | "exchange_closure"
  | "missing_bar";

/** The venue's own calendar for a weekday: what the exchange did, independent of our data. */
export type VenueDay = {
  date: string;
  kind: "open" | "half_day" | "closed";
  /** Why it was closed or short: "Independence Day (observed)". */
  reason: string | null;
  /** A national public holiday fell on this date (Nager.Date), whether or not the venue closed. */
  nationalHoliday: string | null;
  /** Confirmed against an exchange notice, versus inferred from a national holiday. */
  confirmed: boolean;
};

export type VenueCalendar = { mic: string; venue: string; days: readonly VenueDay[] };

export type PriceDisagreement = {
  provider: string;
  date: string;
  primary: number;
  other: number;
  /** Currency and unit both prices are expressed in. */
  currency: string;
};

export type PriceDiagnostics = {
  listingId: string;
  /** Source of record and the alternatives compared against it. */
  sourceOfRecord: string;
  alternatives: readonly string[];
  mic: string;
  exchange: string;
  quoteCurrency: string;
  adjustmentBasis: string;
  lastSession: string | null;
  disagreements: readonly PriceDisagreement[];
};

export type CorrelationWindow = {
  id: string;
  label: string;
  /** Cluster order of the listing keys (most similar adjacent). */
  order: readonly string[];
  /** matrix[i][j] is the correlation of order[i] with order[j]; null when too few shared observations. */
  matrix: readonly (readonly (number | null)[])[];
  /** Shared observations behind each cell. */
  counts: readonly (readonly number[])[];
  minShared: number;
};

export type CorrelationReport = {
  labels: Readonly<Record<string, string>>;
  windows: readonly CorrelationWindow[];
  source: string;
  asOf: string;
  state: DataState;
  reason: string | null;
};
