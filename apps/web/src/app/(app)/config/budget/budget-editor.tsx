"use client";

import { LoaderCircleIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ConfigHeader } from "@/components/config/config-header";
import { CheckboxGroup, SelectField, TextField } from "@/components/config/fields";
import { SaveBar } from "@/components/config/save-bar";
import { Button } from "@/components/ui/button";
import { errorsAt, type FieldErrors } from "@/lib/api/validation-errors";
import { MODEL_SUGGESTIONS } from "@/lib/config/jobs-form";
import { mergeErrors } from "@/lib/config/strategy-form";
import { configSaveQuery, useConfigEditor } from "@/lib/hooks/use-config-editor";

import { budgetApi, JOB_TYPES, type BudgetConfig, type JobType } from "./budget-api";
import {
  AT_CAP_OPTIONS,
  budgetFromBody,
  budgetToBody,
  JOB_TYPE_LABELS,
  newJobCapRow,
  newPriceRow,
  validateBudgetForm,
  type BudgetForm,
  type JobCapRow,
  type PriceRow,
} from "./budget-form";

const SECTION = "space-y-3 rounded-lg border border-border bg-surface-raised p-4";
const JOB_OPTIONS = JOB_TYPES.map((t) => ({
  value: t,
  label: JOB_TYPE_LABELS[t],
}));

/**
 * The Budget config: the price table every AI step is costed with, and the spend limits
 * checked before each AI request of a job (monthly cap, the behaviour at the cap, caps per
 * run). Used from the next AI request, no deploy.
 */
