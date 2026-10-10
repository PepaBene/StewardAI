"use client";

import { useState, type FormEvent } from "react";

import { getBrowserApiClient } from "@/lib/api/client";
import { FACT_LABEL, checkFactValue, isNumericFact } from "@/lib/fundresearch/fund-research";

export type RecordField = {
  key: string;
  /** When given, the value is chosen from these (value, label) pairs. */
  options?: readonly (readonly [string, string])[];
  hint?: string;
};

/**
 * Records one or several sourced facts about a fund with one shared source address.
 * Blank fields are left alone; each filled field becomes its own append-only entry.
 */
export function RecordFactsForm({
  isin,
  fields,
  submitLabel,
  onSaved,
  initial,
}: {
  isin: string;
  fields: readonly RecordField[];
  submitLabel: string;
  onSaved: () => void;
  initial?: Record<string, string>;
}) {
  const [values, setValues] = useState<Record<string, string>>(initial ?? {});
  const [sourceUrl, setSourceUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSaved(null);
    const entries: [string, string][] = [];
    for (const f of fields) {
      const raw = values[f.key] ?? "";
      if (!raw.trim()) continue;
      const checked = checkFactValue(f.key, raw);
      if ("error" in checked) return setError(checked.error);
      entries.push([f.key, checked.value]);
    }
    if (entries.length === 0) return setError("Fill in at least one value.");
    if (!/^https?:\/\//.test(sourceUrl.trim())) return setError("A source is required: paste the web address of the document that states it.");
    setError(null);
    setBusy(true);
    const done: string[] = [];
    for (const [key, value] of entries) {
      let failed = false;
      try {
        const { error: err } = await getBrowserApiClient().PUT("/funds/{isin}/facts", {
          params: { path: { isin } },
          body: { key, value, source_url: sourceUrl.trim() },
        });
        failed = Boolean(err);
      } catch {
        failed = true;
      }
      if (failed) {
        setBusy(false);
        // The history is append-only: what is already stored must not be sent again on a retry.
        if (done.length > 0) {
          setValues((v) => {
            const rest = { ...v };
            for (const k of done) delete rest[k];
            return rest;
          });
          onSaved();
        }
        const stored = done.length > 0 ? ` ${done.map((k) => FACT_LABEL[k] ?? k).join(", ")} ${done.length === 1 ? "was" : "were"} already recorded and removed from the form.` : "";
        return setError(`${FACT_LABEL[key] ?? key} could not be saved. Check the value and the address.${stored}`);
      }
      done.push(key);
    }
    setBusy(false);
    setSaved(`Recorded ${entries.length} ${entries.length === 1 ? "entry" : "entries"}.`);
    setValues({});
    onSaved();
  }

  return (
    <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
      {fields.map((f) => (
        <label key={f.key} className="text-sm">
          <span className="mb-1 block text-xs text-text-muted">{FACT_LABEL[f.key] ?? f.key}</span>
          {f.options ? (
            <select value={values[f.key] ?? ""} onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))} className="w-full border border-border bg-surface px-2 py-1.5">
              <option value="">Leave as it is</option>
              {f.options.map(([v, t]) => (
                <option key={v} value={v}>{t}</option>
              ))}
            </select>
          ) : (
            <input
              value={values[f.key] ?? ""}
              onChange={(e) => setValues((v) => ({ ...v, [f.key]: f.key === "benchmark_currency" ? e.target.value.toUpperCase() : e.target.value }))}
              inputMode={isNumericFact(f.key) ? "decimal" : undefined}
              maxLength={f.key === "benchmark_currency" ? 3 : undefined}
              className="w-full border border-border bg-surface px-2 py-1.5"
            />
          )}
          {f.hint ? <span className="mt-0.5 block text-[11px] text-text-muted">{f.hint}</span> : null}
        </label>
      ))}
      <label className="text-sm sm:col-span-2">
        <span className="mb-1 block text-xs text-text-muted">Source (web address of the factsheet, KID or prospectus)</span>
        <input value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} className="w-full border border-border bg-surface px-2 py-1.5 font-mono text-xs" />
      </label>
      <div className="flex items-center gap-3 sm:col-span-2">
        <button type="submit" disabled={busy} className="border border-accent px-4 py-1.5 text-sm font-semibold hover:bg-surface-muted disabled:opacity-50">{submitLabel}</button>
        {error ? <p role="alert" className="text-sm text-loss">{error}</p> : null}
        {saved ? <p role="status" className="text-sm text-text-muted">{saved}</p> : null}
      </div>
    </form>
  );
}
