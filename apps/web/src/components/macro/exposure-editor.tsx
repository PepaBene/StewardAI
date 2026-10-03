"use client";

import { useMemo, useState, type FormEvent, type ReactNode } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatPct } from "@/lib/charts/format";
import { createExposure, deleteExposure } from "@/lib/macro/api";
import {
  apiErrorMessages,
  BASIS_LABELS,
  geographyName,
  isActiveMapping,
  summariseMappings,
  validateExposureForm,
  type DimensionSummary,
  type ExposureForm,
  type FormErrors,
} from "@/lib/macro/logic";
import type { ExposureMapping } from "@/lib/macro/types";
import { cn } from "@/lib/utils";

import { ExposureTag } from "./exposure-tag";
import { day } from "./format";

const today = () => new Date().toISOString().slice(0, 10);

function blankForm(issuerId: number | null): ExposureForm {
  return { issuerId: issuerId === null ? "" : String(issuerId), segment: "", geography: "", hsCode: "", hsRevision: "", basis: "", weightPercent: "", validFrom: today(), validTo: "" };
}

function Field({ id, label, error, hint, children }: { id: string; label: string; error?: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && !error ? <p className="text-[11px] text-text-muted">{hint}</p> : null}
      {error ? (
        <p id={`${id}-error`} role="alert" className="text-xs text-text">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function DimensionLine({ label, d }: { label: string; d: DimensionSummary }) {
  return (
    <p className="num text-xs text-text-muted">
      {label}: {d.count === 0 ? "nothing mapped" : `${formatPct(d.mapped, 1)} mapped`}
      {d.count > 0 ? ` · ${formatPct(d.unknown, 1)} unknown` : " · 100% unknown"}
      {d.overAllocated ? " · over 100%: fix the mapping" : ""}
    </p>
  );
}

/**
 * Viewer and editor of the exposure mapping (POST/DELETE /macro/exposures). Every row is one
 * weight of one issuer segment to a country or an HS product, labelled reported, estimated or
 * owner-assigned, with its validity dates. The remainder of each dimension stays visibly
 * unknown; the backend enforces the 100% cap per dimension and its message is shown as is.
 */
export function ExposureEditor({
  mappings,
  issuers,
  defaultIssuerId,
  onChanged,
}: {
  mappings: readonly ExposureMapping[];
  /** issuer id -> ticker, for the issuers the page knows about. */
  issuers: ReadonlyMap<number, string>;
  defaultIssuerId: number | null;
  onChanged: () => void;
}) {
  const [form, setForm] = useState<ExposureForm>(() => blankForm(defaultIssuerId));
  const [errors, setErrors] = useState<FormErrors>({});
  const [serverErrors, setServerErrors] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [deleteError, setDeleteError] = useState<{ id: number; messages: string[] } | null>(null);

  const t = today();
  const summaries = useMemo(() => summariseMappings(mappings, t), [mappings, t]);
  const issuerLabel = (id: number) => (issuers.has(id) ? `${issuers.get(id)} (issuer ${id})` : `Issuer ${id}`);
  const set = <K extends keyof ExposureForm>(k: K, v: ExposureForm[K]) => setForm((f) => ({ ...f, [k]: v }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setNotice(null);
    setServerErrors([]);
    const { errors: found, body } = validateExposureForm(form);
    setErrors(found);
    if (!body) return;
    setSaving(true);
    try {
      const { error, response } = await createExposure(body);
      if (!response.ok) {
        const messages = apiErrorMessages(error);
        setServerErrors(messages.length ? messages : [`The API rejected the mapping (HTTP ${response.status}).`]);
        return;
      }
      setNotice(`Mapping saved: ${body.segment} → ${body.geography ?? `HS ${body.hs_code}`} at ${formatPct(body.weight, 1)}.`);
      setForm((f) => ({ ...blankForm(Number(f.issuerId) || null), basis: f.basis }));
      onChanged();
    } catch (err) {
      setServerErrors([`Could not reach the API: ${err instanceof Error ? err.message : String(err)}`]);
    } finally {
      setSaving(false);
    }
  }

  async function remove(id: number) {
    setDeleting(id);
    setDeleteError(null);
    try {
      const { error, response } = await deleteExposure(id);
      if (!response.ok) {
        const messages = apiErrorMessages(error);
        setDeleteError({ id, messages: messages.length ? messages : [`The API did not delete the mapping (HTTP ${response.status}).`] });
        return;
      }
      setConfirming(null);
      onChanged();
    } catch (err) {
      setDeleteError({ id, messages: [`Could not reach the API: ${err instanceof Error ? err.message : String(err)}`] });
    } finally {
      setDeleting(null);
    }
  }

  const fieldErr = (k: keyof ExposureForm) => errors[k];
  const inv = (k: keyof ExposureForm) => (errors[k] ? true : undefined);

  return (
    <Blueprint as="section" aria-labelledby="exposure-editor-heading" className="flex flex-col gap-4 px-5 py-5">
      <PanelHeading kicker="Exposure mapping" title={<span id="exposure-editor-heading">Which countries and products each issuer is tied to</span>} />
      <p className="max-w-prose text-sm text-text-muted">
        Everything on this page links a holding to a country only through these rows. Reported rows come from the company&apos;s own disclosure, estimated rows from a stated method, owner-assigned rows are your assumption. Weights in one dimension (country or product) cannot exceed 100% for an issuer; what is left is unknown.
      </p>

      {summaries.length === 0 ? (
        <p className="border border-dashed border-border px-3 py-4 text-sm text-text-muted">No exposure mapping exists yet. Every holding&apos;s exposure is unknown until you add one below.</p>
      ) : (
        <ul className="m-0 grid list-none grid-cols-1 gap-3 p-0 md:grid-cols-2 xl:grid-cols-3">
          {summaries.map((s) => (
            <li key={s.issuerId} className="flex flex-col gap-0.5 border border-border-soft p-3">
              <span className="font-heading text-lg font-semibold">{issuerLabel(s.issuerId)}</span>
              <DimensionLine label="Countries" d={s.geography} />
              <DimensionLine label="Products" d={s.product} />
              {s.inactive > 0 ? <p className="text-xs text-text-muted">{s.inactive} mapping{s.inactive === 1 ? "" : "s"} not valid today (expired or future) and not counted.</p> : null}
            </li>
          ))}
        </ul>
      )}

      {mappings.length > 0 ? (
        <div className="relative overflow-x-auto">
          <table className="w-full min-w-[44rem] text-xs">
            <caption className="sr-only">All exposure mappings</caption>
            <thead className="bg-surface-muted">
              <tr className="border-b border-border text-left text-[11px] tracking-[0.08em] text-text-muted uppercase">
                <th scope="col" className="px-3 py-1.5 font-medium">Issuer</th>
                <th scope="col" className="px-3 py-1.5 font-medium">Segment</th>
                <th scope="col" className="px-3 py-1.5 font-medium">Target</th>
                <th scope="col" className="px-3 py-1.5 font-medium">Basis</th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">Weight</th>
                <th scope="col" className="px-3 py-1.5 font-medium">Valid</th>
                <th scope="col" className="px-3 py-1.5 font-medium">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {mappings.map((m) => {
                const active = isActiveMapping(m, t);
                return (
                  <tr key={m.id} className={cn("border-b border-border-soft align-top last:border-0", !active && "text-text-muted")}>
                    <th scope="row" className="px-3 py-1.5 text-left font-normal">{issuerLabel(m.issuerId)}</th>
                    <td className="px-3 py-1.5">{m.segment}</td>
                    <td className="px-3 py-1.5">{m.geography ? `${geographyName(m.geography)} (${m.geography})` : m.hsCode ? `HS ${m.hsCode}${m.hsRevision ? ` · ${m.hsRevision}` : ""}` : "n/a"}</td>
                    <td className="px-3 py-1.5"><ExposureTag basis={m.basis} /></td>
                    <td className="num px-3 py-1.5 text-right">{m.weight === null ? "n/a" : formatPct(m.weight, 1)}</td>
                    <td className="num px-3 py-1.5">
                      {day(m.validFrom)} to {m.validTo ? day(m.validTo) : "open"}
                      {!active ? <span className="block text-[11px]">not valid today</span> : null}
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      {confirming === m.id ? (
                        <span className="inline-flex gap-1.5">
                          <Button type="button" size="sm" variant="outline" disabled={deleting === m.id} onClick={() => void remove(m.id)}>
                            {deleting === m.id ? "Deleting…" : "Confirm delete"}
                          </Button>
                          <Button type="button" size="sm" variant="ghost" onClick={() => setConfirming(null)}>Cancel</Button>
                        </span>
                      ) : (
                        <Button type="button" size="sm" variant="ghost" onClick={() => { setConfirming(m.id); setDeleteError(null); }} aria-label={`Delete mapping ${m.segment} for ${issuerLabel(m.issuerId)}`}>
                          Delete
                        </Button>
                      )}
                      {deleteError?.id === m.id ? (
                        <span role="alert" className="mt-1 block text-left text-xs text-text">{deleteError.messages.join(" ")}</span>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      <form onSubmit={submit} noValidate className="flex flex-col gap-3 border-t border-border-soft pt-4" aria-labelledby="exposure-form-heading">
        <h3 id="exposure-form-heading" className="font-heading text-lg font-semibold">Add a mapping</h3>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field id="ex-issuer" label="Issuer id" error={fieldErr("issuerId")} hint={defaultIssuerId === null ? "The id of the issuer this segment belongs to." : undefined}>
            <Input id="ex-issuer" value={form.issuerId} onChange={(e) => set("issuerId", e.target.value)} inputMode="numeric" aria-invalid={inv("issuerId")} aria-describedby={errors.issuerId ? "ex-issuer-error" : undefined} />
          </Field>
          <Field id="ex-segment" label="Segment" error={fieldErr("segment")}>
            <Input id="ex-segment" value={form.segment} onChange={(e) => set("segment", e.target.value)} maxLength={160} aria-invalid={inv("segment")} aria-describedby={errors.segment ? "ex-segment-error" : undefined} />
          </Field>
          <Field id="ex-geo" label="Country code" error={fieldErr("geography")} hint="ISO alpha-2, e.g. DE (or EU)">
            <Input id="ex-geo" value={form.geography} onChange={(e) => set("geography", e.target.value.toUpperCase())} maxLength={5} aria-invalid={inv("geography")} aria-describedby={errors.geography ? "ex-geo-error" : undefined} />
          </Field>
          <Field id="ex-basis" label="Basis" error={fieldErr("basis")}>
            <select
              id="ex-basis"
              value={form.basis}
              onChange={(e) => set("basis", e.target.value as ExposureForm["basis"])}
              aria-invalid={inv("basis")}
              aria-describedby={errors.basis ? "ex-basis-error" : undefined}
              className="h-9 w-full min-w-0 border border-border bg-surface-raised px-2 text-sm text-text outline-none focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30 aria-invalid:border-warning-signal"
            >
              <option value="">Choose…</option>
              <option value="reported">{BASIS_LABELS.reported}</option>
              <option value="estimated">{BASIS_LABELS.estimated}</option>
              <option value="user_assigned">{BASIS_LABELS.user_assigned}</option>
            </select>
          </Field>
          <Field id="ex-hs" label="or HS product code" error={fieldErr("hsCode")} hint="2 to 6 digits">
            <Input id="ex-hs" value={form.hsCode} onChange={(e) => set("hsCode", e.target.value)} inputMode="numeric" aria-invalid={inv("hsCode")} aria-describedby={errors.hsCode ? "ex-hs-error" : undefined} />
          </Field>
          <Field id="ex-rev" label="HS revision" error={fieldErr("hsRevision")} hint="Required with an HS code, e.g. HS2022">
            <Input id="ex-rev" value={form.hsRevision} onChange={(e) => set("hsRevision", e.target.value)} aria-invalid={inv("hsRevision")} aria-describedby={errors.hsRevision ? "ex-rev-error" : undefined} />
          </Field>
          <Field id="ex-weight" label="Weight (% of issuer)" error={fieldErr("weightPercent")} hint="0 to 100">
            <Input id="ex-weight" value={form.weightPercent} onChange={(e) => set("weightPercent", e.target.value)} inputMode="decimal" aria-invalid={inv("weightPercent")} aria-describedby={errors.weightPercent ? "ex-weight-error" : undefined} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field id="ex-from" label="Valid from" error={fieldErr("validFrom")}>
              <Input id="ex-from" type="date" value={form.validFrom} onChange={(e) => set("validFrom", e.target.value)} aria-invalid={inv("validFrom")} aria-describedby={errors.validFrom ? "ex-from-error" : undefined} />
            </Field>
            <Field id="ex-to" label="Valid to" error={fieldErr("validTo")} hint="Optional">
              <Input id="ex-to" type="date" value={form.validTo} onChange={(e) => set("validTo", e.target.value)} aria-invalid={inv("validTo")} aria-describedby={errors.validTo ? "ex-to-error" : undefined} />
            </Field>
          </div>
        </div>
        {errors.form ? <p role="alert" className="text-sm text-text">{errors.form}</p> : null}
        {serverErrors.length > 0 ? (
          <AlertMessage title="The API rejected this mapping">
            {serverErrors.map((m, i) => (
              <span key={i} className="block">{m}</span>
            ))}
          </AlertMessage>
        ) : null}
        {notice ? <p role="status" className="text-sm text-text">{notice}</p> : null}
        <div>
          <Button type="submit" disabled={saving}>{saving ? "Saving…" : "Add mapping"}</Button>
        </div>
      </form>
    </Blueprint>
  );
}
