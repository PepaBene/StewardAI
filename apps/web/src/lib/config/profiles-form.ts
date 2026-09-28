import type { Exposure, HoldingProfile, HoldingProfilesConfig } from "@/lib/api/client";

import { fundamentalsFromProfile, fundamentalsToProfile, newFundamentals, type FundamentalsForm } from "./fundamentals-form";
import { lines } from "./theses-form";

/* Editable form state for holding profiles; list fields are one entry per line. */

export type ExposureKind = Exposure["kind"];
export const EXPOSURE_KINDS: readonly ExposureKind[] = [
  "revenue_currency",
  "revenue_region",
  "commodity_input",
  "other",
];

export type ExposureForm = { key: string; kind: ExposureKind; name: string; share: string };

export type ValuationSource = NonNullable<HoldingProfile["valuation_source"]>;
export const VALUATION_SOURCES: readonly { value: ValuationSource; label: string }[] = [
  { value: "auto", label: "Automatic (freshest filing; manual when none)" },
  { value: "sec", label: "SEC XBRL only" },
  { value: "esef", label: "ESEF (filings.xbrl.org) only" },
  { value: "manual", label: "Manual figures below" },
];

/** Hand-entered per-share figures (`manual_fundamentals`); all empty = none. */
export type ManualForm = {
  eps_ttm: string;
  book_value_per_share: string;
  dividend_per_share: string;
  currency: string;
  as_of: string;
  source_note: string;
};

export const EMPTY_MANUAL: ManualForm = {
  eps_ttm: "",
  book_value_per_share: "",
  dividend_per_share: "",
  currency: "",
  as_of: "",
  source_note: "",
};

export type ProfileForm = {
  key: string;
  ticker: string;
  company_name: string;
  aliases: string;
  ir_url: string;
  results_urls: string;
  financial_calendar_url: string;
  regulatory_news_urls: string;
  extra_domains: string;
  sec_cik: string;
  sec_ticker: string;
  fiscal_year_end_month: number;
  /** Stooq symbol override (e.g. "sap.de"); empty = derived from OpenFIGI. */
  price_symbol: string;
  reporting_currency: string;
  competitors: string;
  suppliers: string;
  customers: string;
  watch_topics: string;
  exposures: ExposureForm[];
  valuation_source: ValuationSource;
  manual: ManualForm;
  /** SEC XBRL fields: 13F share basis and manual revenue rows (fundamentals-form.ts). */
  fundamentals: FundamentalsForm;
};

let counter = 0;
const nextKey = () => `p${++counter}`;

export function newExposure(): ExposureForm {
  return { key: nextKey(), kind: "revenue_region", name: "", share: "" };
}

export function newProfile(ticker = ""): ProfileForm {
  return {
    key: nextKey(),
    ticker,
    company_name: "",
    aliases: "",
    ir_url: "",
    results_urls: "",
    financial_calendar_url: "",
    regulatory_news_urls: "",
    extra_domains: "",
    sec_cik: "",
    sec_ticker: "",
    fiscal_year_end_month: 12,
    price_symbol: "",
    reporting_currency: "",
    competitors: "",
    suppliers: "",
    customers: "",
    watch_topics: "",
    exposures: [],
    valuation_source: "auto",
    manual: { ...EMPTY_MANUAL },
    fundamentals: newFundamentals(),
  };
}

const joined = (values: string[] | undefined | null) => (values ?? []).join("\n");

export function profilesFromBody(body: HoldingProfilesConfig | null): ProfileForm[] {
  return (body?.profiles ?? []).map((p) => ({
    key: nextKey(),
    ticker: p.ticker,
    company_name: p.company_name ?? "",
    aliases: joined(p.aliases),
    ir_url: p.ir_url ?? "",
    results_urls: joined(p.results_urls),
    financial_calendar_url: p.financial_calendar_url ?? "",
    regulatory_news_urls: joined(p.regulatory_news_urls),
    extra_domains: joined(p.extra_domains),
    sec_cik: p.sec_cik ?? "",
    sec_ticker: p.sec_ticker ?? "",
    fiscal_year_end_month: p.fiscal_year_end_month,
    price_symbol: p.price_symbol ?? "",
    reporting_currency: p.reporting_currency ?? "",
    competitors: joined(p.competitors),
    suppliers: joined(p.suppliers),
    customers: joined(p.customers),
    watch_topics: joined(p.watch_topics),
    exposures: (p.exposures ?? []).map((e) => ({
      key: nextKey(),
      kind: e.kind,
      name: e.name,
      share: e.share === null || e.share === undefined ? "" : String(e.share),
    })),
    valuation_source: p.valuation_source ?? "auto",
    manual: manualFromBody(p.manual_fundamentals),
    fundamentals: fundamentalsFromProfile(p),
  }));
}

const numberText = (v: number | null | undefined) => (v === null || v === undefined ? "" : String(v));

function manualFromBody(m: HoldingProfile["manual_fundamentals"]): ManualForm {
  if (!m) return { ...EMPTY_MANUAL };
  return {
    eps_ttm: numberText(m.eps_ttm),
    book_value_per_share: numberText(m.book_value_per_share),
    dividend_per_share: numberText(m.dividend_per_share),
    currency: m.currency,
    as_of: m.as_of,
    source_note: m.source_note ?? "",
  };
}

/** "1 234,5" -> 1234.5; empty -> null. Anything unparsable is sent as-is so the backend names the field. */
function decimal(text: string): number | null {
  const t = text.trim().replace(/\s/g, "").replace(",", ".");
  return t ? Number(t) : null;
}

export function manualToBody(m: ManualForm): HoldingProfile["manual_fundamentals"] {
  const empty = Object.values(m).every((v) => !v.trim());
  if (empty) return null;
  return {
    eps_ttm: decimal(m.eps_ttm),
    book_value_per_share: decimal(m.book_value_per_share),
    dividend_per_share: decimal(m.dividend_per_share),
    currency: m.currency.trim().toUpperCase(),
    as_of: m.as_of.trim(),
    source_note: m.source_note.trim(),
  };
}

const orNull = (text: string) => (text.trim() ? text.trim() : null);

export function profilesToBody(forms: ProfileForm[]): HoldingProfilesConfig {
  return {
    schema_version: 1,
    profiles: forms.map(
      (f): HoldingProfile => ({
        ticker: f.ticker.trim(),
        company_name: f.company_name.trim(),
        aliases: lines(f.aliases),
        ir_url: orNull(f.ir_url),
        results_urls: lines(f.results_urls),
        financial_calendar_url: orNull(f.financial_calendar_url),
        regulatory_news_urls: lines(f.regulatory_news_urls),
        extra_domains: lines(f.extra_domains).map((d) => d.toLowerCase()),
        sec_cik: orNull(f.sec_cik),
        sec_ticker: orNull(f.sec_ticker)?.toUpperCase() ?? null,
        fiscal_year_end_month: f.fiscal_year_end_month,
        price_symbol: orNull(f.price_symbol)?.toLowerCase() ?? null,
        reporting_currency: orNull(f.reporting_currency)?.toUpperCase() ?? null,
        competitors: lines(f.competitors),
        suppliers: lines(f.suppliers),
        customers: lines(f.customers),
        watch_topics: lines(f.watch_topics),
        exposures: f.exposures.map((e) => ({
          kind: e.kind,
          name: e.name.trim(),
          share: e.share.trim() ? Number(e.share.trim().replace(",", ".")) : null,
        })),
        valuation_source: f.valuation_source,
        manual_fundamentals: manualToBody(f.manual),
        ...fundamentalsToProfile(f.fundamentals),
      }),
    ),
  };
}
