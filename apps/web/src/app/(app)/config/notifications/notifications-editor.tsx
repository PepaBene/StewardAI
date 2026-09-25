"use client";

import { ArrowDownIcon, ArrowUpIcon, BellRingIcon, LoaderCircleIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ConfigHeader } from "@/components/config/config-header";
import { CheckboxField, CheckboxGroup, SelectField, TextAreaField, TextField } from "@/components/config/fields";
import { SaveBar } from "@/components/config/save-bar";
import { Button } from "@/components/ui/button";
import {
  getBrowserApiClient,
  type EventType,
  type NotificationsConfig,
  type TestAlertResult,
} from "@/lib/api/client";
import type { FieldErrors } from "@/lib/api/validation-errors";
import {
  CHANNEL_LABELS,
  CHANNELS,
  newRule,
  notificationsFromBody,
  notificationsToBody,
  QUIET_DAYS,
  QUIET_MODES,
  type NotificationsForm,
  type RuleForm,
} from "@/lib/config/news-forms";
import { formatTime } from "@/lib/format";
import { useConfigEditor } from "@/lib/hooks/use-config-editor";
import { EVENT_LABELS, EVENT_TYPES, SEVERITY } from "@/lib/news/labels";

const api = () => getBrowserApiClient();
const EVENT_OPTIONS = EVENT_TYPES.map((t) => ({ value: t, label: EVENT_LABELS[t] }));
const SEVERITY_OPTIONS = ([3, 2, 1] as const).map((s) => ({ value: s, label: `${s} · ${SEVERITY[s].label}` }));
const CHANNEL_OPTIONS = CHANNELS.map((c) => ({ value: c, label: CHANNEL_LABELS[c] }));
const SYSTEM_LEVELS = ([3, 2, 1, 0] as const).map((s) => ({
  value: s,
  label: s === 0 ? "0 · Never alert" : `${s} · ${SEVERITY[s].label}`,
}));

