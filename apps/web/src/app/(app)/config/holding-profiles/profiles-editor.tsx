"use client";

import { LoaderCircleIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ConfigHeader } from "@/components/config/config-header";
import { SaveBar } from "@/components/config/save-bar";
import { SelectField, TextAreaField, TextField } from "@/components/config/fields";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type Holding, type HoldingProfilesConfig } from "@/lib/api/client";
import { errorsAt, type FieldErrors } from "@/lib/api/validation-errors";
import {
  EXPOSURE_KINDS,
  VALUATION_SOURCES,
  newExposure,
  newProfile,
  profilesFromBody,
  profilesToBody,
  type ExposureForm,
  type ManualForm,
  type ProfileForm,
} from "@/lib/config/profiles-form";
import { configSaveQuery, useConfigEditor } from "@/lib/hooks/use-config-editor";
import { BlueprintCorners } from "@/components/ui/blueprint";

import { FundamentalsFields } from "./fundamentals-fields";

const api = () => getBrowserApiClient();

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
].map((label, i) => ({ value: i + 1, label }));

const EXPOSURE_LABELS: Record<string, string> = {
  revenue_currency: "Revenue currency",
  revenue_region: "Revenue region",
  commodity_input: "Commodity input",
  other: "Other",
};

export function ProfilesEditor() {
  const [forms, setForms] = useState<ProfileForm[]>([]);
  const [baseline, setBaseline] = useState("");
  const [holdings, setHoldings] = useState<Holding[]>([]);

  const editor = useConfigEditor<HoldingProfilesConfig>({
    load: () => api().GET("/config/holding-profiles"),
    save: (body, note, base) => api().PUT("/config/holding-profiles", { body, params: { query: configSaveQuery(note, base) } }),
    validate: (body) => api().POST("/config/holding-profiles/validate", { body }),
    onLoaded: (body) => {
      const loaded = profilesFromBody(body);
      setForms(loaded);
      setBaseline(JSON.stringify(profilesToBody(loaded)));
    },
  });

  useEffect(() => {
    let cancelled = false;
    api()
      .GET("/portfolio/holdings")
      .then((r) => {
        if (!cancelled && r.data) setHoldings(r.data.holdings);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (editor.status.kind !== "ready" || !window.location.hash) return;
    document.getElementById(decodeURIComponent(window.location.hash.slice(1)))?.scrollIntoView();
  }, [editor.status.kind]);

  const body = useMemo(() => profilesToBody(forms), [forms]);
  const dirty = JSON.stringify(body) !== baseline;
  const withoutProfile = holdings.filter((h) => !forms.some((f) => f.ticker === h.ticker));

  async function save(note: string | null) {
    if (await editor.save(body, note)) setBaseline(JSON.stringify(body));
  }

  return (
    <div className="space-y-6">
      <ConfigHeader title="Holding profiles" domain="holding-profiles">
        <p>
          Where each company publishes and how its fiscal year runs. The earnings check looks for
          releases on EDGAR (US 8-K, foreign 6-K) and otherwise searches only the domains listed
          here; the fiscal year end maps &ldquo;Q3&rdquo; to calendar months. Names, competitors and
          topics feed the news pipeline.
        </p>
      </ConfigHeader>

      {editor.status.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading profiles…
        </p>
      ) : null}
      {editor.status.kind === "error" ? (
        <AlertMessage title="Could not load the holding profiles">{editor.status.message}</AlertMessage>
      ) : null}

      {editor.status.kind === "ready" ? (
        <>
          {forms.length === 0 ? (
            <EmptyState title="No profiles yet">
              Without a profile a holding is checked with defaults: a December fiscal year end and
              EDGAR only.
            </EmptyState>
          ) : null}
          {forms.map((form, index) => (
            <ProfileCard
              key={form.key}
              form={form}
              index={index}
              errors={editor.errors}
              onChange={(change) => setForms((all) => all.map((f) => (f.key === form.key ? change(f) : f)))}
              onRemove={() => setForms((all) => all.filter((f) => f.key !== form.key))}
            />
          ))}
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => setForms((all) => [...all, newProfile()])}>
              <PlusIcon aria-hidden="true" />
              Add profile
            </Button>
            {withoutProfile.slice(0, 6).map((h) => (
              <Button
                key={h.ticker}
                variant="ghost"
                size="sm"
                onClick={() =>
                  setForms((all) => [...all, { ...newProfile(h.ticker), company_name: h.name ?? "" }])
                }
              >
                <PlusIcon aria-hidden="true" />
                {h.name ?? h.ticker}
              </Button>
            ))}
          </div>
          <datalist id="profile-tickers">
            {holdings.map((h) => (
              <option key={h.ticker} value={h.ticker}>
                {h.name}
              </option>
            ))}
          </datalist>
          <SaveBar
            dirty={dirty}
            savedVersionId={editor.version?.id ?? null}
            busy={editor.busy}
            errors={editor.errors}
            message={editor.message}
            onValidate={() => void editor.validate(body)}
            onSave={(note) => void save(note)}
          />
        </>
      ) : null}
    </div>
  );
}

