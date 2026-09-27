"use client";

import { LoaderCircleIcon, PlusIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ConfigHeader } from "@/components/config/config-header";
import { CheckboxField, CheckboxGroup, SelectField, TextField } from "@/components/config/fields";
import { SaveBar } from "@/components/config/save-bar";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type VerificationConfig } from "@/lib/api/client";
import { errorsAt, type FieldErrors } from "@/lib/api/validation-errors";
import { EFFORT_OPTIONAL_HINT, EFFORTS, MODEL_SUGGESTIONS } from "@/lib/config/jobs-form";
import { mergeErrors } from "@/lib/config/strategy-form";
import {
  CLAIM_MODES,
  CLAIM_TARGET_LABELS,
  CLAIM_TARGETS,
  newOverride,
  overrideErrorPath,
  validateVerificationForm,
  verificationFromBody,
  verificationToBody,
  type OverrideRow,
  type VerificationForm,
} from "@/lib/config/verification-form";
import { configSaveQuery, useConfigEditor } from "@/lib/hooks/use-config-editor";

const api = () => getBrowserApiClient();
const TARGET_OPTIONS = CLAIM_TARGETS.map((t) => ({ value: t, label: CLAIM_TARGET_LABELS[t] }));
const EFFORT_OPTIONS = [{ value: "", label: "Model default" }, ...EFFORTS.map((e) => ({ value: e, label: e }))];
const SECTION = "space-y-3 rounded-lg border border-border bg-surface-raised p-4";

/**
 * The Verification config: every threshold of the defence layers (numeric validation,
 * corroboration, source trust, the claim verifier). Each field is checked here against the
 * backend's bounds before Save is allowed, and the backend validates again.
 */
