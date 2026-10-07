"use client";

import { useState, type FormEvent, type ReactNode } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import type { SettingsData } from "@/lib/performance/performance";
import { formToSettings, settingsToForm, type BenchmarkRow, type SettingsForm } from "@/lib/performance/settings-form";

const MAX_BENCHMARK_LINES = 4;
const SELECT = "h-9 w-full border border-border bg-surface-raised px-2 text-sm";

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium">{label}</span>
      {children}
      {hint ? <span className="mt-1 block text-xs text-text-muted">{hint}</span> : null}
    </label>
  );
}

export function SettingsFormPanel({ initial, onSaved }: { initial: SettingsData; onSaved: () => void }) {
  const [form, setForm] = useState<SettingsForm>(() => settingsToForm(initial.settings));
  const [errors, setErrors] = useState<string[]>([]);
  const [saved, setSaved] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const set = <K extends keyof SettingsForm>(key: K, value: SettingsForm[K]) => {
    setSaved(null);
    setForm((f) => ({ ...f, [key]: value }));
  };
  const setRow = (i: number, patch: Partial<BenchmarkRow>) =>
    set("benchmark", form.benchmark.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  async function submit(e: FormEvent) {
    e.preventDefault();
    const checked = formToSettings(form);
    if (!checked.ok) return setErrors(checked.errors);
    setErrors([]);
    setBusy(true);
    const { data, error } = await getBrowserApiClient().PUT("/portfolio/performance/settings", { body: checked.body });
    setBusy(false);
    if (error || !data) return setErrors(["The settings could not be saved. Check the values and try again."]);
    setSaved(data.version ?? null);
    onSaved();
  }

  return (
    <form onSubmit={submit} className="space-y-6" noValidate>
      <fieldset className="space-y-3">
        <legend className="text-sm font-semibold">Core ETFs and stocks</legend>
        <Field label="Core ETF tickers" hint="Trading212 tickers, separated by commas. Every other holding counts as a stock.">
          <Input value={form.core_tickers} onChange={(e) => set("core_tickers", e.target.value)} placeholder="VWCE_EQ, CSPX_EQ" />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Gap that raises a review item" hint="Percentage points a year the core must lead by.">
            <Input inputMode="decimal" value={form.sleeve_gap_points} onChange={(e) => set("sleeve_gap_points", e.target.value)} />
          </Field>
          <Field label="Comparison window, years">
            <Input inputMode="numeric" value={form.sleeve_lookback_years} onChange={(e) => set("sleeve_lookback_years", e.target.value)} />
          </Field>
        </div>
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="text-sm font-semibold">Cost basis and holding period</legend>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Cost basis">
            <select className={SELECT} value={form.cost_method} onChange={(e) => set("cost_method", e.target.value as SettingsForm["cost_method"])}>
              <option value="fifo">Oldest lot sold first</option>
              <option value="average">Average cost</option>
            </select>
          </Field>
          <Field label="Holding period, years">
            <Input inputMode="numeric" value={form.time_test_years} onChange={(e) => set("time_test_years", e.target.value)} />
          </Field>
          <Field label="Warn within, months">
            <Input inputMode="numeric" value={form.time_test_warning_months} onChange={(e) => set("time_test_warning_months", e.target.value)} />
          </Field>
          <Field label="Annual exemption limit">
            <Input inputMode="decimal" value={form.exemption_limit} onChange={(e) => set("exemption_limit", e.target.value)} />
          </Field>
          <Field label="Limit currency">
            <Input value={form.exemption_currency} onChange={(e) => set("exemption_currency", e.target.value)} maxLength={3} />
          </Field>
          <Field label="Cash tolerance" hint="Largest unexplained daily cash change to accept, in the account currency.">
            <Input inputMode="decimal" value={form.cash_tolerance} onChange={(e) => set("cash_tolerance", e.target.value)} />
          </Field>
        </div>
        <p className="text-xs text-text-muted">These are your own thresholds and holding-period facts. They are not tax advice.</p>
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="text-sm font-semibold">Benchmark</legend>
        <p className="text-xs text-text-muted">
          One line is a single index; several lines are a blend. Say what each series is quoted in and whether it includes dividends, so the comparison is like for like.
          With no lines, the first benchmark symbol from the price settings is used and no active return is drawn.
        </p>
        {form.benchmark.map((row, i) => (
          <div key={i} className="grid grid-cols-2 items-end gap-2 sm:grid-cols-[1fr_6rem_6rem_9rem_auto]">
            <Field label={i === 0 ? "Series symbol" : "Symbol"}>
              <Input value={row.symbol} onChange={(e) => setRow(i, { symbol: e.target.value })} />
            </Field>
            <Field label="Weight, %">
              <Input inputMode="decimal" value={row.weight} onChange={(e) => setRow(i, { weight: e.target.value })} />
            </Field>
            <Field label="Currency">
              <Input value={row.currency} maxLength={3} onChange={(e) => setRow(i, { currency: e.target.value })} />
            </Field>
            <Field label="Dividends">
              <select className={SELECT} value={row.basis} onChange={(e) => setRow(i, { basis: e.target.value as BenchmarkRow["basis"] })}>
                <option value="unknown">Not known</option>
                <option value="total">Included (total return)</option>
                <option value="price">Not included (price only)</option>
              </select>
            </Field>
            <Button type="button" variant="outline" size="sm" onClick={() => set("benchmark", form.benchmark.filter((_, j) => j !== i))}>
              Remove
            </Button>
          </div>
        ))}
        <div className="flex flex-wrap items-end gap-3">
          {form.benchmark.length < MAX_BENCHMARK_LINES ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => set("benchmark", [...form.benchmark, { symbol: "", weight: form.benchmark.length === 0 ? "100" : "", currency: "", basis: "unknown" }])}
            >
              Add a benchmark line
            </Button>
          ) : null}
          {form.benchmark.length > 1 ? (
            <Field label="Rebalance the blend">
              <select className={SELECT} value={form.rebalance} onChange={(e) => set("rebalance", e.target.value as SettingsForm["rebalance"])}>
                <option value="none">Never (weights drift)</option>
                <option value="monthly">Monthly</option>
                <option value="quarterly">Quarterly</option>
                <option value="annual">Yearly</option>
              </select>
            </Field>
          ) : null}
        </div>
      </fieldset>

      <Field label="Note to yourself" hint="Kept with this version of the settings.">
        <Input value={form.note} onChange={(e) => set("note", e.target.value)} maxLength={500} />
      </Field>

      {errors.length > 0 ? (
        <AlertMessage title="Fix these before saving">
          <ul className="list-disc pl-5">
            {errors.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        </AlertMessage>
      ) : null}
      {saved !== null ? (
        <AlertMessage tone="info" title="Settings saved">
          Saved as version {saved}. The figures on the other tabs now use them.
        </AlertMessage>
      ) : null}
      <Button type="submit" disabled={busy}>
        {busy ? "Saving…" : "Save settings"}
      </Button>
    </form>
  );
}
