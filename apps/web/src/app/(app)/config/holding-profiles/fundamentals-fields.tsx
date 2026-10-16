"use client";

import { PlusIcon, Trash2Icon } from "lucide-react";

import { SelectField, TextAreaField, TextField } from "@/components/config/fields";
import { Button } from "@/components/ui/button";
import type { FieldErrors } from "@/lib/api/validation-errors";
import {
  newManualSegment,
  SEGMENT_AXES,
  SEGMENT_AXIS_LABELS,
  type FundamentalsForm,
  type ManualSegmentForm,
} from "@/lib/config/fundamentals-form";

/**
 * A holding profile's SEC XBRL settings: how its 13F figures compare with shares outstanding
 * (ADS ratio, counted share classes, a manual figure for non-SEC issuers) and revenue by
 * region / segment / product entered by hand from an annual report, for issuers whose
 * filings carry no machine-readable breakdown (Prague listings).
 */
export function FundamentalsFields({
  form,
  path,
  errors,
  onChange,
}: {
  form: FundamentalsForm;
  /** Dotted path of the profile in the API body, e.g. "profiles.3". */
  path: string;
  errors: FieldErrors;
  onChange: (change: (form: FundamentalsForm) => FundamentalsForm) => void;
}) {
  const basis = `${path}.share_basis`;
  const text = (key: Exclude<keyof FundamentalsForm, "segments">) => ({
    errors,
    value: form[key],
    onChange: (v: string) => onChange((f) => ({ ...f, [key]: v })),
  });
  const setRow = (key: string, change: Partial<ManualSegmentForm>) =>
    onChange((f) => ({ ...f, segments: f.segments.map((s) => (s.key === key ? { ...s, ...change } : s)) }));

  return (
    <details className="rounded-card bg-surface p-3" open={form.segments.length > 0 || undefined}>
      <summary className="cursor-pointer text-sm font-medium">Shares outstanding and revenue breakdown</summary>
      <fieldset className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <legend className="mb-2 text-sm font-medium">13F share basis</legend>
        <TextField
          label="Shares per US-listed share (ADS ratio)"
          path={`${basis}.ads_ratio`}
          placeholder="default: TSM 5, others 1"
          inputMode="decimal"
          hint="Home-market shares one ADS / New York share represents."
          {...text("ads_ratio")}
        />
        <TextAreaField
          label="Share classes counted"
          path={`${basis}.share_classes`}
          rows={2}
          hint="One XBRL member per line, e.g. us-gaap:CommonClassAMember. Empty: all classes."
          {...text("share_classes")}
        />
        <TextField
          label="Manual shares outstanding"
          path={`${basis}.manual_shares_outstanding`}
          inputMode="numeric"
          hint="Only for issuers outside SEC EDGAR."
          {...text("manual_shares_outstanding")}
        />
        <TextField label="Manual figure as of" path={`${basis}.manual_as_of`} type="date" {...text("manual_as_of")} />
        <TextField
          className="sm:col-span-2 lg:col-span-4"
          label="Manual figure source"
          path={`${basis}.manual_source`}
          placeholder="e.g. CEZ shareholder structure page, 31 Dec 2025"
          {...text("manual_source")}
        />
      </fieldset>

      <fieldset className="mt-4 space-y-2">
        <legend className="text-sm font-medium">Revenue breakdown entered manually</legend>
        <p className="text-xs text-text-muted">
          Shown on the holding page, labelled &ldquo;entered manually&rdquo;, only when no SEC XBRL filing provides a
          breakdown. Copy the figures as the annual report states them.
        </p>
        {form.segments.map((s, i) => {
          const at = `${path}.manual_segments.${i}`;
          return (
            <div
              key={s.key}
              className="grid items-end gap-2 border-b border-border-soft pb-2 sm:grid-cols-2 lg:grid-cols-[1.4fr_1fr_1fr_0.6fr_1fr_1fr_auto]"
            >
              <TextField
                label="Label"
                path={`${at}.label`}
                errors={errors}
                value={s.label}
                onChange={(v) => setRow(s.key, { label: v })}
              />
              <SelectField
                label="Breakdown"
                path={`${at}.axis`}
                errors={errors}
                value={s.axis}
                options={SEGMENT_AXES.map((a) => ({ value: a, label: SEGMENT_AXIS_LABELS[a] }))}
                onChange={(v) => setRow(s.key, { axis: v })}
              />
              <TextField
                label="Revenue"
                path={`${at}.value`}
                errors={errors}
                value={s.value}
                inputMode="decimal"
                onChange={(v) => setRow(s.key, { value: v })}
              />
              <TextField
                label="Currency"
                path={`${at}.currency`}
                errors={errors}
                value={s.currency}
                maxLength={3}
                placeholder="CZK"
                onChange={(v) => setRow(s.key, { currency: v })}
              />
              <TextField
                label="Period start"
                path={`${at}.period_start`}
                errors={errors}
                type="date"
                value={s.period_start}
                onChange={(v) => setRow(s.key, { period_start: v })}
              />
              <TextField
                label="Period end"
                path={`${at}.period_end`}
                errors={errors}
                type="date"
                value={s.period_end}
                onChange={(v) => setRow(s.key, { period_end: v })}
              />
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Remove revenue row ${s.label || i + 1}`}
                onClick={() => onChange((f) => ({ ...f, segments: f.segments.filter((x) => x.key !== s.key) }))}
              >
                <Trash2Icon aria-hidden="true" />
              </Button>
              <TextField
                className="sm:col-span-2 lg:col-span-7"
                label="Source"
                path={`${at}.source`}
                errors={errors}
                value={s.source}
                placeholder="e.g. Annual Report 2025, segment note, p. 212"
                onChange={(v) => setRow(s.key, { source: v })}
              />
            </div>
          );
        })}
        <Button
          variant="outline"
          size="sm"
          onClick={() => onChange((f) => ({ ...f, segments: [...f.segments, newManualSegment(f.segments.at(-1))] }))}
        >
          <PlusIcon aria-hidden="true" />
          Add revenue row
        </Button>
      </fieldset>
    </details>
  );
}