export function VerificationEditor() {
  const [form, setForm] = useState<VerificationForm | null>(null);
  const [baseline, setBaseline] = useState("");

  const editor = useConfigEditor<VerificationConfig>({
    load: () => api().GET("/config/verification"),
    save: (body, note, base) => api().PUT("/config/verification", { body, params: { query: configSaveQuery(note, base) } }),
    validate: (body) => api().POST("/config/verification/validate", { body }),
    onLoaded: (body) => {
      const loaded = verificationFromBody(body);
      setForm(loaded);
      setBaseline(body ? JSON.stringify(verificationToBody(loaded)) : "");
    },
  });

  const body = useMemo(() => (form ? verificationToBody(form) : null), [form]);
  const clientErrors = useMemo(() => (form ? validateVerificationForm(form) : {}), [form]);
  const errors = useMemo(() => mergeErrors(clientErrors, editor.errors), [clientErrors, editor.errors]);
  const blocked = Object.keys(clientErrors).length > 0;
  const dirty = body !== null && JSON.stringify(body) !== baseline;
  const set = (change: Partial<VerificationForm>) => setForm((f) => (f ? { ...f, ...change } : f));
  const setRow = (key: string, change: Partial<OverrideRow>) =>
    setForm((f) => (f ? { ...f, overrides: f.overrides.map((r) => (r.key === key ? { ...r, ...change } : r)) } : f));

  async function save(note: string | null) {
    if (!body || blocked) return;
    if (await editor.save(body, note)) setBaseline(JSON.stringify(body));
  }

  return (
    <div className="space-y-6">
      <ConfigHeader title="Verification" domain="verification">
        <p>
          The thresholds of the defences against wrong information: when an extracted figure is held for your review,
          when a news story counts as confirmed, how your feedback moves a source&rsquo;s trust, and how strictly the claim
          verifier checks model-written sentences. Used from the next run, no deploy.
        </p>
      </ConfigHeader>

      {editor.status.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading the verification config…
        </p>
      ) : null}
      {editor.status.kind === "error" ? <AlertMessage title="Could not load the verification config">{editor.status.message}</AlertMessage> : null}
      {editor.status.kind === "ready" && editor.status.version === null ? (
        <AlertMessage tone="info" title="Defaults in use">
          Nothing saved yet: the values below are the backend&rsquo;s defaults.
        </AlertMessage>
      ) : null}

      {form ? (
        <>
          <section aria-labelledby="figures-heading" className={SECTION}>
            <h2 id="figures-heading" className="text-[22px] leading-tight font-semibold">
              Extracted figures
            </h2>
            <p className="max-w-prose text-sm text-text-muted">
              Checked in code before a figure is stored as verified. A figure outside a bound is held for your review in the{" "}
              <Link href="/review" className="text-accent hover:underline">
                review queue
              </Link>
              .
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-2">
                <CheckboxField
                  label="Hold figures that move too much quarter on quarter"
                  checked={form.sanityEnabled}
                  onChange={(v) => set({ sanityEnabled: v })}
                  hint="Off: no default sanity band (per-metric bands below still apply)."
                />
                {form.sanityEnabled ? (
                  <TextField
                    label="Default sanity band (± %)"
                    inputMode="decimal"
                    path="sanity_max_change_qoq"
                    errors={errors}
                    value={form.sanityMaxChange}
                    onChange={(v) => set({ sanityMaxChange: v })}
                    hint="50 holds a figure more than 50 % above or below the previous quarter's verified figure."
                  />
                ) : null}
              </div>
              <TextField
                label="Stated ratio tolerance (percentage points)"
                inputMode="decimal"
                path="stated_ratio_tolerance_pp"
                errors={errors}
                value={form.statedRatioTolerancePp}
                onChange={(v) => set({ statedRatioTolerancePp: v })}
                hint="How far a ratio the document states may differ from the one recomputed from its raw figures."
              />
              <TextField
                label="Period end tolerance (days)"
                inputMode="numeric"
                path="period_end_tolerance_days"
                errors={errors}
                value={form.periodEndToleranceDays}
                onChange={(v) => set({ periodEndToleranceDays: v })}
                hint="0–31. Slack for 52/53-week fiscal years that end on a weekday, not a month end."
              />
              <TextField
                label="Latest release after the period end (days)"
                inputMode="numeric"
                path="max_report_lag_days"
                errors={errors}
                value={form.maxReportLagDays}
                onChange={(v) => set({ maxReportLagDays: v })}
                hint="30–400."
              />
            </div>

            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">Sanity band per metric</legend>
              <p className="text-xs text-text-muted">
                Replaces the default band for one metric (e.g. revenue). &ldquo;No bound&rdquo; turns the check off for a
                seasonal or often sign-changing figure.
              </p>
              {errorsAt(errors, "sanity_overrides").length > 0 ? (
                <p className="text-xs text-text">{errorsAt(errors, "sanity_overrides").join(" ")}</p>
              ) : null}
              {form.overrides.length === 0 ? <p className="text-sm text-text-muted">No per-metric bands.</p> : null}
              <ul className="space-y-2">
                {form.overrides.map((row, i) => (
                  <OverrideItem
                    key={row.key}
                    row={row}
                    index={i}
                    errors={errors}
                    onChange={(change) => setRow(row.key, change)}
                    onRemove={() => set({ overrides: form.overrides.filter((r) => r.key !== row.key) })}
                  />
                ))}
              </ul>
              <Button variant="outline" size="sm" onClick={() => set({ overrides: [...form.overrides, newOverride()] })}>
                <PlusIcon aria-hidden="true" />
                Add a metric band
              </Button>
            </fieldset>
          </section>

          <section aria-labelledby="corroboration-heading" className={SECTION}>
            <h2 id="corroboration-heading" className="text-[22px] leading-tight font-semibold">
              Corroboration of news
            </h2>
            <div className="grid gap-3 sm:grid-cols-2">
              <CheckboxField
                label="A tier-1 source confirms a story on its own"
                checked={form.tier1ConfirmsAlone}
                onChange={(v) => set({ tier1ConfirmsAlone: v })}
                hint="The company, a regulator, a statistics office. Off: it counts as one independent source like a tier-2 outlet."
              />
              <TextField
                label="Independent tier-2 outlets needed to confirm"
                inputMode="numeric"
                path="corroboration.independent_sources_to_confirm"
                errors={errors}
                value={form.independentSources}
                onChange={(v) => set({ independentSources: v })}
                hint="2–5 different stories, not copies of one wire story."
              />
            </div>
          </section>

          <section aria-labelledby="trust-heading" className={SECTION}>
            <h2 id="trust-heading" className="scroll-mt-24 text-[22px] leading-tight font-semibold">
              Source trust from your feedback
            </h2>
            <p className="max-w-prose text-sm text-text-muted">
              Every source starts at 1 (scores run from 0 to 1). The current scores are on the{" "}
              <Link href="/activity#source-trust" className="text-accent hover:underline">
                Activity page
              </Link>
              .
            </p>
            <div className="grid gap-3 sm:grid-cols-3">
              <TextField
                label="Lost per “wrong”"
                inputMode="decimal"
                path="trust.wrong_penalty"
                errors={errors}
                value={form.wrongPenalty}
                onChange={(v) => set({ wrongPenalty: v })}
                hint="0–1."
              />
              <TextField
                label="Gained per “useful”"
                inputMode="decimal"
                path="trust.useful_reward"
                errors={errors}
                value={form.usefulReward}
                onChange={(v) => set({ usefulReward: v })}
                hint="0–1."
              />
              <TextField
                label="Demote below"
                inputMode="decimal"
                path="trust.demote_below"
                errors={errors}
                value={form.demoteBelow}
                onChange={(v) => set({ demoteBelow: v })}
                hint="0–1. Below it a source counts one tier lower (tier 1 as 2, tier 2 as 3)."
              />
            </div>
          </section>

          <section aria-labelledby="claims-heading" className={SECTION}>
            <h2 id="claims-heading" className="text-[22px] leading-tight font-semibold">
              Claim verifier
            </h2>
            <p className="max-w-prose text-sm text-text-muted">
              Before a report is stored or sent, every model-written sentence is checked against the snippets it cites.
            </p>
            <CheckboxField label="Check model-written sentences" checked={form.claimEnabled} onChange={(v) => set({ claimEnabled: v })} />
            <div className="grid gap-3 sm:grid-cols-2">
              <TextField
                label="Model"
                path="claim_verifier.model"
                errors={errors}
                value={form.claimModel}
                list="verifier-models"
                spellCheck={false}
                onChange={(v) => set({ claimModel: v })}
                hint="A Claude model id."
              />
              <SelectField
                label="Effort"
                path="claim_verifier.effort"
                errors={errors}
                value={form.claimEffort}
                options={EFFORT_OPTIONS}
                onChange={(v) => set({ claimEffort: v })}
                hint={EFFORT_OPTIONAL_HINT}
              />
              <SelectField
                label="An unsupported sentence is"
                path="claim_verifier.mode"
                errors={errors}
                value={form.claimMode}
                options={CLAIM_MODES}
                onChange={(v) => set({ claimMode: v })}
              />
            </div>
            <datalist id="verifier-models">
              {MODEL_SUGGESTIONS.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
            <CheckboxField
              label="Numeric check first"
              checked={form.numericCheck}
              onChange={(v) => set({ numericCheck: v })}
              hint="Code checks that every number in a sentence appears in a snippet it cites; one that fails is unsupported without a model call."
            />
            <CheckboxGroup
              label="Reports checked"
              path="claim_verifier.applies_to"
              errors={errors}
              values={form.appliesTo}
              options={TARGET_OPTIONS}
              onChange={(v) => set({ appliesTo: v })}
              hint="Alerts are composed in code from the source's own headline, so they have no model-written sentences to check."
            />
          </section>

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

function OverrideItem({
  row,
  index,
  errors,
  onChange,
  onRemove,
}: {
  row: OverrideRow;
  index: number;
  errors: FieldErrors;
  onChange: (change: Partial<OverrideRow>) => void;
  onRemove: () => void;
}) {
  const path = overrideErrorPath(row);
  const messages = errorsAt(errors, path);
  return (
    <li className="flex flex-wrap items-start gap-3 rounded-md border border-border p-3" aria-label={`Metric band ${index + 1}`}>
      <TextField
        className="w-56"
        label="Metric"
        path={`${path}.metric`}
        errors={{}}
        value={row.metric}
        spellCheck={false}
        placeholder="revenue"
        onChange={(v) => onChange({ metric: v })}
        aria-invalid={messages.length > 0 || undefined}
      />
      <CheckboxField className="mt-7" label="No bound" checked={row.disabled} onChange={(v) => onChange({ disabled: v })} />
      {!row.disabled ? (
        <TextField
          className="w-40"
          label="Band (± %)"
          inputMode="decimal"
          path={`${path}.band`}
          errors={{}}
          value={row.band}
          onChange={(v) => onChange({ band: v })}
          aria-invalid={messages.length > 0 || undefined}
        />
      ) : null}
      <Button variant="ghost" size="icon" className="mt-6" aria-label={`Remove the band for ${row.metric || `row ${index + 1}`}`} onClick={onRemove}>
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