export function BudgetEditor() {
  const [form, setForm] = useState<BudgetForm | null>(null);
  const [baseline, setBaseline] = useState("");

  const editor = useConfigEditor<BudgetConfig>({
    load: budgetApi.load,
    save: (body, note, base) => budgetApi.save(body, configSaveQuery(note, base)),
    validate: budgetApi.validate,
    onLoaded: (body) => {
      const loaded = budgetFromBody(body);
      setForm(loaded);
      setBaseline(body ? JSON.stringify(budgetToBody(loaded)) : "");
    },
  });

  const body = useMemo(() => (form ? budgetToBody(form) : null), [form]);
  const clientErrors = useMemo(() => (form ? validateBudgetForm(form) : {}), [form]);
  const errors = useMemo(() => mergeErrors(clientErrors, editor.errors), [clientErrors, editor.errors]);
  const blocked = Object.keys(clientErrors).length > 0;
  const dirty = body !== null && JSON.stringify(body) !== baseline;
  const set = (change: Partial<BudgetForm>) => setForm((f) => (f ? { ...f, ...change } : f));
  const setPrice = (key: string, change: Partial<PriceRow>) =>
    setForm((f) =>
      f
        ? {
            ...f,
            prices: f.prices.map((r) => (r.key === key ? { ...r, ...change } : r)),
          }
        : f,
    );
  const setJobCap = (key: string, change: Partial<JobCapRow>) =>
    setForm((f) =>
      f
        ? {
            ...f,
            jobCaps: f.jobCaps.map((r) => (r.key === key ? { ...r, ...change } : r)),
          }
        : f,
    );

  async function save(note: string | null) {
    if (!body || blocked) return;
    if (await editor.save(body, note)) setBaseline(JSON.stringify(body));
  }

  return (
    <div className="space-y-6">
      <ConfigHeader title="Budget" domain="budget">
        <p>
          What AI costs and how much may be spent. Every AI step is costed with the prices below, and before each AI request of a job the
          spend so far is checked against the caps. Used from the next request, no deploy.
        </p>
      </ConfigHeader>

      {editor.status.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading the budget config…
        </p>
      ) : null}
      {editor.status.kind === "error" ? (
        <AlertMessage title="Could not load the budget config">{editor.status.message}</AlertMessage>
      ) : null}
      {editor.status.kind === "ready" && editor.status.version === null ? (
        <AlertMessage tone="info" title="Defaults in use">
          Nothing saved yet: the values below are the backend&rsquo;s defaults (list prices, no caps).
        </AlertMessage>
      ) : null}

      {form ? (
        <>
          <section aria-labelledby="cap-heading" className={SECTION}>
            <h2 id="cap-heading" className="text-lg font-semibold">
              Monthly cap
            </h2>
            <p className="max-w-prose text-sm text-text-muted">
              Spend per calendar month in Prague time, the sum of every AI step&rsquo;s cost. Passing the warning threshold and reaching the
              cap each send one system alert per month.
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              <TextField
                label="Monthly cap (USD)"
                inputMode="decimal"
                path="monthly_cap_usd"
                errors={errors}
                value={form.monthlyCap}
                placeholder="No cap"
                onChange={(v) => set({ monthlyCap: v })}
                hint="Empty: no cap."
              />
              <TextField
                label="Warn at (% of the cap)"
                inputMode="decimal"
                path="warn_at_fraction"
                errors={errors}
                value={form.warnPercent}
                onChange={(v) => set({ warnPercent: v })}
                hint="Above 0, at most 100."
              />
              <SelectField
                label="At the cap"
                path="at_cap"
                errors={errors}
                value={form.atCap}
                options={AT_CAP_OPTIONS}
                onChange={(v) => set({ atCap: v })}
                hint="Critical job types always keep running."
              />
              <TextField
                label="Downgrade model"
                path="downgrade_model"
                errors={errors}
                value={form.downgradeModel}
                list="budget-models"
                spellCheck={false}
                onChange={(v) => set({ downgradeModel: v })}
                hint="Used by non-critical jobs at the cap when downgrading. It needs a price below."
              />
            </div>
            <CheckboxGroup
              label="Critical job types (keep running at the cap)"
              path="critical_job_types"
              errors={errors}
              values={form.critical}
              options={JOB_OPTIONS}
              onChange={(v) => set({ critical: v })}
            />
          </section>

          <section aria-labelledby="run-caps-heading" className={SECTION}>
            <h2 id="run-caps-heading" className="text-lg font-semibold">
              Caps per run
            </h2>
            <p className="max-w-prose text-sm text-text-muted">
              The most one run of a job may spend. A run over its cap fails before its next AI request, with the reason in the job log.
            </p>
            <TextField
              className="max-w-xs"
              label="Default cap per run (USD)"
              inputMode="decimal"
              path="default_job_cap_usd"
              errors={errors}
              value={form.defaultJobCap}
              placeholder="No cap"
              onChange={(v) => set({ defaultJobCap: v })}
              hint="For job types without their own cap. Empty: no cap."
            />
            {errorsAt(errors, "per_job_cap_usd").length > 0 ? (
              <p className="text-xs text-text">{errorsAt(errors, "per_job_cap_usd").join(" ")}</p>
            ) : null}
            <ul className="space-y-2">
              {form.jobCaps.map((row, i) => (
                <JobCapItem
                  key={row.key}
                  row={row}
                  index={i}
                  errors={errors}
                  onChange={(change) => setJobCap(row.key, change)}
                  onRemove={() =>
                    set({
                      jobCaps: form.jobCaps.filter((r) => r.key !== row.key),
                    })
                  }
                />
              ))}
            </ul>
            <Button variant="outline" size="sm" onClick={() => set({ jobCaps: [...form.jobCaps, newJobCapRow()] })}>
              <PlusIcon aria-hidden="true" />
              Add a cap for a job type
            </Button>
          </section>

          <section aria-labelledby="prices-heading" className={SECTION}>
            <h2 id="prices-heading" className="text-lg font-semibold">
              Prices
            </h2>
            <p className="max-w-prose text-sm text-text-muted">
              USD per million tokens. A step of a model without a price has no cost recorded (and does not count toward the caps).
            </p>
            {errorsAt(errors, "prices").length > 0 ? <p className="text-xs text-text">{errorsAt(errors, "prices").join(" ")}</p> : null}
            <ul className="space-y-2">
              {form.prices.map((row, i) => (
                <li
                  key={row.key}
                  className="flex flex-wrap items-start gap-3 rounded-md border border-border p-3"
                  aria-label={`Price ${i + 1}`}
                >
                  <TextField
                    className="w-56"
                    label="Model"
                    path={`prices.${i}.model`}
                    errors={errors}
                    value={row.model}
                    list="budget-models"
                    spellCheck={false}
                    onChange={(v) => setPrice(row.key, { model: v })}
                  />
                  <TextField
                    className="w-36"
                    label="Input / Mtok"
                    inputMode="decimal"
                    path={`prices.${i}.input_per_mtok`}
                    errors={errors}
                    value={row.input}
                    onChange={(v) => setPrice(row.key, { input: v })}
                  />
                  <TextField
                    className="w-36"
                    label="Output / Mtok"
                    inputMode="decimal"
                    path={`prices.${i}.output_per_mtok`}
                    errors={errors}
                    value={row.output}
                    onChange={(v) => setPrice(row.key, { output: v })}
                  />
                  <Button
                    variant="ghost"
                    size="icon"
                    className="mt-6"
                    aria-label={`Remove the price of ${row.model || `row ${i + 1}`}`}
                    onClick={() =>
                      set({
                        prices: form.prices.filter((r) => r.key !== row.key),
                      })
                    }
                  >
                    <Trash2Icon aria-hidden="true" />
                  </Button>
                </li>
              ))}
            </ul>
            <Button variant="outline" size="sm" onClick={() => set({ prices: [...form.prices, newPriceRow()] })}>
              <PlusIcon aria-hidden="true" />
              Add a model price
            </Button>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <TextField
                label="Web search (USD per 1,000)"
                inputMode="decimal"
                path="web_search_per_1000"
                errors={errors}
                value={form.webSearch}
                onChange={(v) => set({ webSearch: v })}
              />
              <TextField
                label="Cache write multiplier"
                inputMode="decimal"
                path="cache_write_multiplier"
                errors={errors}
                value={form.cacheWrite}
                onChange={(v) => set({ cacheWrite: v })}
                hint="× the input price."
              />
              <TextField
                label="Cache read multiplier"
                inputMode="decimal"
                path="cache_read_multiplier"
                errors={errors}
                value={form.cacheRead}
                onChange={(v) => set({ cacheRead: v })}
                hint="× the input price."
              />
              <TextField
                label="Batch multiplier"
                inputMode="decimal"
                path="batch_multiplier"
                errors={errors}
                value={form.batch}
                onChange={(v) => set({ batch: v })}
                hint="0–1, × the token price of Batch API requests."
              />
            </div>
          </section>
          <datalist id="budget-models">
            {MODEL_SUGGESTIONS.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>

          <SaveBar
            dirty={dirty}
            savedVersionId={editor.version?.id ?? null}
            blocked={blocked}
            busy={editor.busy}
            errors={errors}
            message={blocked ? "Fix the fields marked above before saving." : editor.message}
            onValidate={() => body && void editor.validate(body)}
            onSave={(note) => void save(note)}
          />
        </>
      ) : null}
    </div>
  );
}

