"use client";

import { ArrowRightIcon, LoaderCircleIcon, PencilIcon, PlusIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useId, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Ring } from "@/components/charts";
import { ConfigHeader } from "@/components/config/config-header";
import { CheckboxField, SelectField, TextAreaField, TextField } from "@/components/config/fields";
import { SaveBar } from "@/components/config/save-bar";
import { TargetMeter, TargetMeterLegend } from "@/components/config/target-meter";
import { ToggleSwitch } from "@/components/config/toggle-switch";
import { EmptyState } from "@/components/empty-state";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import {
  getBrowserApiClient,
  type ConfigVersionMeta,
  type DriftReport,
  type Holding,
  type StrategyConfig,
} from "@/lib/api/client";
import { errorsAt, type FieldErrors } from "@/lib/api/validation-errors";
import { historyHref } from "@/lib/config/domains";
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
import {
  allocation,
  BAND_STATE_LABEL,
  bandState,
  changesSummary,
  numeric,
  ruleSummary,
  severityLabel,
  strategyChanges,
  targetScale,
  versionNumbers,
  type BandState,
} from "@/lib/config/strategy-view";
import { formatDate, formatPercent } from "@/lib/format";
import { configSaveQuery, useConfigEditor } from "@/lib/hooks/use-config-editor";
import { cn } from "@/lib/utils";

const api = () => getBrowserApiClient();
const OPERATOR_OPTIONS = RULE_OPERATORS.map((o) => ({ value: o, label: o }));
const PANEL = "space-y-4 p-5";
const TABLE_HEAD = "bg-surface-muted text-[11px] tracking-[0.08em] text-text-muted uppercase";
/** Compact, right-aligned inputs inside the target tables. */
const CELL = "[&_input]:h-8 [&_input]:px-2 [&_input]:text-right";

