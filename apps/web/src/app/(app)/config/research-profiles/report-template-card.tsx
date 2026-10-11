"use client";

import { ArrowDownIcon, ArrowUpIcon, CopyIcon, FileTextIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { useId } from "react";

import { SelectField, TextAreaField, TextField } from "@/components/config/fields";
import { Button } from "@/components/ui/button";
import { errorsAt, type FieldErrors } from "@/lib/api/validation-errors";
import {
  addBuiltinSection,
  addCustomSection,
  BOUNDS,
  DEFAULT_VIEWS,
  moveTemplateSection,
  removeTemplateSection,
  unusedBuiltinSections,
  type TemplateForm,
  type TemplateSectionForm,
} from "@/lib/config/research-profiles-form";
import { RESEARCH_SECTIONS, sectionLabel } from "@/lib/research/report";
import { BlueprintCorners } from "@/components/ui/blueprint";

/**
 * One report template: which sections appear, in what order, under what title, whether the
 * concise view skips them, plus up to six sections the owner defines (a title and a question
 * in plain words). A template only arranges the report; it cannot change a code-written figure.
 */
export function ReportTemplateCard({
  template: t,
  index: i,
  errors,
  usedBy,
  onChange,
  onDuplicate,
  onRemove,
}: {
  template: TemplateForm;
  index: number;
  errors: FieldErrors;
  /** Names of the profiles that pick this template. */
  usedBy: string[];
  onChange: (change: Partial<TemplateForm>) => void;
  onDuplicate: () => void;
  onRemove: () => void;
}) {
  const headingId = useId();
  const path = `templates.${i}`;
  const label = t.name || t.id || `template ${i + 1}`;
  const own = errorsAt(errors, path);
  const used = usedBy.length > 0;

  const setSection = (key: string, change: Partial<TemplateSectionForm>) =>
    onChange({ sections: t.sections.map((s) => (s.key === key ? { ...s, ...change } : s)) });
  const setCustom = (id: string, change: { title?: string; task?: string }) =>
    onChange({ custom_sections: t.custom_sections.map((c) => (c.id === id ? { ...c, ...change } : c)) });
  const apply = (next: TemplateForm) => onChange({ sections: next.sections, custom_sections: next.custom_sections });

  return (
    <section aria-labelledby={headingId} className="space-y-4 blueprint p-5">
      <BlueprintCorners />
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 id={headingId} className="flex flex-wrap items-center gap-2 text-[22px] leading-tight font-semibold">
          <FileTextIcon aria-hidden="true" className="size-4 text-text-muted" />
          {t.name || "(no name)"} <span className="font-mono text-sm font-normal text-text-muted">{t.id}</span>
        </h3>
        <div className="flex flex-wrap gap-1">
          <Button variant="ghost" size="sm" onClick={onDuplicate} aria-label={`Duplicate template ${label}`}>
            <CopyIcon aria-hidden="true" />
            Duplicate
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={onRemove}
            aria-label={`Remove template ${label}`}
            disabled={used}
            title={used ? `Used by ${usedBy.join(", ")}: pick another template there first` : undefined}
          >
            <Trash2Icon aria-hidden="true" />
            Remove
          </Button>
        </div>
      </div>
      {own.length > 0 ? <p className="text-xs text-text">{own.join(" ")}</p> : null}
      <p className="text-xs text-text-muted">{used ? `Used by: ${usedBy.join(", ")}.` : "No profile uses this template yet."}</p>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <TextField
          label="Template id"
          path={`${path}.id`}
          errors={errors}
          value={t.id}
          maxLength={BOUNDS.id.max}
          spellCheck={false}
          onChange={(v) => onChange({ id: v })}
          hint="Lower-case words joined by dashes; profiles pick a template by this id."
        />
        <TextField label="Template name" path={`${path}.name`} errors={errors} value={t.name} maxLength={BOUNDS.name.max} onChange={(v) => onChange({ name: v })} />
        <SelectField
          label="View shown first"
          path={`${path}.default_view`}
          errors={errors}
          value={t.default_view}
          options={DEFAULT_VIEWS}
          onChange={(v) => onChange({ default_view: v })}
          hint="The concise view skips sections marked detailed-view only."
        />
      </div>

      <TemplateSections
        template={t}
        path={path}
        errors={errors}
        onSection={setSection}
        onCustom={setCustom}
        onApply={apply}
        onReorder={(next) => onChange({ sections: next.sections })}
      />
    </section>
  );
}

function TemplateSections({
  template: t,
  path,
  errors,
  onSection,
  onCustom,
  onApply,
  onReorder,
}: {
  template: TemplateForm;
  path: string;
  errors: FieldErrors;
  onSection: (key: string, change: Partial<TemplateSectionForm>) => void;
  onCustom: (id: string, change: { title?: string; task?: string }) => void;
  onApply: (next: TemplateForm) => void;
  onReorder: (next: TemplateForm) => void;
}) {
  const id = useId();
  const messages = [...errorsAt(errors, `${path}.sections`), ...errorsAt(errors, `${path}.custom_sections`)];
  const unused = unusedBuiltinSections(t);
  const customCount = t.custom_sections.length;
  const atCustomLimit = customCount >= BOUNDS.custom_sections.max;

  return (
    <fieldset className="space-y-2" aria-describedby={messages.length > 0 ? `${id}-errors` : undefined}>
      <legend className="text-sm font-medium">Sections, in report order</legend>
      <ol className="space-y-2">
        {t.sections.map((s, j) => {
          const custom = t.custom_sections.find((c) => c.id === s.id);
          const customIndex = custom ? t.custom_sections.indexOf(custom) : -1;
          const name = custom ? custom.title.trim() || "Your section" : sectionLabel(s.id);
          const info = RESEARCH_SECTIONS.find((r) => r.id === s.id);
          return (
            <li key={s.key} className="space-y-2 rounded-md border border-border px-2 py-2 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <label className="inline-flex flex-1 items-center gap-2">
                  <input
                    type="checkbox"
                    checked={s.enabled}
                    onChange={(e) => onSection(s.key, { enabled: e.target.checked })}
                    className="size-4 accent-accent"
                  />
                  <span className="num w-5 text-text-muted">{j + 1}.</span>
                  <span className="font-medium">{name}</span>
                  <span className="sr-only">: include in the report</span>
                  <span className="text-xs text-text-muted">
                    {custom ? "Your section, written by the model" : info ? info.description : null}
                  </span>
                </label>
                <span className="flex gap-0.5">
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7"
                    disabled={j === 0}
                    aria-label={`Move ${name} up`}
                    onClick={() => onReorder(moveTemplateSection(t, s.id, -1))}
                  >
                    <ArrowUpIcon aria-hidden="true" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7"
                    disabled={j === t.sections.length - 1}
                    aria-label={`Move ${name} down`}
                    onClick={() => onReorder(moveTemplateSection(t, s.id, 1))}
                  >
                    <ArrowDownIcon aria-hidden="true" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7"
                    aria-label={`Remove ${name} from this template`}
                    onClick={() => onApply(removeTemplateSection(t, s.id))}
                  >
                    <Trash2Icon aria-hidden="true" />
                  </Button>
                </span>
              </div>
              <div className="grid items-start gap-3 sm:grid-cols-3">
                <TextField
                  label={`Title shown for ${name}`}
                  hideLabel
                  placeholder={custom ? "Title from your section below" : `Title: ${sectionLabel(s.id)}`}
                  path={`${path}.sections.${j}.title`}
                  errors={errors}
                  value={s.title}
                  maxLength={BOUNDS.section_title.max}
                  onChange={(v) => onSection(s.key, { title: v })}
                />
                <TextField
                  label={`Sentence cap for ${name}`}
                  hideLabel
                  placeholder="Sentences: the profile's depth"
                  path={`${path}.sections.${j}.max_sentences`}
                  errors={errors}
                  value={s.max_sentences}
                  inputMode="numeric"
                  onChange={(v) => onSection(s.key, { max_sentences: v })}
                  hint={`Blank, or ${BOUNDS.section_sentences.min} to ${BOUNDS.section_sentences.max}.`}
                />
                <label className="inline-flex items-center gap-1.5 pt-1.5 text-sm">
                  <input
                    type="checkbox"
                    checked={s.detail_only}
                    onChange={(e) => onSection(s.key, { detail_only: e.target.checked })}
                    className="size-4 accent-accent"
                  />
                  Detailed view only
                  <span className="sr-only"> for {name}</span>
                </label>
              </div>
              {custom ? (
                <div className="space-y-3 border-t border-border pt-2">
                  <TextField
                    label={`Your section ${customIndex + 1}: title`}
                    path={`${path}.custom_sections.${customIndex}.title`}
                    errors={errors}
                    value={custom.title}
                    maxLength={BOUNDS.section_title.max}
                    onChange={(v) => onCustom(custom.id, { title: v })}
                  />
                  <TextAreaField
                    label={`Your section ${customIndex + 1}: the question it answers`}
                    path={`${path}.custom_sections.${customIndex}.task`}
                    errors={errors}
                    value={custom.task}
                    rows={3}
                    onChange={(v) => onCustom(custom.id, { task: v })}
                    hint={`In plain words, ${BOUNDS.custom_task.min} to ${BOUNDS.custom_task.max} characters. No links or markup. Written by the model from the same sources and cited like any other section.`}
                  />
                </div>
              ) : null}
            </li>
          );
        })}
      </ol>
      {t.sections.length === 0 ? <p className="text-sm text-text-muted">No section in this template.</p> : null}

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="outline" size="sm" disabled={atCustomLimit} onClick={() => onApply(addCustomSection(t))}>
          <PlusIcon aria-hidden="true" />
          Add a section of your own
        </Button>
        <span className="text-xs text-text-muted">
          {customCount} of {BOUNDS.custom_sections.max} of your own
        </span>
      </div>

      {unused.length > 0 ? (
        <div className="space-y-1">
          <p className="text-xs text-text-muted">Not in this template (tick to add at the end):</p>
          <ul className="flex flex-wrap gap-x-4 gap-y-1">
            {unused.map((sid) => (
              <li key={sid}>
                <label className="inline-flex items-center gap-1.5 text-sm text-text-muted">
                  <input type="checkbox" checked={false} onChange={() => onApply(addBuiltinSection(t, sid))} className="size-4 accent-accent" />
                  {sectionLabel(sid)}
                </label>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {messages.length > 0 ? (
        <ul id={`${id}-errors`} className="space-y-0.5 text-xs text-text">
          {messages.map((m) => (
            <li key={m}>{m}</li>
          ))}
        </ul>
      ) : null}
    </fieldset>
  );
}