function JobCapItem({
  row,
  index,
  errors,
  onChange,
  onRemove,
}: {
  row: JobCapRow;
  index: number;
  errors: FieldErrors;
  onChange: (change: Partial<JobCapRow>) => void;
  onRemove: () => void;
}) {
  const messages = [
    ...errorsAt(errors, `per_job_cap_usd.row${index}`),
    ...(row.jobType ? errorsAt(errors, `per_job_cap_usd.${row.jobType}`) : []),
  ];
  const options: { value: JobType | ""; label: string }[] = [{ value: "", label: "Choose a job type" }, ...JOB_OPTIONS];
  return (
    <li className="flex flex-wrap items-start gap-3 rounded-md border border-border p-3" aria-label={`Run cap ${index + 1}`}>
      <SelectField
        className="w-64"
        label="Job type"
        path={`per_job_cap_usd.row${index}.job`}
        errors={{}}
        value={row.jobType}
        options={options}
        onChange={(v) => onChange({ jobType: v })}
      />
      <TextField
        className="w-40"
        label="Cap per run (USD)"
        inputMode="decimal"
        path={`per_job_cap_usd.row${index}.cap`}
        errors={{}}
        value={row.cap}
        onChange={(v) => onChange({ cap: v })}
        aria-invalid={messages.length > 0 || undefined}
      />
      <Button
        variant="ghost"
        size="icon"
        className="mt-6"
        aria-label={`Remove the cap for ${row.jobType || `row ${index + 1}`}`}
        onClick={onRemove}
      >
        <Trash2Icon aria-hidden="true" />
      </Button>
      {messages.length > 0 ? (
        <ul className="basis-full space-y-0.5 text-xs text-text" role="alert">
          {messages.map((m) => (
            <li key={m} className="flex gap-1">
              <span aria-hidden="true" className="text-warning-signal">
                ●
              </span>
              {m}
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}
