"use client";

import { ArrowDownIcon, ArrowUpIcon, LoaderCircleIcon, PlusIcon, RotateCcwIcon, Trash2Icon } from "lucide-react";
import { useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ConfigHeader } from "@/components/config/config-header";
import { CheckboxGroup, SelectField, TextAreaField, TextField } from "@/components/config/fields";
import { ReplayPreviewDialog, useReplayGate } from "@/components/config/replay-preview";
import { SaveBar } from "@/components/config/save-bar";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type EventRulesConfig, type EventType } from "@/lib/api/client";
import { foldChildErrors, type FieldErrors } from "@/lib/api/validation-errors";
import {
  ANNOUNCEMENT_SERVICES,
  DEFAULT_ANNOUNCEMENT_RULES,
  DEFAULT_SEVERITIES,
  eventRulesFromBody,
  eventRulesToBody,
  newAnnouncementRule,
  newOverride,
  type AnnouncementRuleForm,
  type AnnouncementTarget,
  type EventRulesForm,
  type OverrideForm,
} from "@/lib/config/news-forms";
import { useConfigEditor } from "@/lib/hooks/use-config-editor";
import { EVENT_LABELS, EVENT_TYPES, SEVERITY } from "@/lib/news/labels";

const api = () => getBrowserApiClient();
const LEVELS = ([0, 1, 2, 3] as const).map((s) => ({ value: s, label: `${s} · ${SEVERITY[s].label}` }));
const EVENT_OPTIONS = EVENT_TYPES.map((t) => ({ value: t, label: EVENT_LABELS[t] }));
const TARGET_OPTIONS: { value: AnnouncementTarget; label: string }[] = [
  { value: "classify", label: "Leave to the classifier" },
  ...EVENT_OPTIONS,
];
let resetCounter = 0;

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

  // Save shows the replay preview of the change first; only its confirm saves.
  const replay = useReplayGate<EventRulesConfig>({
    preview: (draft) => api().POST("/config/event-rules/preview", { body: draft }),
    save: async (draft, note) => {
      if (await editor.save(draft, note)) setBaseline(JSON.stringify(draft));
    },
    onInvalid: (draft) => void editor.validate(draft),
  });

  return (
    <div className="space-y-6">
      <ConfigHeader title="Event rules" domain="event-rules">
        <p>
          How much each kind of event matters. 0 is ignored, 1 goes in the digest, 2 is a digest
          highlight linked to the thesis, 3 is an instant alert once confirmed. The classifier only
          labels the event type; the severity always comes from here.
        </p>
        <p>Saving first replays the last 7 days under your draft and shows which alerts would change; confirm to save.</p>
      </ConfigHeader>
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

          <section aria-labelledby="triggers-heading" className="space-y-3 rounded-lg border border-border bg-surface-raised p-4">
            <h2 id="triggers-heading" className="text-lg font-semibold">
              Price moves and macro surprises
            </h2>
            <div className="grid gap-3 lg:grid-cols-[1fr_2fr]">
              <TextField
                label="Price move threshold (%)"
                inputMode="decimal"
                path="price_move_threshold_pct"
                errors={editor.errors}
                value={form.price_move_threshold_pct}
                onChange={(v) => set({ price_move_threshold_pct: v })}
                hint="A daily close-to-close move beyond ± this percentage queues an impact note (escalation) for the holding. Above 0, up to 100. Default 8."
              />
              <TextAreaField
                label="Macro surprise bands"
                path="macro_surprise_bands"
                errors={foldChildErrors(editor.errors, "macro_surprise_bands")}
                value={form.macro_surprise_bands}
                onChange={(v) => set({ macro_surprise_bands: v })}
                rows={9}
                hint='One "series band" per line, e.g. "hicp 0.3": a release moving more than the band against the previous one is a macro surprise, in the series&apos; own unit (0: any change, e.g. a policy rate). Evaluated only on release days. Series keys are set in Config: Jobs (Macro).'
              />
            </div>
          </section>

          <AnnouncementRules
            rules={form.announcement_types}
            errors={editor.errors}
            onChange={(announcement_types) => set({ announcement_types })}
          />

          <SaveBar
            dirty={dirty}
            busy={editor.busy ?? (replay.gate.kind === "previewing" ? "previewing" : null)}
            errors={editor.errors}
            message={replay.gate.kind === "previewing" ? "Replaying the last 7 days under your draft…" : editor.message}
            onValidate={() => body && void editor.validate(body)}
            onSave={(note) => body && void replay.request(body, note)}
            saveLabel="Preview and save"
          />
          <ReplayPreviewDialog gate={replay.gate} onConfirm={() => void replay.confirm()} onCancel={replay.cancel} />
        </>
      ) : null}
    </div>
  );
}

