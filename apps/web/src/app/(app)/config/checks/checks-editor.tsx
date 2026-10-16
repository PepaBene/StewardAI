"use client";

import { LoaderCircleIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ConfigHeader } from "@/components/config/config-header";
import { CheckboxField, TextField } from "@/components/config/fields";
import { SaveBar } from "@/components/config/save-bar";
import { StatTile, SummaryPanel } from "@/components/config/summary";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { checksFromBody, checksToBody, fieldPath, FIELDS, SECTIONS, validateChecksForm, type ChecksConfig, type ChecksForm } from "@/lib/config/checks-form";
import { mergeErrors } from "@/lib/config/strategy-form";
import { configSaveQuery, useConfigEditor } from "@/lib/hooks/use-config-editor";

const api = () => getBrowserApiClient();

type ChecksPreview = components["schemas"]["ChecksPreview"];
type PreviewState = { kind: "idle" } | { kind: "loading" } | { kind: "ready"; data: ChecksPreview } | { kind: "error"; message: string };

/**
 * Settings › Checks: every limit the five-axis checklist, the risk checks, the dividend income
 * forecast and the fair-value alerts read. Versioned like the other config, so a check result can
 * always be traced to the rules it was computed under.
 */
export function ChecksEditor() {
  const [form, setForm] = useState<ChecksForm | null>(null);
  const [base, setBase] = useState<ChecksConfig | null>(null);
  const [baseForm, setBaseForm] = useState<ChecksForm | null>(null);
  const [baseline, setBaseline] = useState("");

  const adopt = (body: ChecksConfig) => {
    const f = checksFromBody(body);
    setBase(body);
    setForm(f);
    setBaseForm(f);
    setBaseline(JSON.stringify(checksToBody(f, body)));
  };

  const editor = useConfigEditor<ChecksConfig>({
    load: () => api().GET("/config/checks"),
    save: (body, note, baseId) => api().PUT("/config/checks", { body, params: { query: configSaveQuery(note, baseId) } }),
    validate: (body) => api().POST("/config/checks/validate", { body }),
    onLoaded: (body) => {
      if (body) {
        adopt(body);
        return;
      }
      void api()
        .GET("/config/checks/defaults")
        .then(({ data }) => data && adopt(data));
    },
  });

  const body = useMemo(() => (form && base ? checksToBody(form, base) : null), [form, base]);
  const clientErrors = useMemo(() => (form ? validateChecksForm(form) : {}), [form]);
  const errors = useMemo(() => mergeErrors(clientErrors, editor.errors), [clientErrors, editor.errors]);
  const blocked = Object.keys(clientErrors).length > 0;
  const dirty = body !== null && JSON.stringify(body) !== baseline;
  const changedKeys = useMemo(() => {
    if (!form || !baseForm) return [];
    return Object.keys(form.values).filter((k) => form.values[k] !== baseForm.values[k]);
  }, [form, baseForm]);
  const changedBySection = useMemo(() => {
    const out: Record<string, number> = {};
    for (const f of FIELDS) if (changedKeys.includes(fieldPath(f))) out[f.section] = (out[f.section] ?? 0) + 1;
    return out;
  }, [changedKeys]);
  const [preview, setPreview] = useState<PreviewState>({ kind: "idle" });
  const draftKey = body ? JSON.stringify(body) : "";
  useEffect(() => {
    if (!body || !dirty || blocked) return;
    let live = true;
    const timer = setTimeout(() => {
      setPreview((p) => (p.kind === "ready" ? p : { kind: "loading" }));
      api()
        .POST("/checks/preview", { body })
        .then(({ data, error }) => {
          if (!live) return;
          setPreview(data ? { kind: "ready", data } : { kind: "error", message: error ? "The preview could not be computed." : "No preview." });
        })
        .catch(() => live && setPreview({ kind: "error", message: "The preview could not be computed." }));
    }, 400);
    return () => {
      live = false;
      clearTimeout(timer);
    };
    // The serialized draft is the dependency; `body` is rebuilt on every edit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftKey, dirty, blocked]);
  const shown = dirty && !blocked && preview.kind === "ready" ? preview.data : null;
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
          <SummaryPanel
            title="Limits changed"
            reading={
              changedKeys.length === 0
                ? "No limit differs from the active version."
                : `${changedKeys.length} limit${changedKeys.length === 1 ? "" : "s"} differ from the active version. The next view of each company recomputes its checks with them.`
            }
          >
            <div className="flex flex-wrap gap-3">
              <StatTile label="Limits changed" value={changedKeys.length} />
              <StatTile
                label="Holdings that would change state"
                value={!dirty ? 0 : shown ? shown.changed_count : "…"}
                note={
                  !dirty
                    ? "Change a limit to see the effect."
                    : blocked
                      ? "Fix the marked fields first."
                      : preview.kind === "error"
                        ? preview.message
                        : shown
                          ? `of ${shown.holdings.length} checked${shown.status === "partial" ? " (not all holdings could be checked)" : ""}`
                          : "Working it out…"
                }
              />
            </div>
            {shown && shown.changed_count > 0 ? (
              <ul aria-label="Holdings whose checks would change" className="divide-y divide-border/50 text-sm">
                {shown.holdings
                  .filter((h) => h.changed)
                  .slice(0, 8)
                  .map((h) => (
                    <li key={h.ticker} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-1.5">
                      <span className="font-medium">{h.name ?? h.ticker}</span>
                      <span className="text-text-muted">
                        {h.changes
                          .slice(0, 3)
                          .map((c) => `${c.label}: ${c.was} to ${c.now}`)
                          .join("; ")}
                        {h.changes.length > 3 ? `; ${h.changes.length - 3} more` : ""}
                      </span>
                      <span className="ml-auto tabular-nums text-text-muted">
                        {h.current.passes} to {h.would_be.passes} passing
                      </span>
                    </li>
                  ))}
              </ul>
            ) : null}
          </SummaryPanel>
          {SECTIONS.map((s, index) => (
            <details key={s.id} open={index === 0 || undefined} aria-labelledby={`${s.id}-heading`} className="group rounded-card bg-surface-muted">
              <summary className="flex cursor-pointer list-none flex-wrap items-baseline gap-x-3 px-5 py-3 outline-none focus-visible:ring-2 focus-visible:ring-focus/30">
                <h2 id={`${s.id}-heading`} className="text-lg font-semibold">
                  {s.title}
                </h2>
                <span className="text-sm text-text-muted">
                  {s.note}
                  {changedBySection[s.id] ? ` ${changedBySection[s.id]} changed.` : ""}
                </span>
              </summary>
              <div className="grid gap-3 px-5 pb-5 sm:grid-cols-2">
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
            </details>
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
