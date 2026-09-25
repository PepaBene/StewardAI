"use client";

import { LoaderCircleIcon, PlusIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { SaveBar } from "@/components/config/save-bar";
import { SelectField, TextAreaField, TextField } from "@/components/config/fields";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type Holding, type ThesesConfig } from "@/lib/api/client";
import type { FieldErrors } from "@/lib/api/validation-errors";
import {
  BASES,
  KNOWN_METRICS,
  KPI_PERIODS,
  newKpi,
  newThesis,
  OPERATORS,
  slugFromMetric,
  thesesFromBody,
  thesesToBody,
  UNIT_EXAMPLES,
  type KpiForm,
  type ThesisForm,
} from "@/lib/config/theses-form";
import { useConfigEditor } from "@/lib/hooks/use-config-editor";

const api = () => getBrowserApiClient();

export function ThesesEditor() {
  const [forms, setForms] = useState<ThesisForm[]>([]);
  const [baseline, setBaseline] = useState<string>("");
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [verdictJob, setVerdictJob] = useState<string | null>(null);

  const editor = useConfigEditor<ThesesConfig>({
    load: () => api().GET("/config/theses"),
    save: (body, note) => api().PUT("/config/theses", { body, params: { query: { note } } }),
    validate: (body) => api().POST("/config/theses/validate", { body }),
    onLoaded: (body) => {
      const loaded = thesesFromBody(body);
      setForms(loaded);
      setBaseline(JSON.stringify(thesesToBody(loaded)));
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

  // Jump to the thesis named in the URL hash (links from a holding page).
  useEffect(() => {
    if (editor.status.kind !== "ready" || !window.location.hash) return;
    document.getElementById(decodeURIComponent(window.location.hash.slice(1)))?.scrollIntoView();
  }, [editor.status.kind]);

  const body = useMemo(() => thesesToBody(forms), [forms]);
  const dirty = JSON.stringify(body) !== baseline;
  const withoutThesis = holdings.filter((h) => !forms.some((f) => f.ticker === h.ticker));

  const update = (key: string, change: (form: ThesisForm) => ThesisForm) =>
    setForms((all) => all.map((f) => (f.key === key ? change(f) : f)));

  async function save(note: string | null) {
    setVerdictJob(null);
    const saved = await editor.save(body, note);
    if (!saved) return;
    setBaseline(JSON.stringify(body));
    // The next verdict run uses the new thresholds: queue it now instead of waiting for a release.
    const job = await api()
      .POST("/jobs/thesis-verdicts", { body: {} })
      .catch(() => null);
    setVerdictJob(
      job?.data
        ? `Verdicts are being re-judged with version #${saved.id} (job #${job.data.id}).`
        : "Saved, but re-running the verdicts could not be queued; use Re-run verdicts on a holding page.",
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm text-text-muted">
          <Link href="/config" className="hover:text-text hover:underline">
            Config
          </Link>{" "}
          / Theses
        </p>
        <h1 className="text-2xl font-semibold tracking-tight">Theses</h1>
        <p className="mt-1 max-w-prose text-sm text-text-muted">
          Why you own each holding, and the KPIs that must hold. Thresholds are absolute: the
          earnings check extracts the figures from the company&rsquo;s release and judges each KPI
          pass, breach or unknown. Saving creates a new version; earlier verdicts stay tied to the
          version they were judged under.
        </p>
      </div>

      {editor.status.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading theses…
        </p>
      ) : null}
      {editor.status.kind === "error" ? (
        <AlertMessage title="Could not load the theses">{editor.status.message}</AlertMessage>
      ) : null}

      {editor.status.kind === "ready" ? (
        <>
          {forms.length === 0 ? (
            <EmptyState title="No theses yet">Add one for each holding you want monitored.</EmptyState>
          ) : null}

          {forms.map((form, index) => (
            <ThesisCard
              key={form.key}
              form={form}
              index={index}
              errors={editor.errors}
              onChange={(change) => update(form.key, change)}
              onRemove={() => setForms((all) => all.filter((f) => f.key !== form.key))}
            />
          ))}

          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => setForms((all) => [...all, newThesis()])}>
              <PlusIcon aria-hidden="true" />
              Add thesis
            </Button>
            {withoutThesis.slice(0, 6).map((h) => (
              <Button
                key={h.ticker}
                variant="ghost"
                size="sm"
                onClick={() => setForms((all) => [...all, newThesis(h.ticker)])}
              >
                <PlusIcon aria-hidden="true" />
                {h.name ?? h.ticker}
              </Button>
            ))}
          </div>
          <datalist id="holding-tickers">
            {holdings.map((h) => (
              <option key={h.ticker} value={h.ticker}>
                {h.name}
              </option>
            ))}
          </datalist>
          <datalist id="kpi-metrics">
            {KNOWN_METRICS.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
          <datalist id="kpi-units">
            {UNIT_EXAMPLES.map((u) => (
              <option key={u} value={u} />
            ))}
          </datalist>

          {verdictJob ? <AlertMessage tone="info" title="Saved">{verdictJob}</AlertMessage> : null}
          <SaveBar
            dirty={dirty}
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

function ThesisCard({
  form,
  index,
  errors,
  onChange,
  onRemove,
}: {
  form: ThesisForm;
  index: number;
  errors: FieldErrors;
  onChange: (change: (form: ThesisForm) => ThesisForm) => void;
  onRemove: () => void;
}) {
  const path = `theses.${index}`;
  const set = <K extends keyof ThesisForm>(key: K, value: ThesisForm[K]) =>
    onChange((f) => ({ ...f, [key]: value }));
  const setKpi = (key: string, change: Partial<KpiForm>) =>
    onChange((f) => ({ ...f, kpis: f.kpis.map((k) => (k.key === key ? { ...k, ...change } : k)) }));

  return (
    <section
      id={form.ticker || undefined}
      aria-label={`Thesis ${form.ticker || index + 1}`}
      className="scroll-mt-24 space-y-4 rounded-lg border border-border bg-surface-raised p-4"
    >
      <div className="flex flex-wrap items-start gap-3">
        <TextField
          className="w-56"
          label="Ticker"
          path={`${path}.ticker`}
          errors={errors}
          value={form.ticker}
          onChange={(v) => set("ticker", v)}
          list="holding-tickers"
          placeholder="AAPL_US_EQ"
          hint="Trading212 ticker"
        />
        <TextField
          className="w-44"
          label="Review date"
          type="date"
          path={`${path}.review_date`}
          errors={errors}
          value={form.review_date}
          onChange={(v) => set("review_date", v)}
        />
        <Button variant="ghost" size="sm" className="ml-auto" onClick={onRemove}>
          <Trash2Icon aria-hidden="true" />
          Remove thesis
        </Button>
      </div>
      <TextAreaField
        label="Why it is owned"
        path={`${path}.why_owned`}
        errors={errors}
        value={form.why_owned}
        onChange={(v) => set("why_owned", v)}
      />
      <TextAreaField
        label="Exit conditions"
        path={`${path}.exit_conditions`}
        errors={errors}
        value={form.exit_conditions}
        onChange={(v) => set("exit_conditions", v)}
        hint="One per line."
        rows={2}
      />

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">KPIs</legend>
        {form.kpis.length === 0 ? (
          <p className="text-sm text-text-muted">No KPIs: this holding gets no verdicts.</p>
        ) : null}
        {form.kpis.map((kpi, k) => {
          const kpiPath = `${path}.kpis.${k}`;
          return (
            <div
              key={kpi.key}
              className="grid grid-cols-2 items-start gap-2 rounded-md border border-border p-3 sm:grid-cols-4 lg:grid-cols-[1.4fr_1fr_0.6fr_0.8fr_0.9fr_0.9fr_0.9fr_auto]"
            >
              <TextField
                label="Metric"
                path={`${kpiPath}.metric`}
                errors={errors}
                value={kpi.metric}
                list="kpi-metrics"
                onChange={(v) =>
                  setKpi(kpi.key, {
                    metric: v,
                    ...(kpi.id === slugFromMetric(kpi.metric) ? { id: slugFromMetric(v) } : {}),
                  })
                }
              />
              <TextField
                label="Id"
                path={`${kpiPath}.id`}
                errors={errors}
                value={kpi.id}
                onChange={(v) => setKpi(kpi.key, { id: v })}
              />
              <SelectField
                label="Operator"
                path={`${kpiPath}.operator`}
                errors={errors}
                value={kpi.operator}
                options={OPERATORS.map((o) => ({ value: o, label: o }))}
                onChange={(v) => setKpi(kpi.key, { operator: v })}
              />
              <TextField
                label="Threshold"
                path={`${kpiPath}.threshold`}
                errors={errors}
                value={kpi.threshold}
                inputMode="decimal"
                className="num"
                onChange={(v) => setKpi(kpi.key, { threshold: v })}
              />
              <TextField
                label="Unit"
                path={`${kpiPath}.unit`}
                errors={errors}
                value={kpi.unit}
                list="kpi-units"
                onChange={(v) => setKpi(kpi.key, { unit: v })}
              />
              <SelectField
                label="Basis"
                path={`${kpiPath}.basis`}
                errors={errors}
                value={kpi.basis}
                options={BASES.map((b) => ({ value: b, label: b }))}
                onChange={(v) => setKpi(kpi.key, { basis: v })}
              />
              <SelectField
                label="Period"
                path={`${kpiPath}.period`}
                errors={errors}
                value={kpi.period}
                options={KPI_PERIODS.map((p) => ({ value: p, label: p === "ttm" ? "TTM" : p }))}
                onChange={(v) => setKpi(kpi.key, { period: v })}
              />
              <Button
                variant="ghost"
                size="icon"
                className="self-end"
                aria-label={`Remove KPI ${kpi.id || k + 1}`}
                onClick={() => onChange((f) => ({ ...f, kpis: f.kpis.filter((x) => x.key !== kpi.key) }))}
              >
                <Trash2Icon aria-hidden="true" />
              </Button>
              <TextField
                className="col-span-full"
                label="Description"
                path={`${kpiPath}.description`}
                errors={errors}
                value={kpi.description}
                onChange={(v) => setKpi(kpi.key, { description: v })}
                placeholder="Optional; tells the extraction what a custom metric means"
              />
            </div>
          );
        })}
        <Button
          variant="outline"
          size="sm"
          onClick={() => onChange((f) => ({ ...f, kpis: [...f.kpis, newKpi()] }))}
        >
          <PlusIcon aria-hidden="true" />
          Add KPI
        </Button>
      </fieldset>
    </section>
  );
}
