"use client";

import { ArrowRightIcon, LoaderCircleIcon, PlusIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ConfigHeader } from "@/components/config/config-header";
import { CheckboxField, SelectField, TextAreaField, TextField } from "@/components/config/fields";
import { SaveBar } from "@/components/config/save-bar";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type Holding, type StrategyConfig } from "@/lib/api/client";
import { errorsAt, type FieldErrors } from "@/lib/api/validation-errors";
import {
  FX_CHOICES,
  mergeErrors,
  newCategory,
  newCategoryTarget,
  newClassification,
  newPositionTarget,
  newRule,
  normalizeStrategyErrors,
  ON_BREACH,
  RULE_OPERATORS,
  RULE_TYPES,
  strategyFromBody,
  strategyToBody,
  targetSumErrors,
  targetSums,
  WEIGHT_BASES,
  type CategoryTargetForm,
  type ClassificationForm,
  type PositionTargetForm,
  type RuleForm,
  type StrategyForm,
} from "@/lib/config/strategy-form";
import { formatPercent } from "@/lib/format";
import { configSaveQuery, useConfigEditor } from "@/lib/hooks/use-config-editor";
import { cn } from "@/lib/utils";

const api = () => getBrowserApiClient();
const OPERATOR_OPTIONS = RULE_OPERATORS.map((o) => ({ value: o, label: o }));
const SECTION = "space-y-3 rounded-lg border border-border bg-surface-raised p-4";