function ProfileCard({
  form,
  index,
  errors,
  onChange,
  onRemove,
}: {
  form: ProfileForm;
  index: number;
  errors: FieldErrors;
  onChange: (change: (form: ProfileForm) => ProfileForm) => void;
  onRemove: () => void;
}) {
  const path = `profiles.${index}`;
  const text = (key: keyof ProfileForm) => ({
    path: `${path}.${key}`,
    errors,
    value: form[key] as string,
    onChange: (v: string) => onChange((f) => ({ ...f, [key]: v })),
  });
  const setExposure = (key: string, change: Partial<ExposureForm>) =>
    onChange((f) => ({ ...f, exposures: f.exposures.map((e) => (e.key === key ? { ...e, ...change } : e)) }));

  return (
    <section
      id={form.ticker || undefined}
      aria-label={`Profile ${form.ticker || index + 1}`}
      className="scroll-mt-24 space-y-4 blueprint p-5"
    >
      <BlueprintCorners />
      <div className="flex flex-wrap items-start gap-3">
        <TextField className="w-56" label="Ticker" list="profile-tickers" {...text("ticker")} />
        <TextField className="min-w-56 flex-1" label="Company name" {...text("company_name")} />
        <Button variant="ghost" size="sm" onClick={onRemove}>
          <Trash2Icon aria-hidden="true" />
          Remove
        </Button>
      </div>

      <fieldset className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <legend className="mb-2 text-sm font-medium">Filings, fiscal calendar and prices</legend>
        <SelectField
          label="Fiscal year ends in"
          path={`${path}.fiscal_year_end_month`}
          errors={errors}
          value={form.fiscal_year_end_month}
          options={MONTHS}
          onChange={(v) => onChange((f) => ({ ...f, fiscal_year_end_month: v }))}
          hint="Apple: September. Microsoft: June."
        />
        <TextField label="Reporting currency" placeholder="USD" maxLength={3} {...text("reporting_currency")} />
        <TextField label="SEC CIK" placeholder="looked up from the ticker" inputMode="numeric" {...text("sec_cik")} />
        <TextField label="EDGAR / Finnhub symbol" placeholder="if not the ticker prefix" {...text("sec_ticker")} />
        <TextField
          label="Stooq price symbol"
          placeholder="derived from OpenFIGI"
          spellCheck={false}
          hint="Only when the derived symbol is wrong or has no data, e.g. sap.de, aapl.us, vod.uk. Lower case."
          {...text("price_symbol")}
        />
        <TextField
          label="Yahoo symbol"
          placeholder="derived from the listing"
          spellCheck={false}
          hint="Intraday prices, and daily closes when Stooq has none (Yahoo Finance, unofficial). Only when the derived one is wrong, e.g. NOVO-B.CO, CEZ.PR, VWCE.DE."
          {...text("yahoo_symbol")}
        />
      </fieldset>

      <fieldset className="grid gap-3 lg:grid-cols-2">
        <legend className="mb-2 text-sm font-medium">Where results are published</legend>
        <TextField label="Investor relations URL" type="url" {...text("ir_url")} />
        <TextField label="Financial calendar URL" type="url" hint="Read for the next report date." {...text("financial_calendar_url")} />
        <TextAreaField label="Results pages" hint="One URL per line." rows={2} {...text("results_urls")} />
        <TextAreaField label="Regulatory news pages" hint="One URL per line." rows={2} {...text("regulatory_news_urls")} />
        <TextAreaField
          label="Extra searchable domains"
          hint="e.g. globenewswire.com. The IR and results URLs' domains are always allowed."
          rows={2}
          {...text("extra_domains")}
        />
        <TextAreaField label="Aliases and former names" hint="One per line." rows={2} {...text("aliases")} />
      </fieldset>

      <ValuationFields form={form} path={path} errors={errors} onChange={onChange} />

      <details className="rounded-md border border-border p-3">
        <summary className="cursor-pointer text-sm font-medium">News relevance and exposures</summary>
        <div className="mt-3 grid gap-3 lg:grid-cols-2">
          <TextAreaField label="Competitors" rows={2} {...text("competitors")} />
          <TextAreaField label="Suppliers" rows={2} {...text("suppliers")} />
          <TextAreaField label="Customers" rows={2} {...text("customers")} />
          <TextAreaField label="Watch topics" rows={2} {...text("watch_topics")} />
        </div>
        <fieldset className="mt-3 space-y-2">
          <legend className="text-sm font-medium">Exposures</legend>
          {form.exposures.map((e, i) => (
            <div key={e.key} className="grid grid-cols-[1fr_1.5fr_0.7fr_auto] items-end gap-2">
              <SelectField
                label="Kind"
                path={`${path}.exposures.${i}.kind`}
                errors={errors}
                value={e.kind}
                options={EXPOSURE_KINDS.map((k) => ({ value: k, label: EXPOSURE_LABELS[k] ?? k }))}
                onChange={(v) => setExposure(e.key, { kind: v })}
              />
              <TextField
                label="Name"
                path={`${path}.exposures.${i}.name`}
                errors={errors}
                value={e.name}
                onChange={(v) => setExposure(e.key, { name: v })}
              />
              <TextField
                label="Share (0–1)"
                path={`${path}.exposures.${i}.share`}
                errors={errors}
                value={e.share}
                inputMode="decimal"
                onChange={(v) => setExposure(e.key, { share: v })}
              />
              <Button
                variant="ghost"
                size="icon"
                aria-label="Remove exposure"
                onClick={() => onChange((f) => ({ ...f, exposures: f.exposures.filter((x) => x.key !== e.key) }))}
              >
                <Trash2Icon aria-hidden="true" />
              </Button>
            </div>
          ))}
          <Button
            variant="outline"
            size="sm"
            onClick={() => onChange((f) => ({ ...f, exposures: [...f.exposures, newExposure()] }))}
          >
            <PlusIcon aria-hidden="true" />
            Add exposure
          </Button>
        </fieldset>
      </details>

      <FundamentalsFields
        form={form.fundamentals}
        path={path}
        errors={errors}
        onChange={(change) => onChange((f) => ({ ...f, fundamentals: change(f.fundamentals) }))}
      />
    </section>
  );
}