export function StrategyEditor() {
  const [form, setForm] = useState<StrategyForm | null>(null);
  /** The active body as the form would send it (so key order and defaults compare equal). */
  const [baseline, setBaseline] = useState<StrategyConfig | null>(null);
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [drift, setDrift] = useState<DriftReport | null>(null);
  const [versions, setVersions] = useState<ConfigVersionMeta[] | null>(null);
  const [savedId, setSavedId] = useState<number | null>(null);
  const editor = useConfigEditor<StrategyConfig>({
    load: () => api().GET("/config/strategy"),
    save: (body, note, base) => api().PUT("/config/strategy", { body, params: { query: configSaveQuery(note, base) } }),
    validate: (body) => api().POST("/config/strategy/validate", { body }),
    onLoaded: (body) => {
      const loaded = strategyFromBody(body);
      setForm(loaded);
      setBaseline(body ? strategyToBody(loaded) : null);
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
    api()
      .GET("/portfolio/overview")
      .then((r) => {
        if (!cancelled && r.data) setDrift(r.data.drift);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  // The version list, re-read after every save.
  const activeId = editor.version?.id ?? null;
  useEffect(() => {
    let cancelled = false;
    api()
      .GET("/config/{domain}/versions", { params: { path: { domain: "strategy" } } })
      .then((r) => {
        if (!cancelled && r.data) setVersions(r.data);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [activeId]);

  const body = useMemo(() => (form ? strategyToBody(form) : null), [form]);
  const baselineText = baseline ? JSON.stringify(baseline) : "";
  const dirty = body !== null && JSON.stringify(body) !== baselineText;
  const changes = useMemo(() => (body && dirty ? strategyChanges(baseline, body) : []), [baseline, body, dirty]);
  const clientErrors = useMemo(() => (body ? targetSumErrors(body) : {}), [body]);
  const errors = useMemo(
    () => mergeErrors(clientErrors, normalizeStrategyErrors(editor.errors)),
    [clientErrors, editor.errors],
  );
  const blocked = Object.keys(clientErrors).length > 0;
  const numbers = useMemo(() => versionNumbers(versions ?? []), [versions]);
  const activeNumber = activeId !== null ? numbers.get(activeId) : undefined;
  const nextNumber = versions ? versions.length + 1 : null;

  const set = useCallback((change: Partial<StrategyForm>) => setForm((f) => (f ? { ...f, ...change } : f)), []);

  async function save(note: string | null) {
    if (!body || blocked) return;
    setSavedId(null);
    const saved = await editor.save(body, note);
    if (saved) {
      setBaseline(body);
      setSavedId(saved.id);
    }
  }

  function discard() {
    setForm(strategyFromBody(baseline));
  }

  return (
    <div className="space-y-5">
      <ConfigHeader
        title="Strategy"
        domain="strategy"
        aside={
          editor.version ? (
            <span className="inline-flex items-center gap-2">
              <span className="bg-accent-soft px-2 py-0.5 font-semibold text-accent-ink" title={`Version #${editor.version.id}`}>
                {activeNumber ? `v${activeNumber}` : `#${editor.version.id}`} active
              </span>
              <span className="num">since {formatDate(new Date(editor.version.created_at))}</span>
            </span>
          ) : null
        }
      >
        <p>
          Target weights, bands and the rules the code checks. Drift on the{" "}
          <Link href="/" className="text-accent-ink hover:underline">
            Overview
          </Link>{" "}
          and Holdings, alerts and the monthly review read from the active version, so a saved change shows there at
          once. Weights are typed as percentages: 10 means 10 %.
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

      {form && body ? (
        <>
          <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-5 xl:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
            <TargetsPanel form={form} body={body} errors={errors} set={set} holdings={holdings} drift={drift} />
            <div className="grid grid-cols-[minmax(0,1fr)] gap-5">
              <RulesPanel form={form} errors={errors} set={set} />
              <RiskPanel form={form} errors={errors} set={set} />
              <HistoryPanel
                versions={versions}
                numbers={numbers}
                dirty={dirty}
                changeCount={changes.length}
              />
            </div>
          </div>
          <CategoryTargetsPanel form={form} body={body} errors={errors} set={set} drift={drift} />
          <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-5">
            <BasicsSection form={form} errors={errors} set={set} />
            <CategoriesSection form={form} errors={errors} set={set} holdings={holdings} />
          </div>

          {savedId !== null && !dirty ? (
            <AlertMessage tone="info" title={`Version #${savedId} is active`}>
              Drift now uses these targets.{" "}
              <Link href="/" className="inline-flex items-center gap-1 text-accent-ink hover:underline">
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
            summary={changesSummary(changes)}
            onDiscard={discard}
            saveLabel={nextNumber ? `Save as v${nextNumber}` : "Save new version"}
            onValidate={() => void editor.validate(body)}
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

// ---------------------------------------------------------------------------
// Target weights
// ---------------------------------------------------------------------------

function stateMark(state: BandState): string {
  if (state === "above_band" || state === "above_limit") return "▲";
  if (state === "below_band") return "▼";
  return "";
}

function NowCell({ weight, state }: { weight: number | null; state: BandState }) {
  const out = state === "above_band" || state === "below_band" || state === "above_limit";
  return (
    <span
      className={cn("num block text-right text-[13px]", out ? "font-semibold text-accent-ink" : "text-text-muted")}
      title={weight === null ? "Not held" : `Now ${formatPercent(weight)} · ${BAND_STATE_LABEL[state]}`}
    >
      {weight === null ? "—" : formatPercent(weight)}
      {stateMark(state) ? <span aria-hidden="true"> {stateMark(state)}</span> : null}
      {out ? <span className="sr-only"> ({BAND_STATE_LABEL[state]})</span> : null}
    </span>
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

const POSITION_GRID =
  "grid grid-cols-[7.75rem_minmax(7rem,1fr)_4.25rem_4rem_4rem_4rem_2rem] items-start gap-x-2.5";

function TargetsPanel({
  form,
  body,
  errors,
  set,
  holdings,
  drift,
}: SectionProps & { body: StrategyConfig; holdings: Holding[]; drift: DriftReport | null }) {
  const sums = targetSums(body);
  const alloc = allocation(body);
  const modelErrors = errorsAt(errors, "");
  const untargeted = holdings.filter((h) => !form.target_positions.some((p) => p.ticker.trim() === h.ticker));
  const weightOf = (ticker: string) => numeric(drift?.positions.find((p) => p.ticker === ticker)?.weight);
  const cashNow = numeric(drift?.cash?.weight);
  const setPosition = (key: string, change: Partial<PositionTargetForm>) =>
    set({ target_positions: form.target_positions.map((p) => (p.key === key ? { ...p, ...change } : p)) });

  const rows = (body.target_positions ?? []).map((p) => ({
    target: numeric(p.target_weight),
    band: numeric(p.band),
    hardLimit: numeric(p.hard_limit),
    current: weightOf(p.ticker),
  }));
  const cash = body.cash_target
    ? { target: numeric(body.cash_target.target_weight), band: numeric(body.cash_target.band), current: cashNow }
    : null;
  const max = targetScale([
    ...rows.flatMap((r) => [r.target !== null ? r.target + (r.band ?? 0) : null, r.hardLimit, r.current]),
    cash?.target !== null && cash ? cash.target + (cash.band ?? 0) : null,
    cash?.current,
  ]);
  const over = alloc.total > 1 + 1e-9;

  return (
    <Blueprint aria-labelledby="targets-heading">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
        <h2 id="targets-heading" className="text-[22px] leading-tight font-semibold">
          Target weights
        </h2>
        <div className="flex items-center gap-2.5">
          <Ring value={Math.min(alloc.total, 1) * 100} label="" size={40} ariaLabel="Allocated by position and cash targets" tone={over ? "warning" : "accent"} />
          <div className="text-[13px]">
            <b className="num font-semibold">{formatPercent(alloc.total)}</b> allocated
            {over ? (
              <span className="ml-1 inline-flex items-center gap-1 font-medium">
                <span aria-hidden="true" className="text-warning-signal">
                  ●
                </span>
                over 100 %
              </span>
            ) : null}
            <div className="num text-[11px] text-text-muted">
              {alloc.cash === null ? "no cash target" : `incl. cash ${formatPercent(alloc.cash)}`}
            </div>
          </div>
        </div>
      </div>
      <p className="px-5 pt-3 text-sm text-text-muted">
        Target weight and the band around it (± percentage points). Outside the band a position shows as drifting;
        above its hard limit it raises an alert. Position and cash targets together may not exceed 100 %.
        {drift ? " The tick is today’s weight from the Overview." : null}
      </p>
      {modelErrors.length > 0 ? (
        <div className="px-5 pt-3">
          <AlertMessage title="The backend rejected the strategy">
            <ul className="list-disc pl-4">
              {modelErrors.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          </AlertMessage>
        </div>
      ) : null}

      <div className="relative mt-3 overflow-x-auto">
        <div className="min-w-[38rem]" role="table" aria-label="Position and cash targets">
          <div role="row" className={cn(POSITION_GRID, TABLE_HEAD, "px-5 py-2")}>
            <span role="columnheader">Holding</span>
            <span role="columnheader">Target &amp; band · 0–{formatPercent(max, { decimals: 0 })}</span>
            <span role="columnheader" className="text-right">
              Now
            </span>
            <span role="columnheader" className="text-right">
              Target %
            </span>
            <span role="columnheader" className="text-right">
              Band ±pp
            </span>
            <span role="columnheader" className="text-right">
              Limit %
            </span>
            <span role="columnheader">
              <span className="sr-only">Remove</span>
            </span>
          </div>
          {form.target_positions.length === 0 ? (
            <p className="px-5 py-3 text-sm text-text-muted">No position targets: every position shows &ldquo;No target&rdquo;.</p>
          ) : null}
          {form.target_positions.map((p, i) => {
            const r = rows[i];
            const name = p.ticker || `target ${i + 1}`;
            const rowErrors = errorsAt(errors, `target_positions.${i}`);
            return (
              <div key={p.key} role="row" className="border-b border-border-soft px-5 py-1.5">
                <div className={POSITION_GRID}>
                  <span role="cell">
                    <TextField
                      hideLabel
                      label={`Ticker of ${name}`}
                      path={`target_positions.${i}.ticker`}
                      errors={errors}
                      value={p.ticker}
                      className="[&_input]:h-8 [&_input]:px-2 [&_input]:font-mono [&_input]:text-xs"
                      onChange={(v) => setPosition(p.key, { ticker: v.trim() })}
                    />
                  </span>
                  <span role="cell" className="pt-[7px]">
                    <TargetMeter label={name} target={r.target} band={r.band} hardLimit={r.hardLimit} current={r.current} max={max} />
                  </span>
                  <span role="cell" className="pt-1.5">
                    <NowCell weight={r.current} state={bandState(r.current, r.target, r.band, r.hardLimit)} />
                  </span>
                  <span role="cell">
                    <TextField hideLabel label={`${name} target (%)`} inputMode="decimal" path={`target_positions.${i}.target_weight`} errors={errors} value={p.target_weight} className={cn(CELL, "[&_input]:font-semibold")} onChange={(v) => setPosition(p.key, { target_weight: v })} />
                  </span>
                  <span role="cell">
                    <TextField hideLabel label={`${name} band (± pp)`} inputMode="decimal" path={`target_positions.${i}.band`} errors={errors} value={p.band} className={CELL} onChange={(v) => setPosition(p.key, { band: v })} />
                  </span>
                  <span role="cell">
                    <TextField hideLabel label={`${name} hard limit (%), optional`} inputMode="decimal" placeholder="—" path={`target_positions.${i}.hard_limit`} errors={errors} value={p.hard_limit} className={CELL} onChange={(v) => setPosition(p.key, { hard_limit: v })} />
                  </span>
                  <span role="cell">
                    <Button variant="ghost" size="icon" className="size-8" aria-label={`Remove target ${p.ticker || i + 1}`} onClick={() => set({ target_positions: form.target_positions.filter((x) => x.key !== p.key) })}>
                      <Trash2Icon aria-hidden="true" />
                    </Button>
                  </span>
                </div>
                {rowErrors.length > 0 ? <p className="mt-1 text-xs text-text">{rowErrors.join(" ")}</p> : null}
              </div>
            );
          })}
          {form.cash_enabled && cash ? (
            <div role="row" className="border-b border-border-soft px-5 py-1.5">
              <div className={POSITION_GRID}>
                <span role="cell" className="pt-1.5 text-[13px] font-medium">
                  Cash
                </span>
                <span role="cell" className="pt-[7px]">
                  <TargetMeter label="Cash" target={cash.target} band={cash.band} current={cash.current} max={max} />
                </span>
                <span role="cell" className="pt-1.5">
                  <NowCell weight={cash.current} state={bandState(cash.current, cash.target, cash.band)} />
                </span>
                <span role="cell">
                  <TextField hideLabel label="Cash target (%)" path="cash_target.target_weight" errors={errors} inputMode="decimal" value={form.cash_target_weight} className={cn(CELL, "[&_input]:font-semibold")} onChange={(v) => set({ cash_target_weight: v })} />
                </span>
                <span role="cell">
                  <TextField hideLabel label="Cash band (± pp)" path="cash_target.band" errors={errors} inputMode="decimal" value={form.cash_band} className={CELL} onChange={(v) => set({ cash_band: v })} />
                </span>
                <span role="cell" />
                <span role="cell" />
              </div>
            </div>
          ) : null}
        </div>
      </div>

      <div className="space-y-4 px-5 pt-3 pb-5">
        <TargetMeterLegend />
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => set({ target_positions: [...form.target_positions, newPositionTarget()] })}>
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
        <CheckboxField label="Set a cash target" checked={form.cash_enabled} onChange={(v) => set({ cash_enabled: v })} />
        <SumLine label="Positions and cash" total={sums.positionsAndCash} />
        <div className="grid gap-3 border-t border-border-soft pt-4 sm:grid-cols-2">
          <SelectField
            label="Above a hard limit"
            path="hard_limit_severity"
            errors={errors}
            value={form.hard_limit_severity}
            options={ON_BREACH}
            onChange={(v) => set({ hard_limit_severity: v })}
            hint="Alerted when it opens, routed by Config: Notifications."
          />
          <SelectField
            label="Outside a band"
            path="band_breach_severity"
            errors={errors}
            value={form.band_breach_severity}
            options={ON_BREACH}
            onChange={(v) => set({ band_breach_severity: v })}
            hint="Severity 1 is listed in the digest only; 2 or 3 also alert when it opens."
          />
        </div>
      </div>
    </Blueprint>
  );
}

// ---------------------------------------------------------------------------
// Category targets
// ---------------------------------------------------------------------------

const CATEGORY_GRID =
  "grid grid-cols-[8.5rem_9rem_minmax(7rem,1fr)_4.25rem_4.5rem_4.5rem_2.25rem] items-start gap-x-3";

function CategoryTargetsPanel({ form, body, errors, set, drift }: SectionProps & { body: StrategyConfig; drift: DriftReport | null }) {
  const sums = targetSums(body);
  const dimensionNames = [...new Set(form.categories.map((c) => c.name.trim()).filter(Boolean))];
  const setCategory = (key: string, change: Partial<CategoryTargetForm>) =>
    set({ target_categories: form.target_categories.map((c) => (c.key === key ? { ...c, ...change } : c)) });
  const weightOf = (dimension: string, bucket: string) =>
    numeric(drift?.categories.find((c) => c.dimension === dimension)?.buckets.find((b) => b.bucket === bucket)?.weight);
  const rows = (body.target_categories ?? []).map((c) => ({
    target: numeric(c.target_weight),
    band: numeric(c.band),
    current: weightOf(c.dimension, c.bucket),
  }));
  const max = targetScale(rows.flatMap((r) => [r.target !== null ? r.target + (r.band ?? 0) : null, r.current]));

  return (
    <Blueprint aria-labelledby="category-targets-heading">
      <div className="space-y-1 px-5 pt-4 pb-3">
        <PanelHeading kicker="Allocation" title={<span id="category-targets-heading">Category targets</span>} />
        <p className="text-sm text-text-muted">
          Target and band per bucket of a dimension (e.g. geography · Europe). The targets of each dimension may not
          exceed 100 %.
        </p>
      </div>
      {form.target_categories.length > 0 ? (
        <div className="relative overflow-x-auto">
          <div className="min-w-[48rem]" role="table" aria-label="Category targets">
            <div role="row" className={cn(CATEGORY_GRID, TABLE_HEAD, "px-5 py-2")}>
              <span role="columnheader">Dimension</span>
              <span role="columnheader">Bucket</span>
              <span role="columnheader">Target &amp; band · 0–{formatPercent(max, { decimals: 0 })}</span>
              <span role="columnheader" className="text-right">
                Now
              </span>
              <span role="columnheader" className="text-right">
                Target %
              </span>
              <span role="columnheader" className="text-right">
                Band ±pp
              </span>
              <span role="columnheader">
                <span className="sr-only">Remove</span>
              </span>
            </div>
            {form.target_categories.map((c, i) => {
              const r = rows[i];
              const name = `${c.dimension || "dimension"} · ${c.bucket || `bucket ${i + 1}`}`;
              const options = [
                { value: "", label: "Choose…" },
                ...[...new Set([...dimensionNames, c.dimension].filter(Boolean))].map((d) => ({ value: d, label: d })),
              ];
              return (
                <div key={c.key} role="row" className={cn(CATEGORY_GRID, "border-b border-border-soft px-5 py-1.5")}>
                  <span role="cell">
                    <SelectField hideLabel label={`Dimension of category target ${i + 1}`} path={`target_categories.${i}.dimension`} errors={errors} value={c.dimension} options={options} className="[&_select]:h-8" onChange={(v) => setCategory(c.key, { dimension: v })} />
                  </span>
                  <span role="cell">
                    <TextField hideLabel label={`Bucket of category target ${i + 1}`} path={`target_categories.${i}.bucket`} errors={errors} value={c.bucket} className="[&_input]:h-8 [&_input]:px-2" onChange={(v) => setCategory(c.key, { bucket: v })} />
                  </span>
                  <span role="cell" className="pt-[7px]">
                    <TargetMeter label={name} target={r.target} band={r.band} current={r.current} max={max} />
                  </span>
                  <span role="cell" className="pt-1.5">
                    <NowCell weight={r.current} state={bandState(r.current, r.target, r.band)} />
                  </span>
                  <span role="cell">
                    <TextField hideLabel label={`${name} target (%)`} inputMode="decimal" path={`target_categories.${i}.target_weight`} errors={errors} value={c.target_weight} className={cn(CELL, "[&_input]:font-semibold")} onChange={(v) => setCategory(c.key, { target_weight: v })} />
                  </span>
                  <span role="cell">
                    <TextField hideLabel label={`${name} band (± pp)`} inputMode="decimal" path={`target_categories.${i}.band`} errors={errors} value={c.band} className={CELL} onChange={(v) => setCategory(c.key, { band: v })} />
                  </span>
                  <span role="cell">
                    <Button variant="ghost" size="icon" className="size-8" aria-label={`Remove target ${c.bucket || i + 1}`} onClick={() => set({ target_categories: form.target_categories.filter((x) => x.key !== c.key) })}>
                      <Trash2Icon aria-hidden="true" />
                    </Button>
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <p className="px-5 text-sm text-text-muted">No category targets.</p>
      )}
      <div className="space-y-2 px-5 pt-3 pb-5">
        <Button variant="outline" size="sm" onClick={() => set({ target_categories: [...form.target_categories, newCategoryTarget(dimensionNames[0] ?? "")] })}>
          <PlusIcon aria-hidden="true" />
          Add category target
        </Button>
        {Object.entries(sums.byDimension).map(([dimension, total]) => (
          <SumLine key={dimension} label={dimension || "No dimension"} total={total} />
        ))}
      </div>
    </Blueprint>
  );
}

// ---------------------------------------------------------------------------
// Rules
// ---------------------------------------------------------------------------

function RulesPanel({ form, errors, set }: SectionProps) {
  const dimensionNames = [...new Set(form.categories.map((c) => c.name.trim()).filter(Boolean))];
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const setRule = (key: string, change: Partial<RuleForm>) =>
    set({ rules: form.rules.map((r) => (r.key === key ? { ...r, ...change } : r)) });
  const toggleOpen = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <Blueprint aria-labelledby="rules-heading" className={PANEL}>
      <div className="space-y-1">
        <h2 id="rules-heading" className="text-[22px] leading-tight font-semibold">
          Rules
        </h2>
        <p className="text-sm text-text-muted">
          Limits the code checks after every sync. A breached rule is raised at its severity; a rule switched off stays
          in the config but is not checked.
        </p>
      </div>
      {form.rules.length === 0 ? <EmptyState title="No rules">Add one, e.g. no single position above 10 %.</EmptyState> : null}
      <ul className="divide-y divide-border-soft border-t border-border-soft">
        {form.rules.map((r, i) => {
          const typeInfo = RULE_TYPES.find((t) => t.value === r.type);
          const isCount = r.type === "position_count";
          const dimensionOptions = [
            { value: "", label: "Choose…" },
            ...[...new Set([...dimensionNames, r.dimension].filter(Boolean))].map((d) => ({ value: d, label: d })),
          ];
          const ruleErrors = errorsAt(errors, `rules.${i}`);
          const fieldErrors = Object.keys(errors).some((p) => p.startsWith(`rules.${i}.`));
          const expanded = open.has(r.key) || ruleErrors.length > 0 || fieldErrors;
          const name = r.id || `rule ${i + 1}`;
          return (
            <li key={r.key} className="py-2.5">
              <div className="flex items-start gap-3">
                <span className={cn("w-[5.5rem] shrink-0 truncate pt-0.5 font-mono text-[11px]", r.enabled ? "text-text-muted" : "text-text-faint")} title={r.id}>
                  {r.id || "new rule"}
                </span>
                <div className="min-w-0 flex-1">
                  <p className={cn("text-sm font-medium", !r.enabled && "text-text-muted")}>
                    {ruleSummary(r)}
                    {!r.enabled ? <span className="ml-1.5 border border-border px-1 text-[11px] font-normal text-text-muted">off</span> : null}
                  </p>
                  <p className="text-xs text-text-muted">
                    {r.description ? `${r.description} · ` : ""}
                    {severityLabel(r.on_breach)}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7 text-text-muted"
                  aria-expanded={expanded}
                  aria-label={`${expanded ? "Close" : "Edit"} rule ${name}`}
                  onClick={() => toggleOpen(r.key)}
                >
                  <PencilIcon aria-hidden="true" className="size-3.5" />
                </Button>
                <ToggleSwitch className="mt-0.5" checked={r.enabled} label={`Rule ${name} enabled`} onChange={(v) => setRule(r.key, { enabled: v })} />
              </div>
              {expanded ? (
                <div className="mt-3 space-y-3 border-l-2 border-accent-200 pl-3">
                  <div className="flex flex-wrap items-start gap-3">
                    <TextField className="w-44" label="Rule id" path={`rules.${i}.id`} errors={errors} value={r.id} onChange={(v) => setRule(r.key, { id: v })} hint="Lowercase words joined by hyphens." />
                    <SelectField className="w-44" label="Checks" path={`rules.${i}.type`} errors={errors} value={r.type} options={RULE_TYPES} onChange={(v) => setRule(r.key, { type: v })} />
                    {r.type === "position_weight" ? (
                      <>
                        <TextField className="w-40" label="Ticker (optional)" path={`rules.${i}.ticker`} errors={errors} value={r.ticker} onChange={(v) => setRule(r.key, { ticker: v.trim() })} />
                        {!r.ticker.trim() ? (
                          <TextField className="w-56" label="Except (optional)" path={`rules.${i}.exclude`} errors={errors} value={r.exclude} onChange={(v) => setRule(r.key, { exclude: v })} hint="Tickers the rule skips, separated by commas." />
                        ) : null}
                      </>
                    ) : null}
                    {r.type === "category_weight" ? (
                      <>
                        <SelectField className="w-36" label="Dimension" path={`rules.${i}.dimension`} errors={errors} value={r.dimension} options={dimensionOptions} onChange={(v) => setRule(r.key, { dimension: v })} />
                        <TextField className="w-40" label="Bucket (optional)" path={`rules.${i}.bucket`} errors={errors} value={r.bucket} onChange={(v) => setRule(r.key, { bucket: v })} />
                      </>
                    ) : null}
                    <SelectField className="w-20" label="Operator" path={`rules.${i}.operator`} errors={errors} value={r.operator} options={OPERATOR_OPTIONS} onChange={(v) => setRule(r.key, { operator: v })} />
                    <TextField className="w-28" label={isCount ? "Positions" : "Value (%)"} inputMode={isCount ? "numeric" : "decimal"} path={`rules.${i}.value`} errors={errors} value={r.value} onChange={(v) => setRule(r.key, { value: v })} />
                    <SelectField className="w-56" label="On breach" path={`rules.${i}.on_breach`} errors={errors} value={r.on_breach} options={ON_BREACH} onChange={(v) => setRule(r.key, { on_breach: v })} />
                  </div>
                  {typeInfo ? <p className="text-xs text-text-muted">{typeInfo.hint}</p> : null}
                  <TextField label="Description (optional)" path={`rules.${i}.description`} errors={errors} value={r.description} onChange={(v) => setRule(r.key, { description: v })} />
                  {ruleErrors.length > 0 ? <p className="text-xs text-text">{ruleErrors.join(" ")}</p> : null}
                  <Button variant="ghost" size="sm" aria-label={`Remove rule ${r.id || i + 1}`} onClick={() => set({ rules: form.rules.filter((x) => x.key !== r.key) })}>
                    <Trash2Icon aria-hidden="true" />
                    Remove rule
                  </Button>
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
      <Button
        variant="outline"
        size="sm"
        onClick={() => {
          const rule = newRule();
          set({ rules: [...form.rules, rule] });
          setOpen((prev) => new Set(prev).add(rule.key));
        }}
      >
        <PlusIcon aria-hidden="true" />
        Add rule
      </Button>
    </Blueprint>
  );
}

// ---------------------------------------------------------------------------
// Version history (inline)
// ---------------------------------------------------------------------------

function RiskPanel({ form, errors, set }: SectionProps) {
  return (
    <Blueprint aria-labelledby="risk-heading" className={PANEL}>
      <div className="space-y-1">
        <h2 id="risk-heading" className="text-[22px] leading-tight font-semibold">
          Risk limits
        </h2>
        <p className="text-sm text-text-muted">
          Checked after every sync; leave a field blank to switch that limit off. A breach asks for a thesis review, it
          never means sell: a price fall alone is not a reason to sell.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField label="Max volatility (% a year)" path="risk.max_volatility" errors={errors} inputMode="decimal" value={form.risk_max_volatility} onChange={(v) => set({ risk_max_volatility: v })} hint="Portfolio volatility over the last year." />
        <TextField label="Max beta" path="risk.max_beta" errors={errors} inputMode="decimal" value={form.risk_max_beta} onChange={(v) => set({ risk_max_beta: v })} hint="Against the benchmark, last year." />
        <TextField label="Review a position down (%)" path="risk.position_loss_review" errors={errors} inputMode="decimal" value={form.risk_position_loss_review} onChange={(v) => set({ risk_position_loss_review: v })} hint="Loss from cost that forces a thesis review." />
        <TextField label="Max overlap via funds (%)" path="risk.max_look_through_weight" errors={errors} inputMode="decimal" value={form.risk_max_look_through_weight} onChange={(v) => set({ risk_max_look_through_weight: v })} hint="One company, direct plus through ETFs." />
        <TextField label="Max one sector (%)" path="risk.max_sector_weight" errors={errors} inputMode="decimal" value={form.risk_max_sector_weight} onChange={(v) => set({ risk_max_sector_weight: v })} hint="Share of the whole portfolio, counting what funds hold." />
        <TextField label="Max one country (%)" path="risk.max_country_weight" errors={errors} inputMode="decimal" value={form.risk_max_country_weight} onChange={(v) => set({ risk_max_country_weight: v })} hint="Share of the whole portfolio, counting what funds hold." />
        <TextField label="Max drawdown (%)" path="risk.max_drawdown" errors={errors} inputMode="decimal" value={form.risk_max_drawdown} onChange={(v) => set({ risk_max_drawdown: v })} hint="Fall from the highest level of the last year." />
        <TextField label="Max pair correlation (0 to 1)" path="risk.max_pair_correlation" errors={errors} inputMode="decimal" value={form.risk_max_pair_correlation} onChange={(v) => set({ risk_max_pair_correlation: v })} hint="Two holdings that move together, last year." />
        <TextField label="Correlation applies from combined weight (%)" path="risk.correlation_min_combined_weight" errors={errors} inputMode="decimal" value={form.risk_correlation_min_combined_weight} onChange={(v) => set({ risk_correlation_min_combined_weight: v })} hint="Of the invested value; smaller pairs are ignored." />
        <TextField label="Max one-day loss, 95% (%)" path="risk.max_var_95" errors={errors} inputMode="decimal" value={form.risk_max_var_95} onChange={(v) => set({ risk_max_var_95: v })} hint="Historical value at risk over the last year; describes the sample, not a forecast." />
        <TextField label="Max tracking error (% a year)" path="risk.max_tracking_error" errors={errors} inputMode="decimal" value={form.risk_max_tracking_error} onChange={(v) => set({ risk_max_tracking_error: v })} hint="How far returns stray from the benchmark." />
        <TextField label="Max stress loss (%)" path="risk.max_stress_loss" errors={errors} inputMode="decimal" value={form.risk_max_stress_loss} onChange={(v) => set({ risk_max_stress_loss: v })} hint="Loss if equities fall by the size below." />
        <TextField label="Equity fall used for the stress loss (%)" path="risk.stress_equity_fall" errors={errors} inputMode="decimal" value={form.risk_stress_equity_fall} onChange={(v) => set({ risk_stress_equity_fall: v })} />
        <TextField label="Credit spread alert (bp)" path="risk.credit_spread_alert_bp" errors={errors} inputMode="decimal" value={form.risk_credit_spread_alert_bp} onChange={(v) => set({ risk_credit_spread_alert_bp: v })} hint="US high-yield spread at or above this level. A market signal." />
        <TextField label="Max terminal growth in a valuation (%)" path="risk.max_valuation_terminal_growth" errors={errors} inputMode="decimal" value={form.risk_max_valuation_terminal_growth} onChange={(v) => set({ risk_max_valuation_terminal_growth: v })} hint="Flags saved valuations of held stocks that assume more." />
        <TextField label="Max yearly cash flow growth in a valuation (%)" path="risk.max_valuation_growth" errors={errors} inputMode="decimal" value={form.risk_max_valuation_growth} onChange={(v) => set({ risk_max_valuation_growth: v })} hint="Flags saved valuations with faster growth in any year." />
        <SelectField label="On breach" path="risk.on_breach" errors={errors} value={form.risk_on_breach} options={ON_BREACH} onChange={(v) => set({ risk_on_breach: v })} />
      </div>
    </Blueprint>
  );
}

function HistoryPanel({
  versions,
  numbers,
  dirty,
  changeCount,
}: {
  versions: ConfigVersionMeta[] | null;
  numbers: Map<number, number>;
  dirty: boolean;
  changeCount: number;
}) {
  const shown = (versions ?? []).slice(0, 6);
  return (
    <Blueprint aria-labelledby="history-heading" className={PANEL}>
      <PanelHeading
        title={<span id="history-heading">History</span>}
        action={
          <Link href={historyHref("strategy")} className="text-sm text-accent-ink hover:underline">
            All versions
          </Link>
        }
      />
      {versions === null ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading versions…
        </p>
      ) : (
        <ol className="space-y-1">
          {dirty ? (
            <li className="grid grid-cols-[14px_2.75rem_minmax(0,1fr)_auto] items-center gap-2.5 py-1.5 text-[13px]">
              <span aria-hidden="true" className="size-[9px] border border-accent" />
              <span className="font-mono text-xs">Draft</span>
              <span>
                {changeCount} change{changeCount === 1 ? "" : "s"}, not saved
              </span>
              <span className="text-xs text-text-muted">now</span>
            </li>
          ) : null}
          {versions.length === 0 ? <li className="text-sm text-text-muted">Nothing saved yet.</li> : null}
          {shown.map((v) => (
            <li key={v.id} className="grid grid-cols-[14px_2.75rem_minmax(0,1fr)_auto] items-start gap-2.5 py-1.5 text-[13px]">
              <span aria-hidden="true" className={cn("mt-1 size-[9px] border border-accent", v.active && "bg-accent")} />
              <span className="font-mono text-xs leading-5" title={`Version #${v.id}`}>
                v{numbers.get(v.id) ?? "?"}
              </span>
              <span className="min-w-0">
                <span className="line-clamp-2">{v.note || <span className="text-text-muted">No note</span>}</span>
                <span className="block text-xs text-text-muted">
                  {v.active ? "Active · " : ""}
                  {v.changes === null ? "first version" : `${v.changes} change${v.changes === 1 ? "" : "s"}`}
                  {" · "}
                  <Link href={`${historyHref("strategy")}?version=${v.id}`} className="text-accent-ink hover:underline">
                    View diff<span className="sr-only"> of v{numbers.get(v.id)}</span>
                  </Link>
                </span>
              </span>
              <time dateTime={v.created_at} className="num text-xs leading-5 text-text-muted">
                {formatDate(new Date(v.created_at))}
              </time>
            </li>
          ))}
        </ol>
      )}
    </Blueprint>
  );
}

// ---------------------------------------------------------------------------
// Basics, categories
// ---------------------------------------------------------------------------

function BasicsSection({ form, errors, set }: SectionProps) {
  return (
    <Blueprint aria-labelledby="basics-heading" className={PANEL}>
      <PanelHeading kicker="Strategy" title={<span id="basics-heading">Basics</span>} />
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
    </Blueprint>
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
    <Blueprint aria-labelledby="categories-heading" className={PANEL}>
      <PanelHeading kicker="Allocation" title={<span id="categories-heading">Categories and classifications</span>} />
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
        <div className="relative overflow-x-auto border border-border">
          <table className="w-full min-w-[40rem] text-sm">
            <caption className="sr-only">Bucket per dimension for each holding</caption>
            <thead className={TABLE_HEAD}>
              <tr className="border-b border-border text-left">
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
                <tr key={row.key} className="border-b border-border-soft last:border-0">
                  <td className="px-2 py-1.5 align-top">
                    <TextField
                      hideLabel
                      label="Ticker"
                      path={`classifications.${row.ticker}`}
                      errors={errors}
                      value={row.ticker}
                      className="w-40 [&_input]:font-mono [&_input]:text-xs"
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
    </Blueprint>
  );
}