export function StrategyEditor() {
  const [form, setForm] = useState<StrategyForm | null>(null);
  const [baseline, setBaseline] = useState("");
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [savedId, setSavedId] = useState<number | null>(null);
  const editor = useConfigEditor<StrategyConfig>({
    load: () => api().GET("/config/strategy"),
    save: (body, note, base) => api().PUT("/config/strategy", { body, params: { query: configSaveQuery(note, base) } }),
    validate: (body) => api().POST("/config/strategy/validate", { body }),
    onLoaded: (body) => {
      const loaded = strategyFromBody(body);
      setForm(loaded);
      setBaseline(body ? JSON.stringify(strategyToBody(loaded)) : "");
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

  const body = useMemo(() => (form ? strategyToBody(form) : null), [form]);
  const dirty = body !== null && JSON.stringify(body) !== baseline;
  const clientErrors = useMemo(() => (body ? targetSumErrors(body) : {}), [body]);
  const errors = useMemo(
    () => mergeErrors(clientErrors, normalizeStrategyErrors(editor.errors)),
    [clientErrors, editor.errors],
  );
  const blocked = Object.keys(clientErrors).length > 0;

  const set = (change: Partial<StrategyForm>) => setForm((f) => (f ? { ...f, ...change } : f));

  async function save(note: string | null) {
    if (!body || blocked) return;
    setSavedId(null);
    const saved = await editor.save(body, note);
    if (saved) {
      setBaseline(JSON.stringify(body));
      setSavedId(saved.id);
    }
  }

  return (
    <div className="space-y-6">
      <ConfigHeader title="Strategy" domain="strategy">
        <p>
          Base currency, target allocation with bands, limits and the rules the code checks. Drift
          on the <Link href="/" className="text-accent hover:underline">Overview</Link> and
          Holdings is computed against the active version, so a saved change shows there at once.
          Weights are typed as percentages: 10 means 10 %.
        </p>
      </ConfigHeader>

      {editor.status.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading strategy…
        </p>
      ) : null}
      {editor.status.kind === "error" ? (
        <AlertMessage title="Could not load the strategy">{editor.status.message}</AlertMessage>
      ) : null}
      {editor.status.kind === "ready" && editor.status.version === null ? (
        <AlertMessage tone="info" title="No strategy saved yet">
          These are the defaults. Save them, or change them first; until then the backend uses a CZK
          base with no targets.
        </AlertMessage>
      ) : null}

      {form ? (
        <>
          <BasicsSection form={form} errors={errors} set={set} />
          <CategoriesSection form={form} errors={errors} set={set} holdings={holdings} />
          <TargetsSection form={form} body={body!} errors={errors} set={set} holdings={holdings} />
          <RulesSection form={form} errors={errors} set={set} />

          {savedId !== null && !dirty ? (
            <AlertMessage tone="info" title={`Version #${savedId} is active`}>
              Drift now uses these targets.{" "}
              <Link href="/" className="inline-flex items-center gap-1 text-accent hover:underline">
                See the Overview
                <ArrowRightIcon aria-hidden="true" className="size-3.5" />
              </Link>
            </AlertMessage>
          ) : null}
          <SaveBar
            dirty={dirty}
            savedVersionId={editor.version?.id ?? null}
            blocked={blocked}
            busy={editor.busy}
            errors={errors}
            message={blocked ? "Fix the targets above 100 % before saving." : editor.message}
            onValidate={() => body && void editor.validate(body)}
            onSave={(note) => void save(note)}
          />
        </>
      ) : null}
    </div>
  );
}

type SectionProps = {
  form: StrategyForm;
  errors: FieldErrors;
  set: (change: Partial<StrategyForm>) => void;
};

function BasicsSection({ form, errors, set }: SectionProps) {
  return (
    <section aria-labelledby="basics-heading" className={SECTION}>
      <h2 id="basics-heading" className="text-[22px] leading-tight font-semibold">
        Basics
      </h2>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <TextField
          label="Base currency"
          path="base_currency"
          errors={errors}
          value={form.base_currency}
          maxLength={3}
          autoCapitalize="characters"
          onChange={(v) => set({ base_currency: v.toUpperCase() })}
          hint="ISO code; every value is converted to it."
        />
        <SelectField
          label="FX rates"
          path="fx_source"
          errors={errors}
          value={form.fx_source}
          options={FX_CHOICES}
          onChange={(v) => set({ fx_source: v })}
        />
        <SelectField
          label="Weights measured against"
          path="weight_basis"
          errors={errors}
          value={form.weight_basis}
          options={WEIGHT_BASES}
          onChange={(v) => set({ weight_basis: v })}
        />
        <TextField
          label="Horizon (years)"
          path="horizon_years"
          errors={errors}
          value={form.horizon_years}
          inputMode="numeric"
          onChange={(v) => set({ horizon_years: v })}
          hint="Optional, 1 to 100."
        />
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <TextAreaField label="Goals" path="goals" errors={errors} value={form.goals} onChange={(v) => set({ goals: v })} />
        <TextAreaField
          label="Notes for the AI"
          path="notes"
          errors={errors}
          value={form.notes}
          onChange={(v) => set({ notes: v })}
          hint="Free-text guidance the AI reads; never evaluated by code."
        />
      </div>
    </section>
  );
}

function CategoriesSection({ form, errors, set, holdings }: SectionProps & { holdings: Holding[] }) {
  const listPrefix = useId();
  const unclassified = holdings.filter((h) => !form.classifications.some((c) => c.ticker.trim() === h.ticker));
  // Errors on a rendered ticker or cell show there; the rest (e.g. a duplicate) are listed below the table.
  const shown = new Set(
    form.classifications.flatMap((r) => [
      `classifications.${r.ticker}`,
      ...form.categories.map((c) => `classifications.${r.ticker}.${c.name}`),
    ]),
  );
  const classificationErrors = Object.entries(errors).filter(
    ([p]) => (p === "classifications" || p.startsWith("classifications.")) && !shown.has(p),
  );
  const setRow = (key: string, change: (row: ClassificationForm) => ClassificationForm) =>
    set({ classifications: form.classifications.map((r) => (r.key === key ? change(r) : r)) });
  const bucketsOf = (categoryKey: string) =>
    [...new Set(form.classifications.map((r) => r.buckets[categoryKey]?.trim()).filter(Boolean))].sort();

  return (
    <section aria-labelledby="categories-heading" className={SECTION}>
      <h2 id="categories-heading" className="text-[22px] leading-tight font-semibold">
        Categories and classifications
      </h2>
      <p className="max-w-prose text-sm text-text-muted">
        Dimensions for allocation (lowercase, e.g. sector, geography). Put each holding in one bucket
        per dimension; an empty cell leaves it unclassified. <span className="font-mono">currency</span>{" "}
        is derived from the instrument unless you set it here.
      </p>
      <div className="flex flex-wrap items-start gap-2">
        {form.categories.map((c, i) => (
          <div key={c.key} className="flex items-start gap-1">
            <TextField
              className="w-40"
              label={`Dimension ${i + 1}`}
              path={`categories.${i}`}
              errors={errors}
              value={c.name}
              onChange={(v) =>
                set({ categories: form.categories.map((x) => (x.key === c.key ? { ...x, name: v.toLowerCase() } : x)) })
              }
            />
            <Button
              variant="ghost"
              size="icon"
              className="mt-6"
              aria-label={`Remove dimension ${c.name || i + 1}`}
              onClick={() => set({ categories: form.categories.filter((x) => x.key !== c.key) })}
            >
              <Trash2Icon aria-hidden="true" />
            </Button>
          </div>
        ))}
        <Button variant="outline" className="mt-6" onClick={() => set({ categories: [...form.categories, newCategory()] })}>
          <PlusIcon aria-hidden="true" />
          Add dimension
        </Button>
      </div>

      {form.categories.length > 0 ? (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full min-w-[40rem] text-sm">
            <caption className="sr-only">Bucket per dimension for each holding</caption>
            <thead className="bg-surface-muted">
              <tr className="border-b border-border text-left text-text-muted">
                <th scope="col" className="px-2 py-2 font-medium">
                  Ticker
                </th>
                {form.categories.map((c, i) => (
                  <th key={c.key} scope="col" className="px-2 py-2 font-medium">
                    {c.name || `Dimension ${i + 1}`}
                  </th>
                ))}
                <th scope="col" className="w-10 px-2 py-2">
                  <span className="sr-only">Remove</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {form.classifications.map((row) => (
                <tr key={row.key} className="border-b border-border last:border-0">
                  <td className="px-2 py-1.5 align-top">
                    <TextField
                      hideLabel
                      label="Ticker"
                      path={`classifications.${row.ticker}`}
                      errors={errors}
                      value={row.ticker}
                      className="w-40"
                      onChange={(v) => setRow(row.key, (r) => ({ ...r, ticker: v.trim() }))}
                    />
                  </td>
                  {form.categories.map((c, i) => (
                    <td key={c.key} className="px-2 py-1.5 align-top">
                      <TextField
                        hideLabel
                        label={`${row.ticker || "Holding"} ${c.name || `dimension ${i + 1}`}`}
                        path={`classifications.${row.ticker}.${c.name}`}
                        errors={errors}
                        value={row.buckets[c.key] ?? ""}
                        list={`${listPrefix}-${c.key}`}
                        onChange={(v) => setRow(row.key, (r) => ({ ...r, buckets: { ...r.buckets, [c.key]: v } }))}
                      />
                    </td>
                  ))}
                  <td className="px-2 py-1.5 align-top">
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Remove ${row.ticker || "row"}`}
                      onClick={() => set({ classifications: form.classifications.filter((r) => r.key !== row.key) })}
                    >
                      <Trash2Icon aria-hidden="true" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {form.categories.map((c) => (
            <datalist key={c.key} id={`${listPrefix}-${c.key}`}>
              {bucketsOf(c.key).map((b) => (
                <option key={b} value={b} />
              ))}
            </datalist>
          ))}
        </div>
      ) : null}
      {classificationErrors.length > 0 ? (
        <ul className="space-y-0.5 text-xs text-text">
          {classificationErrors.flatMap(([p, ms]) =>
            ms.map((m) => (
              <li key={`${p}:${m}`} className="flex gap-1">
                <span aria-hidden="true" className="text-warning-signal">
                  ●
                </span>
                <span className="font-mono">{p}</span> {m}
              </li>
            )),
          )}
        </ul>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => set({ classifications: [...form.classifications, newClassification()] })}>
          <PlusIcon aria-hidden="true" />
          Add holding
        </Button>
        {unclassified.slice(0, 8).map((h) => (
          <Button
            key={h.ticker}
            variant="ghost"
            size="sm"
            onClick={() => set({ classifications: [...form.classifications, newClassification(h.ticker)] })}
          >
            <PlusIcon aria-hidden="true" />
            {h.name ?? h.ticker}
          </Button>
        ))}
      </div>
    </section>
  );
}

function SumLine({ label, total }: { label: string; total: number }) {
  const over = total > 1 + 1e-9;
  return (
    <p className={cn("text-sm", over ? "font-medium text-text" : "text-text-muted")}>
      {label}: <span className="num">{formatPercent(total, { decimals: 2 })}</span> of 100 %
      {over ? (
        <span className="ml-1 inline-flex items-center gap-1">
          <span aria-hidden="true" className="text-warning-signal">
            ●
          </span>
          over the limit
        </span>
      ) : null}
    </p>
  );
}

function TargetsSection({
  form,
  body,
  errors,
  set,
  holdings,
}: SectionProps & { body: StrategyConfig; holdings: Holding[] }) {
  const sums = targetSums(body);
  const modelErrors = errorsAt(errors, "");
  const untargeted = holdings.filter((h) => !form.target_positions.some((p) => p.ticker.trim() === h.ticker));
  const dimensionNames = [...new Set(form.categories.map((c) => c.name.trim()).filter(Boolean))];
  const setPosition = (key: string, change: Partial<PositionTargetForm>) =>
    set({ target_positions: form.target_positions.map((p) => (p.key === key ? { ...p, ...change } : p)) });
  const setCategory = (key: string, change: Partial<CategoryTargetForm>) =>
    set({ target_categories: form.target_categories.map((c) => (c.key === key ? { ...c, ...change } : c)) });

  return (
    <section aria-labelledby="targets-heading" className={SECTION}>
      <h2 id="targets-heading" className="text-[22px] leading-tight font-semibold">
        Targets
      </h2>
      <p className="max-w-prose text-sm text-text-muted">
        Target weight and the band around it (± percentage points). Outside the band a position
        shows as drifting; above its hard limit it raises an alert. Position and cash targets
        together, and the targets of each dimension, may not exceed 100 %.
      </p>
      {modelErrors.length > 0 ? (
        <AlertMessage title="The backend rejected the strategy">
          <ul className="list-disc pl-4">
            {modelErrors.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        </AlertMessage>
      ) : null}

      <div className="flex flex-wrap gap-3">
        <SelectField
          className="w-72"
          label="Above a hard limit"
          path="hard_limit_severity"
          errors={errors}
          value={form.hard_limit_severity}
          options={ON_BREACH}
          onChange={(v) => set({ hard_limit_severity: v })}
          hint="Alerted when it opens, routed by Config: Notifications."
        />
        <SelectField
          className="w-72"
          label="Outside a band"
          path="band_breach_severity"
          errors={errors}
          value={form.band_breach_severity}
          options={ON_BREACH}
          onChange={(v) => set({ band_breach_severity: v })}
          hint="Severity 1 is listed in the digest only; 2 or 3 also alert when it opens."
        />
      </div>

      <div className="space-y-2">
        <h3 className="font-medium">Cash</h3>
        <CheckboxField label="Set a cash target" checked={form.cash_enabled} onChange={(v) => set({ cash_enabled: v })} />
        {form.cash_enabled ? (
          <div className="flex flex-wrap gap-3">
            <TextField
              className="w-36"
              label="Cash target (%)"
              path="cash_target.target_weight"
              errors={errors}
              inputMode="decimal"
              value={form.cash_target_weight}
              onChange={(v) => set({ cash_target_weight: v })}
            />
            <TextField
              className="w-36"
              label="Band (± pp)"
              path="cash_target.band"
              errors={errors}
              inputMode="decimal"
              value={form.cash_band}
              onChange={(v) => set({ cash_band: v })}
            />
          </div>
        ) : null}
      </div>

      <div className="space-y-2">
        <h3 className="font-medium">Positions</h3>
        {form.target_positions.length === 0 ? (
          <p className="text-sm text-text-muted">No position targets: every position shows &ldquo;No target&rdquo;.</p>
        ) : null}
        {form.target_positions.map((p, i) => (
          <div key={p.key} className="flex flex-wrap items-start gap-3 rounded-md border border-border p-3">
            <TextField className="w-48" label="Ticker" path={`target_positions.${i}.ticker`} errors={errors} value={p.ticker} onChange={(v) => setPosition(p.key, { ticker: v.trim() })} />
            <TextField className="w-36" label="Target (%)" inputMode="decimal" path={`target_positions.${i}.target_weight`} errors={errors} value={p.target_weight} onChange={(v) => setPosition(p.key, { target_weight: v })} />
            <TextField className="w-36" label="Band (± pp)" inputMode="decimal" path={`target_positions.${i}.band`} errors={errors} value={p.band} onChange={(v) => setPosition(p.key, { band: v })} />
            <TextField className="w-36" label="Hard limit (%)" inputMode="decimal" path={`target_positions.${i}.hard_limit`} errors={errors} value={p.hard_limit} onChange={(v) => setPosition(p.key, { hard_limit: v })} hint="Optional." />
            {errorsAt(errors, `target_positions.${i}`).length > 0 ? (
              <p className="basis-full text-xs text-text">{errorsAt(errors, `target_positions.${i}`).join(" ")}</p>
            ) : null}
            <Button variant="ghost" size="icon" className="mt-6" aria-label={`Remove target ${p.ticker || i + 1}`} onClick={() => set({ target_positions: form.target_positions.filter((x) => x.key !== p.key) })}>
              <Trash2Icon aria-hidden="true" />
            </Button>
          </div>
        ))}
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => set({ target_positions: [...form.target_positions, newPositionTarget()] })}>
            <PlusIcon aria-hidden="true" />
            Add position target
          </Button>
          {untargeted.slice(0, 8).map((h) => (
            <Button key={h.ticker} variant="ghost" size="sm" onClick={() => set({ target_positions: [...form.target_positions, newPositionTarget(h.ticker)] })}>
              <PlusIcon aria-hidden="true" />
              {h.name ?? h.ticker}
            </Button>
          ))}
        </div>
        <SumLine label="Positions and cash" total={sums.positionsAndCash} />
      </div>

      <div className="space-y-2">
        <h3 className="font-medium">Categories</h3>
        {form.target_categories.map((c, i) => {
          const options = [
            { value: "", label: "Choose…" },
            ...[...new Set([...dimensionNames, c.dimension].filter(Boolean))].map((d) => ({ value: d, label: d })),
          ];
          return (
            <div key={c.key} className="flex flex-wrap items-start gap-3 rounded-md border border-border p-3">
              <SelectField className="w-44" label="Dimension" path={`target_categories.${i}.dimension`} errors={errors} value={c.dimension} options={options} onChange={(v) => setCategory(c.key, { dimension: v })} />
              <TextField className="w-48" label="Bucket" path={`target_categories.${i}.bucket`} errors={errors} value={c.bucket} onChange={(v) => setCategory(c.key, { bucket: v })} />
              <TextField className="w-36" label="Target (%)" inputMode="decimal" path={`target_categories.${i}.target_weight`} errors={errors} value={c.target_weight} onChange={(v) => setCategory(c.key, { target_weight: v })} />
              <TextField className="w-36" label="Band (± pp)" inputMode="decimal" path={`target_categories.${i}.band`} errors={errors} value={c.band} onChange={(v) => setCategory(c.key, { band: v })} />
              <Button variant="ghost" size="icon" className="mt-6" aria-label={`Remove target ${c.bucket || i + 1}`} onClick={() => set({ target_categories: form.target_categories.filter((x) => x.key !== c.key) })}>
                <Trash2Icon aria-hidden="true" />
              </Button>
            </div>
          );
        })}
        <Button variant="outline" onClick={() => set({ target_categories: [...form.target_categories, newCategoryTarget(dimensionNames[0] ?? "")] })}>
          <PlusIcon aria-hidden="true" />
          Add category target
        </Button>
        {Object.entries(sums.byDimension).map(([dimension, total]) => (
          <SumLine key={dimension} label={dimension || "No dimension"} total={total} />
        ))}
      </div>
    </section>
  );
}

function RulesSection({ form, errors, set }: SectionProps) {
  const dimensionNames = [...new Set(form.categories.map((c) => c.name.trim()).filter(Boolean))];
  const setRule = (key: string, change: Partial<RuleForm>) =>
    set({ rules: form.rules.map((r) => (r.key === key ? { ...r, ...change } : r)) });

  return (
    <section aria-labelledby="rules-heading" className="space-y-3">
      <div>
        <h2 id="rules-heading" className="text-[22px] leading-tight font-semibold">
          Rules
        </h2>
        <p className="max-w-prose text-sm text-text-muted">
          Limits the code checks after every sync. A breached rule is raised at its severity.
        </p>
      </div>
      {form.rules.length === 0 ? <EmptyState title="No rules">Add one, e.g. no single position above 10 %.</EmptyState> : null}
      {form.rules.map((r, i) => {
        const typeInfo = RULE_TYPES.find((t) => t.value === r.type);
        const isCount = r.type === "position_count";
        const dimensionOptions = [
          { value: "", label: "Choose…" },
          ...[...new Set([...dimensionNames, r.dimension].filter(Boolean))].map((d) => ({ value: d, label: d })),
        ];
        const ruleErrors = errorsAt(errors, `rules.${i}`);
        return (
          <div key={r.key} className="space-y-3 rounded-lg border border-border bg-surface-raised p-4">
            <div className="flex flex-wrap items-start gap-3">
              <TextField className="w-56" label="Rule id" path={`rules.${i}.id`} errors={errors} value={r.id} onChange={(v) => setRule(r.key, { id: v })} hint="Lowercase words joined by hyphens." />
              <SelectField className="w-48" label="Checks" path={`rules.${i}.type`} errors={errors} value={r.type} options={RULE_TYPES} onChange={(v) => setRule(r.key, { type: v })} />
              {r.type === "position_weight" ? (
                <TextField className="w-44" label="Ticker (optional)" path={`rules.${i}.ticker`} errors={errors} value={r.ticker} onChange={(v) => setRule(r.key, { ticker: v.trim() })} />
              ) : null}
              {r.type === "category_weight" ? (
                <>
                  <SelectField className="w-40" label="Dimension" path={`rules.${i}.dimension`} errors={errors} value={r.dimension} options={dimensionOptions} onChange={(v) => setRule(r.key, { dimension: v })} />
                  <TextField className="w-44" label="Bucket (optional)" path={`rules.${i}.bucket`} errors={errors} value={r.bucket} onChange={(v) => setRule(r.key, { bucket: v })} />
                </>
              ) : null}
              <SelectField className="w-20" label="Operator" path={`rules.${i}.operator`} errors={errors} value={r.operator} options={OPERATOR_OPTIONS} onChange={(v) => setRule(r.key, { operator: v })} />
              <TextField className="w-32" label={isCount ? "Positions" : "Value (%)"} inputMode={isCount ? "numeric" : "decimal"} path={`rules.${i}.value`} errors={errors} value={r.value} onChange={(v) => setRule(r.key, { value: v })} />
              <SelectField className="w-60" label="On breach" path={`rules.${i}.on_breach`} errors={errors} value={r.on_breach} options={ON_BREACH} onChange={(v) => setRule(r.key, { on_breach: v })} />
              <Button variant="ghost" size="icon" className="mt-6" aria-label={`Remove rule ${r.id || i + 1}`} onClick={() => set({ rules: form.rules.filter((x) => x.key !== r.key) })}>
                <Trash2Icon aria-hidden="true" />
              </Button>
            </div>
            {typeInfo ? <p className="text-xs text-text-muted">{typeInfo.hint}</p> : null}
            <TextField label="Description (optional)" path={`rules.${i}.description`} errors={errors} value={r.description} onChange={(v) => setRule(r.key, { description: v })} />
            {ruleErrors.length > 0 ? <p className="text-xs text-text">{ruleErrors.join(" ")}</p> : null}
          </div>
        );
      })}
      <Button variant="outline" onClick={() => set({ rules: [...form.rules, newRule()] })}>
        <PlusIcon aria-hidden="true" />
        Add rule
      </Button>
    </section>
  );
}
