"use client";

import { CalendarDaysIcon, ClockIcon, InfoIcon, LoaderCircleIcon, PlusIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import { useId, useMemo, useState, type ReactNode } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ConfigHeader } from "@/components/config/config-header";
import { CheckboxField, SelectField, TextAreaField, TextField } from "@/components/config/fields";
import { SaveBar } from "@/components/config/save-bar";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type JobsConfig } from "@/lib/api/client";
import type { FieldErrors } from "@/lib/api/validation-errors";
import { describeCron } from "@/lib/config/cron";
import {
  EFFORTS,
  jobsFromBody,
  jobsToBody,
  MODEL_SUGGESTIONS,
  newClosure,
  SCHEDULES,
  scheduleLabel,
  SETTINGS,
  SETTINGS_KEYS,
  type ClosureForm,
  type JobsForm,
  type ScheduleForm,
  type ScheduleKey,
  type SettingField,
  type SettingsKey,
} from "@/lib/config/jobs-form";
import { useConfigEditor } from "@/lib/hooks/use-config-editor";
import { SEVERITY } from "@/lib/news/labels";

const api = () => getBrowserApiClient();
const EFFORT_OPTIONS = EFFORTS.map((e) => ({ value: e, label: e }));
const SEVERITY_OPTIONS = (["1", "2", "3"] as const).map((s) => ({
  value: s,
  label: `${s} · ${SEVERITY[Number(s) as 1 | 2 | 3].label} and above`,
}));

export function JobsEditor() {
  const [form, setForm] = useState<JobsForm | null>(null);
  const [baseline, setBaseline] = useState("");
  const editor = useConfigEditor<JobsConfig>({
    load: () => api().GET("/config/jobs"),
    save: (body, note) => api().PUT("/config/jobs", { body, params: { query: { note } } }),
    validate: (body) => api().POST("/config/jobs/validate", { body }),
    onLoaded: (body) => {
      const loaded = jobsFromBody(body);
      setForm(loaded);
      setBaseline(body ? JSON.stringify(jobsToBody(loaded)) : "");
    },
  });
  const body = useMemo(() => (form ? jobsToBody(form) : null), [form]);
  const dirty = body !== null && JSON.stringify(body) !== baseline;

  const setSchedule = (name: string, change: Partial<ScheduleForm>) =>
    setForm((f) => (f ? { ...f, schedules: f.schedules.map((s) => (s.name === name ? { ...s, ...change } : s)) } : f));
  const setSetting = (section: SettingsKey, key: string, value: string | boolean) =>
    setForm((f) =>
      f
        ? {
            ...f,
            settings: {
              ...f.settings,
              [section]: { ...f.settings[section], values: { ...f.settings[section].values, [key]: value } },
            },
          }
        : f,
    );

  async function save(note: string | null) {
    if (body && (await editor.save(body, note))) setBaseline(JSON.stringify(body));
  }

  return (
    <div className="space-y-6">
      <ConfigHeader title="Jobs" domain="jobs">
        <p>
          When each scheduled job runs, whether it runs at all, and the models and caps of the jobs
          that call Claude. Times are read in each schedule&rsquo;s time zone.
        </p>
      </ConfigHeader>
      <AlertMessage tone="info" title="No restart needed">
        The worker re-reads this config every few minutes: a saved schedule is used within 5
        minutes. A job already running finishes with the settings it started with.
      </AlertMessage>

      {editor.status.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading jobs…
        </p>
      ) : null}
      {editor.status.kind === "error" ? (
        <AlertMessage title="Could not load the jobs config">{editor.status.message}</AlertMessage>
      ) : null}
      {editor.status.kind === "ready" && editor.status.version === null ? (
        <p className="text-sm text-text-muted">Nothing saved yet: the worker runs on these defaults.</p>
      ) : null}

      {form ? (
        <>
          <section aria-labelledby="schedules-heading" className="space-y-3">
            <h2 id="schedules-heading" className="text-lg font-semibold">
              Schedules
            </h2>
            {form.schedules.map((s) => (
              <ScheduleCard key={s.name} schedule={s} errors={editor.errors} onChange={(change) => setSchedule(s.name, change)} />
            ))}
          </section>

          {SETTINGS_KEYS.map((section) => (
            <SettingsCard
              key={section}
              section={section}
              values={form.settings[section].values}
              errors={editor.errors}
              onChange={(key, value) => setSetting(section, key, value)}
            >
              {section === "calendar" ? (
                <ClosuresEditor
                  closures={form.closures}
                  errors={editor.errors}
                  onChange={(closures) => setForm((f) => (f ? { ...f, closures } : f))}
                />
              ) : null}
            </SettingsCard>
          ))}

          <SaveBar
            dirty={dirty}
            busy={editor.busy}
            errors={editor.errors}
            message={editor.message ? `${editor.message}${editor.message.startsWith("Saved") ? " The worker uses it within 5 minutes." : ""}` : null}
            onValidate={() => body && void editor.validate(body)}
            onSave={(note) => void save(note)}
          />
        </>
      ) : null}
    </div>
  );
}