function AnnouncementRules({
  rules,
  errors,
  onChange,
}: {
  rules: AnnouncementRuleForm[];
  errors: FieldErrors;
  onChange: (rules: AnnouncementRuleForm[]) => void;
}) {
  const set = (key: string, change: Partial<AnnouncementRuleForm>) =>
    onChange(rules.map((r) => (r.key === key ? { ...r, ...change } : r)));
  const move = (index: number, by: -1 | 1) => {
    const next = [...rules];
    const [rule] = next.splice(index, 1);
    next.splice(index + by, 0, rule);
    onChange(next);
  };
  const own = errors.announcement_types ?? [];
  return (
    <section aria-labelledby="announcements-heading" className="space-y-3">
      <div>
        <h2 id="announcements-heading" className="text-lg font-semibold">
          Regulatory announcement types
        </h2>
        <p className="max-w-prose text-sm text-text-muted">
          How an RNS headline or an EQS category maps to an event type. Rules are checked top to bottom and the first
          match wins; the pattern is a case-insensitive regular expression. &ldquo;Leave to the classifier&rdquo; sends the
          item to the usual classification.
        </p>
      </div>
      {rules.length === 0 ? <p className="text-sm text-text-muted">No rules: every announcement goes to the classifier.</p> : null}
      <ol className="space-y-2">
        {rules.map((r, i) => {
          const path = `announcement_types.${i}`;
          return (
            <li key={r.key} className="grid items-start gap-2 rounded-lg border border-border bg-surface-raised p-3 sm:grid-cols-[auto_10rem_minmax(0,1fr)_14rem_auto]">
              <span className="mt-7 font-mono text-sm text-text-muted">{i + 1}.</span>
              <SelectField label="Service" hideLabel={i > 0} path={`${path}.service`} errors={errors} value={r.service} options={ANNOUNCEMENT_SERVICES} onChange={(v) => set(r.key, { service: v })} />
              <TextField
                label="Pattern"
                hideLabel={i > 0}
                path={`${path}.pattern`}
                errors={errors}
                value={r.pattern}
                spellCheck={false}
                maxLength={200}
                onChange={(v) => set(r.key, { pattern: v })}
              />
              <SelectField label="Event type" hideLabel={i > 0} path={`${path}.event_type`} errors={errors} value={r.event_type} options={TARGET_OPTIONS} onChange={(v) => set(r.key, { event_type: v })} />
              <div className={i === 0 ? "flex gap-1 sm:mt-6" : "flex gap-1"}>
                <Button variant="ghost" size="icon" aria-label={`Move rule ${i + 1} up`} disabled={i === 0} onClick={() => move(i, -1)}>
                  <ArrowUpIcon aria-hidden="true" />
                </Button>
                <Button variant="ghost" size="icon" aria-label={`Move rule ${i + 1} down`} disabled={i === rules.length - 1} onClick={() => move(i, 1)}>
                  <ArrowDownIcon aria-hidden="true" />
                </Button>
                <Button variant="ghost" size="icon" aria-label={`Remove rule ${i + 1}`} onClick={() => onChange(rules.filter((x) => x.key !== r.key))}>
                  <Trash2Icon aria-hidden="true" />
                </Button>
              </div>
            </li>
          );
        })}
      </ol>
      {own.length > 0 ? <p className="text-xs text-text">{own.join(" ")}</p> : null}
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => onChange([...rules, newAnnouncementRule()])}>
          <PlusIcon aria-hidden="true" />
          Add rule
        </Button>
        <Button
          variant="ghost"
          onClick={() => onChange(DEFAULT_ANNOUNCEMENT_RULES.map((r) => ({ ...r, key: `default-${++resetCounter}` })))}
        >
          <RotateCcwIcon aria-hidden="true" />
          Reset to the defaults
        </Button>
      </div>
    </section>
  );
}
