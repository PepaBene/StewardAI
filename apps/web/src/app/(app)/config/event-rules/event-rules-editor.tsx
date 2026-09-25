"use client";

import { LoaderCircleIcon, PlusIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { CheckboxGroup, SelectField, TextField } from "@/components/config/fields";
import { SaveBar } from "@/components/config/save-bar";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type EventRulesConfig, type EventType } from "@/lib/api/client";
import {
  DEFAULT_SEVERITIES,
  eventRulesFromBody,
  eventRulesToBody,
  newOverride,
  type EventRulesForm,
  type OverrideForm,
} from "@/lib/config/news-forms";
import { useConfigEditor } from "@/lib/hooks/use-config-editor";
import { EVENT_LABELS, EVENT_TYPES, SEVERITY } from "@/lib/news/labels";

const api = () => getBrowserApiClient();
const LEVELS = ([0, 1, 2, 3] as const).map((s) => ({ value: s, label: `${s} · ${SEVERITY[s].label}` }));
const EVENT_OPTIONS = EVENT_TYPES.map((t) => ({ value: t, label: EVENT_LABELS[t] }));

export function EventRulesEditor() {
  const [form, setForm] = useState<EventRulesForm | null>(null);
  const [baseline, setBaseline] = useState("");
  const editor = useConfigEditor<EventRulesConfig>({
    load: () => api().GET("/config/event-rules"),
    save: (body, note) => api().PUT("/config/event-rules", { body, params: { query: { note } } }),
    validate: (body) => api().POST("/config/event-rules/validate", { body }),
    onLoaded: (body) => {
      const loaded = eventRulesFromBody(body);
      setForm(loaded);
      setBaseline(body ? JSON.stringify(eventRulesToBody(loaded)) : "");
    },
  });
  const body = useMemo(() => (form ? eventRulesToBody(form) : null), [form]);
  const dirty = body !== null && JSON.stringify(body) !== baseline;
  const set = (change: Partial<EventRulesForm>) => setForm((f) => (f ? { ...f, ...change } : f));
  const setOverride = (key: string, change: Partial<OverrideForm>) =>
    setForm((f) => (f ? { ...f, overrides: f.overrides.map((o) => (o.key === key ? { ...o, ...change } : o)) } : f));

  async function save(note: string | null) {
    if (body && (await editor.save(body, note))) setBaseline(JSON.stringify(body));
  }

  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm text-text-muted">
          <Link href="/config" className="hover:text-text hover:underline">
            Config
          </Link>{" "}
          / Event rules
        </p>
        <h1 className="text-2xl font-semibold tracking-tight">Event rules</h1>
        <p className="mt-1 max-w-prose text-sm text-text-muted">
          How much each kind of event matters. 0 is ignored, 1 goes in the digest, 2 is a digest
          highlight linked to the thesis, 3 is an instant alert once confirmed. The classifier only
          labels the event type; the severity always comes from here.
        </p>
      </div>
      {editor.status.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading event rules…
        </p>
      ) : null}
      {editor.status.kind === "error" ? <AlertMessage title="Could not load the event rules">{editor.status.message}</AlertMessage> : null}

      {form ? (
        <>
          <section aria-labelledby="severity-heading" className="space-y-3 rounded-lg border border-border bg-surface-raised p-4">
            <h2 id="severity-heading" className="text-lg font-semibold">
              Severity per event type
            </h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {EVENT_TYPES.map((t) => (
                <SelectField
                  key={t}
                  label={EVENT_LABELS[t]}
                  path={`severities.${t}`}
                  errors={editor.errors}
                  value={form.severities[t] as 0 | 1 | 2 | 3}
                  options={LEVELS}
                  onChange={(v) => set({ severities: { ...form.severities, [t]: v } })}
                  hint={form.severities[t] !== DEFAULT_SEVERITIES[t] ? `Default ${DEFAULT_SEVERITIES[t]}` : undefined}
                />
              ))}
            </div>
          </section>

          <section aria-labelledby="overrides-heading" className="space-y-3">
            <h2 id="overrides-heading" className="text-lg font-semibold">
              Per-holding overrides
            </h2>
            {form.overrides.map((o, i) => (
              <div key={o.key} className="flex flex-wrap items-start gap-3 rounded-lg border border-border bg-surface-raised p-3">
                <TextField className="w-56" label="Ticker" path={`overrides.${i}.ticker`} errors={editor.errors} value={o.ticker} onChange={(v) => setOverride(o.key, { ticker: v })} />
                <SelectField className="w-60" label="Event type" path={`overrides.${i}.event_type`} errors={editor.errors} value={o.event_type} options={EVENT_OPTIONS} onChange={(v) => setOverride(o.key, { event_type: v })} />
                <SelectField className="w-44" label="Severity" path={`overrides.${i}.severity`} errors={editor.errors} value={o.severity as 0 | 1 | 2 | 3} options={LEVELS} onChange={(v) => setOverride(o.key, { severity: v })} />
                <Button variant="ghost" size="icon" className="mt-6" aria-label="Remove override" onClick={() => set({ overrides: form.overrides.filter((x) => x.key !== o.key) })}>
                  <Trash2Icon aria-hidden="true" />
                </Button>
              </div>
            ))}
            <Button variant="outline" onClick={() => set({ overrides: [...form.overrides, newOverride()] })}>
              <PlusIcon aria-hidden="true" />
              Add override
            </Button>
          </section>

          <section aria-labelledby="adjust-heading" className="space-y-3 rounded-lg border border-border bg-surface-raised p-4">
            <h2 id="adjust-heading" className="text-lg font-semibold">
              Adjustments
            </h2>
            <CheckboxGroup label="Raise one level when the item touches a thesis KPI or exit condition" path="escalate_when_thesis_linked" errors={editor.errors} values={form.escalate_when_thesis_linked} options={EVENT_OPTIONS} onChange={(v) => set({ escalate_when_thesis_linked: v as EventType[] })} />
            <CheckboxGroup label="Queue an earnings thesis check when confirmed" path="earnings_check_on" errors={editor.errors} values={form.earnings_check_on} options={EVENT_OPTIONS} onChange={(v) => set({ earnings_check_on: v as EventType[] })} />
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <SelectField label="KPI breach alert severity" path="thesis_breach_severity" errors={editor.errors} value={form.thesis_breach_severity as 0 | 1 | 2 | 3} options={LEVELS} onChange={(v) => set({ thesis_breach_severity: v })} />
              <TextField label="Insider cluster: transactions" inputMode="numeric" path="insider_cluster_count" errors={editor.errors} value={form.insider_cluster_count} onChange={(v) => set({ insider_cluster_count: v })} />
              <TextField label="Insider cluster: within days" inputMode="numeric" path="insider_cluster_days" errors={editor.errors} value={form.insider_cluster_days} onChange={(v) => set({ insider_cluster_days: v })} />
              <TextField label="Recycled story after (days)" inputMode="numeric" path="recycled_after_days" errors={editor.errors} value={form.recycled_after_days} onChange={(v) => set({ recycled_after_days: v })} />
            </div>
          </section>

          <SaveBar dirty={dirty} busy={editor.busy} errors={editor.errors} message={editor.message} onValidate={() => body && void editor.validate(body)} onSave={(note) => void save(note)} />
        </>
      ) : null}
    </div>
  );
}