function ScheduleCard({
  schedule,
  errors,
  onChange,
}: {
  schedule: ScheduleForm;
  errors: FieldErrors;
  onChange: (change: Partial<ScheduleForm>) => void;
}) {
  const known = SCHEDULES[schedule.name as ScheduleKey];
  const reading = describeCron(schedule.cron);
  const headingId = useId();
  // A model-level error (bad cron or time zone) is reported on the schedule itself.
  const own = errors[schedule.name] ?? [];
  return (
    <div aria-labelledby={headingId} role="group" className="space-y-3 rounded-lg border border-border bg-surface-raised p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h3 id={headingId} className="font-medium">
            {scheduleLabel(schedule.name)} <span className="font-mono text-xs text-text-muted">{schedule.name}</span>
          </h3>
          {known ? <p className="text-sm text-text-muted">{known.description}</p> : null}
        </div>
        <CheckboxField label="Enabled" checked={schedule.enabled} onChange={(v) => onChange({ enabled: v })} />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField
          label="Cron (minute hour day month weekday)"
          path={`${schedule.name}.cron`}
          errors={errors}
          value={schedule.cron}
          spellCheck={false}
          onChange={(v) => onChange({ cron: v })}
          hint={
            <span className="inline-flex items-center gap-1">
              <ClockIcon aria-hidden="true" className="size-3.5" />
              {reading.text}
              {known && schedule.cron.trim() !== known.cron ? ` Default: ${known.cron}` : null}
            </span>
          }
        />
        <TextField
          label="Time zone"
          path={`${schedule.name}.timezone`}
          errors={errors}
          value={schedule.timezone}
          spellCheck={false}
          onChange={(v) => onChange({ timezone: v })}
          hint="IANA name, e.g. Europe/Prague or America/New_York."
        />
      </div>
      {own.length > 0 ? <p className="text-xs text-text">{own.join(" ")}</p> : null}
      {!schedule.enabled ? (
        <p className="flex items-center gap-1.5 text-xs text-text-muted">
          <InfoIcon aria-hidden="true" className="size-3.5" />
          Disabled: it does not run on a schedule; you can still start it by hand.
        </p>
      ) : null}
    </div>
  );
}

