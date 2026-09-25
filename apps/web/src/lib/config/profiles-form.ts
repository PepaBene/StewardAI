import type { Exposure, HoldingProfile, HoldingProfilesConfig } from "@/lib/api/client";

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
  }));
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
      }),
    ),
  };
}