/**
 * Valuation inputs: which fundamentals the P/E uses, the depositary ratio, and figures entered
 * by hand for holdings no automatic source covers (shown as "entered manually").
 */
function ValuationFields({
  form,
  path,
  errors,
  onChange,
}: {
  form: ProfileForm;
  path: string;
  errors: FieldErrors;
  onChange: (change: (form: ProfileForm) => ProfileForm) => void;
}) {
  const manualPath = `${path}.manual_fundamentals`;
  const manual = (key: keyof ManualForm) => ({
    path: `${manualPath}.${key}`,
    errors,
    value: form.manual[key],
    onChange: (v: string) => onChange((f) => ({ ...f, manual: { ...f.manual, [key]: v } })),
  });
  const hasManual = Object.values(form.manual).some((v) => v.trim());
  const open = hasManual || form.valuation_source !== "auto";
  const manualErrors = errorsAt(errors, manualPath);
  return (
    <details className="rounded-md border border-border p-3" open={open || undefined}>
      <summary className="cursor-pointer text-sm font-medium">Valuation (P/E, P/B, dividend yield)</summary>
      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <SelectField
          label="Fundamentals from"
          path={`${path}.valuation_source`}
          errors={errors}
          value={form.valuation_source}
          options={VALUATION_SOURCES.map((o) => ({ value: o.value, label: o.label }))}
          onChange={(v) => onChange((f) => ({ ...f, valuation_source: v }))}
          hint="Automatic uses SEC XBRL or ESEF, whichever is fresher, and your figures only when neither has any."
        />
        <p className="self-center text-xs text-text-muted sm:col-span-1 lg:col-span-3">
          Depositary receipts: per-share figures are multiplied by the ADS ratio under SEC XBRL fundamentals &rarr; share basis
          (empty: the ratio the 20-F states, e.g. TSMC 5, else 1).
        </p>
      </div>
      <fieldset className="mt-3 space-y-2">
        <legend className="text-sm font-medium">Figures entered manually</legend>
        <p className="text-xs text-text-muted">
          For holdings without machine-readable filings (e.g. Prague listings). Per ordinary share, from the company&rsquo;s own
          report; leave everything empty for none.
        </p>
        <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <TextField label="EPS (TTM)" inputMode="decimal" {...manual("eps_ttm")} />
          <TextField label="Book value / share" inputMode="decimal" {...manual("book_value_per_share")} />
          <TextField label="Dividend / share (12 m)" inputMode="decimal" {...manual("dividend_per_share")} />
          <TextField label="Currency" placeholder="CZK" maxLength={3} {...manual("currency")} />
          <TextField label="As of" type="date" {...manual("as_of")} />
          <TextField className="sm:col-span-3 lg:col-span-6" label="Source" placeholder="Annual report 2025, p. 17" {...manual("source_note")} />
        </div>
        {manualErrors.length > 0 ? (
          <ul className="space-y-0.5 text-xs text-text">
            {manualErrors.map((m) => (
              <li key={m} className="flex gap-1">
                <span aria-hidden="true" className="text-warning-signal">
                  ●
                </span>
                {m}
              </li>
            ))}
          </ul>
        ) : null}
      </fieldset>
    </details>
  );
}