function SettingsCard({
  section,
  values,
  errors,
  onChange,
  children,
}: {
  section: SettingsKey;
  values: Record<string, string | boolean>;
  errors: FieldErrors;
  onChange: (key: string, value: string | boolean) => void;
  children?: ReactNode;
}) {
  const info = SETTINGS[section];
  const listId = useId();
  const headingId = `${section}-heading`;
  return (
    <section aria-labelledby={headingId} className="space-y-3 rounded-lg border border-border bg-surface-raised p-4">
      <div>
        <h2 id={headingId} className="text-lg font-semibold">
          {info.title}
        </h2>
        <p className="text-sm text-text-muted">{info.description}</p>
      </div>
      <datalist id={listId}>
        {MODEL_SUGGESTIONS.map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {(info.fields as SettingField<SettingsKey>[]).map((field) => {
          const path = `${section}.${field.key}`;
          const value = values[field.key];
          const range = field.min !== undefined && field.max !== undefined ? `${field.min} to ${field.max}.` : undefined;
          const hint = [field.hint, range].filter(Boolean).join(" ") || undefined;
          if (field.kind === "bool") {
            return <CheckboxField key={field.key} label={field.label} checked={Boolean(value)} onChange={(v) => onChange(field.key, v)} hint={field.hint} />;
          }
          if (field.kind === "effort") {
            return <SelectField key={field.key} label={field.label} path={path} errors={errors} value={String(value)} options={EFFORT_OPTIONS} onChange={(v) => onChange(field.key, v)} hint={field.hint} />;
          }
          if (field.kind === "lines") {
            return <TextAreaField key={field.key} label={field.label} path={path} errors={errors} value={String(value)} rows={3} onChange={(v) => onChange(field.key, v)} hint={hint} />;
          }
          if (field.kind === "severity") {
            return <SelectField key={field.key} label={field.label} path={path} errors={errors} value={String(value) as "1" | "2" | "3"} options={SEVERITY_OPTIONS} onChange={(v) => onChange(field.key, v)} hint={field.hint} />;
          }
          return (
            <TextField
              key={field.key}
              label={field.label}
              path={path}
              errors={{
                ...errors,
                // Errors per list item ("check_days_after_report.1") shown on the list field.
                [path]: Object.entries(errors)
                  .filter(([p]) => p === path || p.startsWith(`${path}.`))
                  .flatMap(([, ms]) => ms),
              }}
              value={String(value)}
              list={field.kind === "model" ? listId : undefined}
              inputMode={field.kind === "int" ? "numeric" : field.kind === "number" ? "decimal" : undefined}
              spellCheck={false}
              onChange={(v) => onChange(field.key, v)}
              hint={hint}
            />
          );
        })}
      </div>
      {children}
    </section>
  );
}

function ClosuresEditor({
  closures,
  errors,
  onChange,
}: {
  closures: ClosureForm[];
  errors: FieldErrors;
  onChange: (closures: ClosureForm[]) => void;
}) {
  const path = "calendar.exchange_closures";
  const set = (key: string, change: Partial<ClosureForm>) =>
    onChange(closures.map((c) => (c.key === key ? { ...c, ...change } : c)));
  const own = errors[path] ?? [];
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">Exchange closures</legend>
      <p className="text-xs text-text-muted">
        Days an exchange is closed, used without a deploy. Public holidays are not exchange calendars; review the suggested
        days on the{" "}
        <Link href="/config/calendar" className="text-accent underline-offset-4 hover:underline">
          exchange calendar
        </Link>{" "}
        page.
      </p>
      {closures.length === 0 ? <p className="text-sm text-text-muted">No closures confirmed.</p> : null}
      {closures.map((c, i) => (
        <div key={c.key} role="group" aria-label={`Closure ${i + 1}`} className="grid grid-cols-[0.8fr_1fr_2fr_auto] items-end gap-2">
          <TextField
            label="MIC"
            path={`${path}.${i}.mic`}
            errors={errors}
            value={c.mic}
            maxLength={4}
            placeholder="XNYS"
            spellCheck={false}
            hideLabel={i > 0}
            onChange={(v) => set(c.key, { mic: v })}
          />
          <TextField
            label="Date"
            type="date"
            path={`${path}.${i}.date`}
            errors={errors}
            value={c.date}
            hideLabel={i > 0}
            onChange={(v) => set(c.key, { date: v })}
          />
          <TextField
            label="Name"
            path={`${path}.${i}.name`}
            errors={errors}
            value={c.name}
            maxLength={100}
            placeholder="Good Friday"
            hideLabel={i > 0}
            onChange={(v) => set(c.key, { name: v })}
          />
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Remove closure ${c.mic || i + 1} ${c.date}`}
            onClick={() => onChange(closures.filter((x) => x.key !== c.key))}
          >
            <Trash2Icon aria-hidden="true" />
          </Button>
        </div>
      ))}
      {own.length > 0 ? <p className="text-xs text-text">{own.join(" ")}</p> : null}
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={() => onChange([...closures, newClosure()])}>
          <PlusIcon aria-hidden="true" />
          Add closure
        </Button>
        <Button asChild variant="ghost" size="sm">
          <Link href="/config/calendar">
            <CalendarDaysIcon aria-hidden="true" />
            Suggested closures
          </Link>
        </Button>
      </div>
    </fieldset>
  );
}