export function NotificationsEditor() {
  const [form, setForm] = useState<NotificationsForm | null>(null);
  const [baseline, setBaseline] = useState("");

  const editor = useConfigEditor<NotificationsConfig>({
    load: () => api().GET("/config/notifications"),
    save: (body, note) => api().PUT("/config/notifications", { body, params: { query: { note } } }),
    validate: (body) => api().POST("/config/notifications/validate", { body }),
    onLoaded: (body) => {
      const loaded = notificationsFromBody(body);
      setForm(loaded);
      setBaseline(body ? JSON.stringify(notificationsToBody(loaded)) : "");
    },
  });

  const body = useMemo(() => (form ? notificationsToBody(form) : null), [form]);
  const dirty = body !== null && JSON.stringify(body) !== baseline;
  const set = (change: Partial<NotificationsForm>) => setForm((f) => (f ? { ...f, ...change } : f));
  const setRule = (key: string, change: Partial<RuleForm>) =>
    setForm((f) => (f ? { ...f, rules: f.rules.map((r) => (r.key === key ? { ...r, ...change } : r)) } : f));
  const move = (index: number, by: -1 | 1) =>
    setForm((f) => {
      if (!f) return f;
      const rules = [...f.rules];
      const [rule] = rules.splice(index, 1);
      rules.splice(index + by, 0, rule);
      return { ...f, rules };
    });

  async function save(note: string | null) {
    if (body && (await editor.save(body, note))) setBaseline(JSON.stringify(body));
  }

  return (
    <div className="space-y-6">
      <ConfigHeader title="Notifications" domain="notifications">
        <p>
          Which confirmed events reach you right away, on which channel. Rules are checked top to
          bottom and the first match decides; anything no rule matches waits for the digest. Rumours
          and unconfirmed stories never alert. Changes apply to the next alert, no restart.
        </p>
      </ConfigHeader>

      {editor.status.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading notification rules…
        </p>
      ) : null}
      {editor.status.kind === "error" ? (
        <AlertMessage title="Could not load the notification rules">{editor.status.message}</AlertMessage>
      ) : null}
      {editor.status.kind === "ready" && editor.status.version === null ? (
        <AlertMessage tone="info" title="Defaults in use">
          Nothing saved yet: severity-3 events go to Telegram, held during quiet hours (22:00–07:00).
        </AlertMessage>
      ) : null}

      {form ? (
        <>
          <section aria-labelledby="rules-heading" className="space-y-3">
            <h2 id="rules-heading" className="text-lg font-semibold">
              Rules
            </h2>
            {form.rules.map((rule, index) => (
              <RuleCard
                key={rule.key}
                rule={rule}
                index={index}
                count={form.rules.length}
                errors={editor.errors}
                onChange={(change) => setRule(rule.key, change)}
                onMove={(by) => move(index, by)}
                onRemove={() => set({ rules: form.rules.filter((r) => r.key !== rule.key) })}
              />
            ))}
            <Button variant="outline" onClick={() => set({ rules: [...form.rules, newRule()] })}>
              <PlusIcon aria-hidden="true" />
              Add rule
            </Button>
          </section>

          <section aria-labelledby="quiet-heading" className="space-y-3 rounded-lg border border-border bg-surface-raised p-4">
            <h2 id="quiet-heading" className="text-lg font-semibold">
              Quiet hours and limits
            </h2>
            <CheckboxField label="Quiet hours" checked={form.quiet_enabled} onChange={(v) => set({ quiet_enabled: v })} />
            <div className="grid gap-3 sm:grid-cols-3">
              <TextField label="From" type="time" path="quiet_hours.start" errors={editor.errors} value={form.quiet_start} onChange={(v) => set({ quiet_start: v })} />
              <TextField label="To" type="time" path="quiet_hours.end" errors={editor.errors} value={form.quiet_end} onChange={(v) => set({ quiet_end: v })} />
              <TextField label="Time zone" path="quiet_hours.timezone" errors={editor.errors} value={form.quiet_timezone} onChange={(v) => set({ quiet_timezone: v })} />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <CheckboxGroup
                label="Quiet all day on"
                path="quiet_all_day_on"
                errors={editor.errors}
                values={form.quiet_all_day_on}
                options={QUIET_DAYS}
                onChange={(v) => set({ quiet_all_day_on: v })}
                hint="These days are quiet from start to end: alerts are held (or go to the digest) as during quiet hours."
              />
              <TextField
                label="Public holidays of (country)"
                path="holiday_country"
                errors={editor.errors}
                value={form.holiday_country}
                maxLength={2}
                spellCheck={false}
                onChange={(v) => set({ holiday_country: v.toUpperCase() })}
                hint="Two-letter code, e.g. CZ. Used when public holidays are quiet; the holidays come from the Nager.Date calendar."
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <TextField
                label="Daily alert cap"
                inputMode="numeric"
                path="daily_alert_cap"
                errors={editor.errors}
                value={form.daily_alert_cap}
                onChange={(v) => set({ daily_alert_cap: v })}
                hint="Empty: no cap. Alerts over the cap wait for the digest."
              />
              <TextField
                label="Repeat throttle (hours)"
                inputMode="numeric"
                path="repeat_throttle_hours"
                errors={editor.errors}
                value={form.repeat_throttle_hours}
                onChange={(v) => set({ repeat_throttle_hours: v })}
                hint="A second story with the same holding and event type is not alerted again within this window."
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <TextAreaField label="Muted holdings" path="muted_tickers" errors={editor.errors} value={form.muted_tickers} onChange={(v) => set({ muted_tickers: v })} rows={2} hint="One ticker per line." />
              <CheckboxGroup
                label="Muted event types"
                path="muted_event_types"
                errors={editor.errors}
                values={form.muted_event_types}
                options={EVENT_OPTIONS}
                onChange={(v) => set({ muted_event_types: v as EventType[] })}
              />
            </div>
            <SelectField
              className="max-w-sm"
              label="Monitoring alert severity"
              path="system_alert_severity"
              errors={editor.errors}
              value={form.system_alert_severity}
              options={SYSTEM_LEVELS}
              onChange={(v) => set({ system_alert_severity: v })}
              hint="Severity of system alerts (a failed job, a missed run, a holding with no news coverage). They are routed by the rules above like any event."
            />
            <CheckboxGroup
              label="Digest channels"
              path="digest_channels"
              errors={editor.errors}
              values={form.digest_channels}
              options={CHANNEL_OPTIONS}
              onChange={(v) => set({ digest_channels: v })}
              hint="Where the daily and weekly digests are sent."
            />
          </section>

          <TestAlertPanel dirty={dirty} />

          <SaveBar
            dirty={dirty}
            busy={editor.busy}
            errors={editor.errors}
            message={editor.message}
            onValidate={() => body && void editor.validate(body)}
            onSave={(note) => void save(note)}
          />
        </>
      ) : null}
    </div>
  );
}

