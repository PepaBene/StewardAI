"use client";

import { LoaderCircleIcon } from "lucide-react";
import { useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ConfigHeader } from "@/components/config/config-header";
import { CheckboxField, TextField } from "@/components/config/fields";
import { SaveBar } from "@/components/config/save-bar";
import { BlueprintCorners } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { checksFromBody, checksToBody, fieldPath, FIELDS, SECTIONS, validateChecksForm, type ChecksConfig, type ChecksForm } from "@/lib/config/checks-form";
import { mergeErrors } from "@/lib/config/strategy-form";
import { configSaveQuery, useConfigEditor } from "@/lib/hooks/use-config-editor";

const api = () => getBrowserApiClient();
const SECTION = "blueprint space-y-3 p-5";

/**
 * Settings › Checks: every limit the five-axis checklist, the risk checks, the dividend income
 * forecast and the fair-value alerts read. Versioned like the other config, so a check result can
 * always be traced to the rules it was computed under.
 */
export function ChecksEditor() {
  const [form, setForm] = useState<ChecksForm | null>(null);
  const [base, setBase] = useState<ChecksConfig | null>(null);
  const [baseline, setBaseline] = useState("");

  const adopt = (body: ChecksConfig, saved: boolean) => {
    const f = checksFromBody(body);
    setBase(body);
    setForm(f);
    setBaseline(saved ? JSON.stringify(checksToBody(f, body)) : "");
  };

  const editor = useConfigEditor<ChecksConfig>({
    load: () => api().GET("/config/checks"),
    save: (body, note, baseId) => api().PUT("/config/checks", { body, params: { query: configSaveQuery(note, baseId) } }),
    validate: (body) => api().POST("/config/checks/validate", { body }),
    onLoaded: (body) => {
      if (body) {
        adopt(body, true);
        return;
      }
      void api()
        .GET("/config/checks/defaults")
        .then(({ data }) => data && adopt(data, false));
    },
  });

  const body = useMemo(() => (form && base ? checksToBody(form, base) : null), [form, base]);
  const clientErrors = useMemo(() => (form ? validateChecksForm(form) : {}), [form]);
  const errors = useMemo(() => mergeErrors(clientErrors, editor.errors), [clientErrors, editor.errors]);
  const blocked = Object.keys(clientErrors).length > 0;
  const dirty = body !== null && JSON.stringify(body) !== baseline;
  const setValue = (k: string, v: string) => setForm((f) => (f ? { ...f, values: { ...f.values, [k]: v } } : f));

  async function save(note: string | null) {
    if (!body || blocked) return;
    if (await editor.save(body, note)) setBaseline(JSON.stringify(body));
  }

  return (
    <div className="space-y-6">
      <ConfigHeader title="Checks" domain="checks">
        <p>
          The limits behind each company&rsquo;s checklist (value, future, past, health, dividend and the risk list), how
          dividend income is forecast, and when a fair value counts as stale. Checks are recomputed with the new limits on the
          next view; earlier results keep the version they were computed under.
        </p>
      </ConfigHeader>

      {editor.status.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading the checks config…
        </p>
      ) : null}
      {editor.status.kind === "error" ? <AlertMessage title="Could not load the checks config">{editor.status.message}</AlertMessage> : null}
      {editor.status.kind === "ready" && editor.status.version === null ? (
        <AlertMessage tone="info" title="Defaults in use">
          Nothing saved yet: the values below are the backend&rsquo;s defaults.
        </AlertMessage>
      ) : null}

      {form ? (
        <>
          {SECTIONS.map((s) => (
            <section key={s.id} aria-labelledby={`${s.id}-heading`} className={SECTION}>
              <BlueprintCorners />
              <h2 id={`${s.id}-heading`} className="text-[22px] leading-tight font-semibold">
                {s.title}
              </h2>
              <p className="max-w-prose text-sm text-text-muted">{s.note}</p>
              <div className="grid gap-3 sm:grid-cols-2">
                {FIELDS.filter((f) => f.section === s.id).map((f) =>
                  f.kind === "bool" ? (
                    <CheckboxField
                      key={f.key}
                      label={f.label}
                      checked={form.values[fieldPath(f)] === "true"}
                      onChange={(v) => setValue(fieldPath(f), v ? "true" : "false")}
                      hint={f.hint}
                    />
                  ) : (
                    <TextField
                      key={f.key}
                      label={`${f.label}${f.kind === "percent" ? " (%)" : ""}`}
                      inputMode={f.kind === "int" ? "numeric" : "decimal"}
                      path={fieldPath(f)}
                      errors={errors}
                      value={form.values[fieldPath(f)] ?? ""}
                      onChange={(v) => setValue(fieldPath(f), v)}
                      hint={f.hint}
                    />
                  ),
                )}
                {s.id === "dividend" ? (
                  <>
                    <TextField
                      label="Quality tier: low below (%)"
                      inputMode="decimal"
                      path="dividends.tier_cuts"
                      errors={errors}
                      value={form.tierLow}
                      onChange={(v) => setForm((f) => (f ? { ...f, tierLow: v } : f))}
                      hint="Share of applicable dividend checks that pass."
                    />
                    <TextField
                      label="Quality tier: high from (%)"
                      inputMode="decimal"
                      path="dividends.tier_cuts.1"
                      errors={{}}
                      value={form.tierHigh}
                      onChange={(v) => setForm((f) => (f ? { ...f, tierHigh: v } : f))}
                      hint="Between the two cuts is medium."
                    />
                  </>
                ) : null}
              </div>
            </section>
          ))}

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