function RuleCard({
  rule,
  index,
  count,
  errors,
  onChange,
  onMove,
  onRemove,
}: {
  rule: RuleForm;
  index: number;
  count: number;
  errors: FieldErrors;
  onChange: (change: Partial<RuleForm>) => void;
  onMove: (by: -1 | 1) => void;
  onRemove: () => void;
}) {
  const path = `rules.${index}`;
  return (
    <div className="space-y-3 rounded-lg border border-border bg-surface-raised p-4" aria-label={`Rule ${index + 1}`} role="group">
      <div className="flex flex-wrap items-start gap-3">
        <span className="mt-7 font-mono text-sm text-text-muted">{index + 1}.</span>
        <TextField className="w-60" label="Rule id" path={`${path}.id`} errors={errors} value={rule.id} onChange={(v) => onChange({ id: v })} placeholder="instant-severity-3" />
        <CheckboxField className="mt-7" label="Enabled" checked={rule.enabled} onChange={(v) => onChange({ enabled: v })} />
        <div className="ml-auto flex gap-1">
          <Button variant="ghost" size="icon" aria-label="Move rule up" disabled={index === 0} onClick={() => onMove(-1)}>
            <ArrowUpIcon aria-hidden="true" />
          </Button>
          <Button variant="ghost" size="icon" aria-label="Move rule down" disabled={index === count - 1} onClick={() => onMove(1)}>
            <ArrowDownIcon aria-hidden="true" />
          </Button>
          <Button variant="ghost" size="icon" aria-label={`Remove rule ${rule.id || index + 1}`} onClick={onRemove}>
            <Trash2Icon aria-hidden="true" />
          </Button>
        </div>
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <CheckboxGroup label="Severity" path={`${path}.match.severities`} errors={errors} values={rule.severities} options={SEVERITY_OPTIONS} onChange={(v) => onChange({ severities: v })} />
        <CheckboxGroup label="Channels" path={`${path}.channels`} errors={errors} values={rule.channels} options={CHANNEL_OPTIONS} onChange={(v) => onChange({ channels: v })} />
        <SelectField label="During quiet hours" path={`${path}.during_quiet_hours`} errors={errors} value={rule.during_quiet_hours} options={QUIET_MODES} onChange={(v) => onChange({ during_quiet_hours: v })} />
      </div>
      <details className="rounded-md border border-border p-3">
        <summary className="cursor-pointer text-sm font-medium">
          Narrow to event types or holdings
          {rule.event_types.length || rule.tickers.trim() ? " (narrowed)" : " (any)"}
        </summary>
        <div className="mt-3 grid gap-4 lg:grid-cols-[2fr_1fr]">
          <CheckboxGroup label="Event types (none ticked: any)" path={`${path}.match.event_types`} errors={errors} values={rule.event_types} options={EVENT_OPTIONS} onChange={(v) => onChange({ event_types: v as EventType[] })} />
          <TextAreaField label="Holdings (empty: any)" path={`${path}.match.tickers`} errors={errors} value={rule.tickers} onChange={(v) => onChange({ tickers: v })} hint="One ticker per line." />
        </div>
      </details>
    </div>
  );
}

function TestAlertPanel({ dirty }: { dirty: boolean }) {
  const [severity, setSeverity] = useState<1 | 2 | 3>(3);
  const [eventType, setEventType] = useState<EventType>("guidance_change");
  const [result, setResult] = useState<TestAlertResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  async function send() {
    setSending(true);
    setError(null);
    try {
      const { data, response } = await api().POST("/alerts/test", { body: { severity, event_type: eventType } });
      if (data) setResult(data);
      else setError(`The API answered HTTP ${response.status}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSending(false);
    }
  }

  return (
    <section aria-labelledby="test-heading" className="space-y-3 rounded-lg border border-border bg-surface-raised p-4">
      <h2 id="test-heading" className="text-lg font-semibold">
        Send a test alert
      </h2>
      <p className="max-w-prose text-sm text-text-muted">
        Routed through the <strong>saved</strong> rules exactly like a real event (quiet hours
        included), then sent by the worker within seconds.
        {dirty ? " Save first to test your unsaved changes." : ""}
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <SelectField className="w-44" label="Severity" path="test.severity" errors={{}} value={severity} options={SEVERITY_OPTIONS} onChange={setSeverity} />
        <SelectField className="w-60" label="Event type" path="test.event_type" errors={{}} value={eventType} options={EVENT_OPTIONS} onChange={setEventType} />
        <Button onClick={() => void send()} disabled={sending}>
          {sending ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <BellRingIcon aria-hidden="true" />}
          Send test alert
        </Button>
      </div>
      {error ? <AlertMessage title="Could not send">{error}</AlertMessage> : null}
      {result ? (
        <AlertMessage tone="info" title={result.alerts.length ? "Test alert routed" : "No alert"}>
          <p>{result.explanation}</p>
          {result.channels.length ? <p>Channels: {result.channels.map((c) => CHANNEL_LABELS[c as keyof typeof CHANNEL_LABELS] ?? c).join(", ")}</p> : null}
          {result.held_until ? <p>Held until {formatTime(new Date(result.held_until))}.</p> : null}
          {result.suppressed_reason ? <p>Suppressed: {result.suppressed_reason}</p> : null}
        </AlertMessage>
      ) : null}
    </section>
  );
}
