"use client";

import {
  ArrowDownIcon,
  ArrowUpIcon,
  CopyIcon,
  FlaskConicalIcon,
  LoaderCircleIcon,
  PlusIcon,
  StarIcon,
  Trash2Icon,
} from "lucide-react";
import Link from "next/link";
import { useId, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ConfigHeader } from "@/components/config/config-header";
import {
  CheckboxField,
  CheckboxGroup,
  SelectField,
  TextAreaField,
  TextField,
} from "@/components/config/fields";
import { SaveBar } from "@/components/config/save-bar";
import { Button } from "@/components/ui/button";
import {
  getBrowserApiClient,
  type ResearchProfilesConfig,
  type ResearchSectionId,
} from "@/lib/api/client";
import { errorsAt, type FieldErrors } from "@/lib/api/validation-errors";
import { EFFORTS, MODEL_SUGGESTIONS } from "@/lib/config/jobs-form";
import {
  AUTHORING_MODES,
  BOUNDS,
  duplicateProfile,
  duplicateTemplate,
  ENGINES,
  LANGUAGES,
  LENSES,
  MODEL_SECTION_OPTIONS,
  MODES,
  moveInList,
  newProfile,
  newTemplate,
  PRIORITIES,
  researchProfilesFromBody,
  researchProfilesToBody,
  SEARCH_MODES,
  STALE_KINDS,
  TIER_OPTIONS,
  toggleInList,
  validateResearchProfiles,
  WEB_EMPHASES,
  WORKER_EFFORTS,
  WRITE_ORDERS,
  type ProfileForm,
  type ResearchProfilesForm,
  type TemplateForm,
} from "@/lib/config/research-profiles-form";
import { mergeErrors } from "@/lib/config/strategy-form";
import {
  configSaveQuery,
  useConfigEditor,
} from "@/lib/hooks/use-config-editor";
import {
  DEPTHS,
  RESEARCH_SECTIONS,
  sectionLabel,
  VALUATION_METHODS,
  WRITTEN_BY,
} from "@/lib/research/report";
import { cn } from "@/lib/utils";
import { BlueprintCorners } from "@/components/ui/blueprint";

import { ReportTemplateCard } from "./report-template-card";

const api = () => getBrowserApiClient();
const EFFORT_OPTIONS = EFFORTS.map((e) => ({ value: e, label: e }));
const VALUATION_OPTIONS = VALUATION_METHODS.map((m) => ({
  value: m.id,
  label: m.label,
}));

/**
 * Research profiles: the shapes a research run can take. Every field of every profile is
 * editable (planning, source policy, iteration, authoring mode and report template too);
 * profiles can be added, duplicated and removed, and one is the default. Report templates
 * (sections, order, titles, owner-defined sections) are edited below the profiles. Checked
 * here against the backend's bounds before Save, and validated again by the backend.
 */
export function ResearchProfilesEditor() {
  const [form, setForm] = useState<ResearchProfilesForm | null>(null);
  const [baseline, setBaseline] = useState("");
  const editor = useConfigEditor<ResearchProfilesConfig>({
    load: () => api().GET("/config/research-profiles"),
    save: (body, note, base) =>
      api().PUT("/config/research-profiles", {
        body,
        params: { query: configSaveQuery(note, base) },
      }),
    validate: (body) =>
      api().POST("/config/research-profiles/validate", { body }),
    onLoaded: (body) => {
      const loaded = researchProfilesFromBody(body);
      setForm(loaded);
      setBaseline(body ? JSON.stringify(researchProfilesToBody(loaded)) : "");
    },
  });
  const body = useMemo(
    () => (form ? researchProfilesToBody(form) : null),
    [form],
  );
  const clientErrors = useMemo(
    () => (form ? validateResearchProfiles(form) : {}),
    [form],
  );
  const errors = useMemo(
    () => mergeErrors(clientErrors, editor.errors),
    [clientErrors, editor.errors],
  );
  const blocked = Object.keys(clientErrors).length > 0;
  const dirty = body !== null && JSON.stringify(body) !== baseline;

  const setProfile = (key: string, change: Partial<ProfileForm>) =>
    setForm((f) =>
      f
        ? {
            ...f,
            profiles: f.profiles.map((p) =>
              p.key === key ? { ...p, ...change } : p,
            ),
          }
        : f,
    );
  // Renaming a template's id keeps the profiles that use it pointing at it.
  const setTemplate = (key: string, change: Partial<TemplateForm>) =>
    setForm((f) => {
      if (!f) return f;
      const old = f.templates.find((t) => t.key === key);
      // A partial id that equals another template's id must not move profiles onto that
      // template (or, once typed past it, move that template's own profiles along).
      const others = new Set(
        f.templates.filter((t) => t.key !== key).map((t) => t.id),
      );
      const renamed =
        change.id !== undefined &&
        old &&
        old.id !== "" &&
        change.id !== "" &&
        old.id !== change.id &&
        !others.has(old.id) &&
        !others.has(change.id)
          ? { from: old.id, to: change.id }
          : null;
      return {
        ...f,
        templates: f.templates.map((t) =>
          t.key === key ? { ...t, ...change } : t,
        ),
        profiles: renamed
          ? f.profiles.map((p) =>
              p.template_id === renamed.from
                ? { ...p, template_id: renamed.to }
                : p,
            )
          : f.profiles,
      };
    });
  const ids = form?.profiles.map((p) => p.id) ?? [];

  async function save(note: string | null) {
    if (!body || blocked) return;
    if (await editor.save(body, note)) setBaseline(JSON.stringify(body));
  }

  return (
    <div className="space-y-6">
      <ConfigHeader title="Research profiles" domain="research-profiles">
        <p>
          The shapes a research run can take: which sections, in which order,
          how deep, which model and budget, which source tiers, trusted and
          blocked domains, peers, valuation methods and language, whether it
          searches again for thin sections, and which report template presents
          the result. A quick screen and a thorough workup are two profiles.
          Used from the next run.
        </p>
        <p>
          <Link
            href="/research"
            className="text-accent-ink underline-offset-4 hover:underline"
          >
            Start research
          </Link>{" "}
          with any profile.
        </p>
      </ConfigHeader>

      {editor.status.kind === "loading" ? (
        <p
          className="flex items-center gap-2 text-sm text-text-muted"
          role="status"
        >
          <LoaderCircleIcon
            aria-hidden="true"
            className="size-4 animate-spin"
          />
          Loading research profiles…
        </p>
      ) : null}
      {editor.status.kind === "error" ? (
        <AlertMessage title="Could not load the research profiles">
          {editor.status.message}
        </AlertMessage>
      ) : null}
      {editor.status.kind === "ready" && editor.status.version === null ? (
        <AlertMessage tone="info" title="Defaults in use">
          Nothing saved yet: the profiles and report templates below are the
          backend&rsquo;s defaults.
        </AlertMessage>
      ) : null}

      {form ? (
        <>
          <section
            aria-labelledby="default-profile-heading"
            className="space-y-2 blueprint p-5"
          >
            <BlueprintCorners />
            <h2 id="default-profile-heading" className="font-medium">
              Default profile
            </h2>
            <SelectField
              className="max-w-sm"
              label="Used when a run names no profile"
              path="default_profile"
              errors={errors}
              value={form.default_profile}
              options={[
                ...form.profiles.map((p) => ({
                  value: p.id,
                  label: `${p.name || "(no name)"} (${p.id || "no id"})`,
                })),
                ...(form.profiles.some((p) => p.id === form.default_profile)
                  ? []
                  : [
                      {
                        value: form.default_profile,
                        label: `${form.default_profile} (not a profile)`,
                      },
                    ]),
              ]}
              onChange={(v) =>
                setForm((f) => (f ? { ...f, default_profile: v } : f))
              }
            />
          </section>

          {errorsAt(errors, "profiles").length > 0 ? (
            <AlertMessage title="Profiles">
              {errorsAt(errors, "profiles").join(" ")}
            </AlertMessage>
          ) : null}

          {form.profiles.map((p, i) => (
            <ProfileCard
              key={p.key}
              profile={p}
              index={i}
              errors={errors}
              isDefault={p.id === form.default_profile}
              templates={form.templates}
              onChange={(change) => {
                // Renaming the default profile's id keeps it the default.
                if (change.id !== undefined && p.id === form.default_profile) {
                  setForm((f) =>
                    f
                      ? {
                          ...f,
                          default_profile: change.id!,
                          profiles: f.profiles.map((x) =>
                            x.key === p.key ? { ...x, ...change } : x,
                          ),
                        }
                      : f,
                  );
                } else setProfile(p.key, change);
              }}
              onMakeDefault={() =>
                setForm((f) => (f ? { ...f, default_profile: p.id } : f))
              }
              onDuplicate={() =>
                setForm((f) => {
                  if (!f) return f;
                  const copy = duplicateProfile(
                    p,
                    f.profiles.map((x) => x.id),
                  );
                  const at = f.profiles.findIndex((x) => x.key === p.key);
                  return {
                    ...f,
                    profiles: [
                      ...f.profiles.slice(0, at + 1),
                      copy,
                      ...f.profiles.slice(at + 1),
                    ],
                  };
                })
              }
              onRemove={
                form.profiles.length > 1
                  ? () =>
                      setForm((f) =>
                        f
                          ? {
                              ...f,
                              profiles: f.profiles.filter(
                                (x) => x.key !== p.key,
                              ),
                            }
                          : f,
                      )
                  : undefined
              }
            />
          ))}

          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              setForm((f) =>
                f ? { ...f, profiles: [...f.profiles, newProfile(ids)] } : f,
              )
            }
          >
            <PlusIcon aria-hidden="true" />
            Add profile
          </Button>

          <section aria-labelledby="templates-heading" className="space-y-4">
            <div className="space-y-1">
              <h2
                id="templates-heading"
                className="text-[22px] leading-tight font-semibold"
              >
                Report templates
              </h2>
              <p className="text-sm text-text-muted">
                A profile decides how a run investigates; a template decides how
                the report is laid out: which sections appear, in what order,
                under what title, and which are left out of the concise view. A
                template only arranges the report. It cannot change a figure
                that code calculated or your strategy. Pick one per profile
                above.
              </p>
            </div>
            {errorsAt(errors, "templates").length > 0 ? (
              <AlertMessage title="Templates">
                {errorsAt(errors, "templates").join(" ")}
              </AlertMessage>
            ) : null}
            {form.templates.map((t, i) => (
              <ReportTemplateCard
                key={t.key}
                template={t}
                index={i}
                errors={errors}
                usedBy={form.profiles
                  .filter(
                    (p) =>
                      p.template_id.trim() !== "" && p.template_id === t.id,
                  )
                  .map((p) => p.name || p.id)}
                onChange={(change) => setTemplate(t.key, change)}
                onDuplicate={() =>
                  setForm((f) => {
                    if (!f) return f;
                    const copy = duplicateTemplate(
                      t,
                      f.templates.map((x) => x.id),
                    );
                    const at = f.templates.findIndex((x) => x.key === t.key);
                    return {
                      ...f,
                      templates: [
                        ...f.templates.slice(0, at + 1),
                        copy,
                        ...f.templates.slice(at + 1),
                      ],
                    };
                  })
                }
                onRemove={() =>
                  setForm((f) =>
                    f
                      ? {
                          ...f,
                          templates: f.templates.filter((x) => x.key !== t.key),
                        }
                      : f,
                  )
                }
              />
            ))}
            {form.templates.length === 0 ? (
              <p className="text-sm text-text-muted">
                No template yet. Profiles then use their own section list.
              </p>
            ) : null}
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                setForm((f) =>
                  f
                    ? {
                        ...f,
                        templates: [
                          ...f.templates,
                          newTemplate(f.templates.map((x) => x.id)),
                        ],
                      }
                    : f,
                )
              }
            >
              <PlusIcon aria-hidden="true" />
              Add template
            </Button>
          </section>

          <SaveBar
            dirty={dirty}
            savedVersionId={editor.version?.id ?? null}
            busy={editor.busy}
            blocked={blocked}
            errors={errors}
            message={editor.message}
            onValidate={() => body && void editor.validate(body)}
            onSave={(note) => void save(note)}
          />
        </>
      ) : null}
    </div>
  );
}

function ProfileCard({
  profile: p,
  index: i,
  errors,
  isDefault,
  templates,
  onChange,
  onMakeDefault,
  onDuplicate,
  onRemove,
}: {
  profile: ProfileForm;
  index: number;
  errors: FieldErrors;
  isDefault: boolean;
  templates: readonly TemplateForm[];
  onChange: (change: Partial<ProfileForm>) => void;
  onMakeDefault: () => void;
  onDuplicate: () => void;
  onRemove?: () => void;
}) {
  const headingId = useId();
  const listId = useId();
  const path = `profiles.${i}`;
  const own = errorsAt(errors, path);
  const label = p.name || p.id || `profile ${i + 1}`;
  const templateOptions = [
    { value: "", label: "None (the profile's own section list)" },
    ...templates.map((t) => ({
      value: t.id,
      label: `${t.name || "(no name)"} (${t.id || "no id"})`,
    })),
    ...(p.template_id && !templates.some((t) => t.id === p.template_id)
      ? [{ value: p.template_id, label: `${p.template_id} (not a template)` }]
      : []),
  ];
  return (
    <section aria-labelledby={headingId} className="space-y-4 blueprint p-5">
      <BlueprintCorners />
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h2
          id={headingId}
          className="flex flex-wrap items-center gap-2 text-[22px] leading-tight font-semibold"
        >
          <FlaskConicalIcon
            aria-hidden="true"
            className="size-4 text-text-muted"
          />
          {p.name || "(no name)"}{" "}
          <span className="font-mono text-sm font-normal text-text-muted">
            {p.id}
          </span>
          {isDefault ? (
            <span className="inline-flex items-center gap-1 rounded-sm border border-border px-1.5 py-0.5 text-xs font-medium">
              <StarIcon aria-hidden="true" className="size-3" />
              Default
            </span>
          ) : null}
        </h2>
        <div className="flex flex-wrap gap-1">
          {!isDefault ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={onMakeDefault}
              aria-label={`Make ${label} the default profile`}
            >
              <StarIcon aria-hidden="true" />
              Make default
            </Button>
          ) : null}
          <Button
            variant="ghost"
            size="sm"
            onClick={onDuplicate}
            aria-label={`Duplicate ${label}`}
          >
            <CopyIcon aria-hidden="true" />
            Duplicate
          </Button>
          {onRemove ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={onRemove}
              aria-label={`Remove ${label}`}
              disabled={isDefault}
              title={
                isDefault ? "Make another profile the default first" : undefined
              }
            >
              <Trash2Icon aria-hidden="true" />
              Remove
            </Button>
          ) : null}
        </div>
      </div>
      {own.length > 0 ? (
        <p className="text-xs text-text">{own.join(" ")}</p>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <TextField
          label="Id"
          path={`${path}.id`}
          errors={errors}
          value={p.id}
          maxLength={BOUNDS.id.max}
          spellCheck={false}
          onChange={(v) => onChange({ id: v })}
          hint="Lower-case words joined by dashes; used in the job and the report."
        />
        <TextField
          label="Name"
          path={`${path}.name`}
          errors={errors}
          value={p.name}
          maxLength={BOUNDS.name.max}
          onChange={(v) => onChange({ name: v })}
        />
        <SelectField
          label="Language"
          path={`${path}.language`}
          errors={errors}
          value={p.language}
          options={LANGUAGES}
          onChange={(v) => onChange({ language: v })}
        />
        <SelectField
          label="Depth"
          path={`${path}.depth`}
          errors={errors}
          value={p.depth}
          options={DEPTHS}
          onChange={(v) => onChange({ depth: v })}
        />
        <TextField
          label="Model"
          path={`${path}.model`}
          errors={errors}
          value={p.model}
          list={listId}
          spellCheck={false}
          onChange={(v) => onChange({ model: v })}
        />
        <datalist id={listId}>
          {MODEL_SUGGESTIONS.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
        <SelectField
          label="Effort"
          path={`${path}.effort`}
          errors={errors}
          value={p.effort}
          options={EFFORT_OPTIONS}
          onChange={(v) => onChange({ effort: v })}
        />
        <TextField
          label="Step cap per run"
          path={`${path}.max_steps`}
          errors={errors}
          value={p.max_steps}
          inputMode="numeric"
          onChange={(v) => onChange({ max_steps: v })}
          hint={`Model calls plus web searches. ${BOUNDS.max_steps.min} to ${BOUNDS.max_steps.max}.`}
        />
        <TextField
          label="Web searches per run"
          path={`${path}.max_searches`}
          errors={errors}
          value={p.max_searches}
          inputMode="numeric"
          onChange={(v) => onChange({ max_searches: v })}
          hint={`${BOUNDS.max_searches.min} to ${BOUNDS.max_searches.max}.`}
        />
        <TextField
          label="Largest 13F holders named"
          path={`${path}.top_holders`}
          errors={errors}
          value={p.top_holders}
          inputMode="numeric"
          onChange={(v) => onChange({ top_holders: v })}
          hint={`In the ownership section. ${BOUNDS.top_holders.min} to ${BOUNDS.top_holders.max}.`}
        />
        <CheckboxField
          label="Compare with peers"
          checked={p.include_peers}
          onChange={(v) => onChange({ include_peers: v })}
          hint="Name the holding's competitors (from holding profiles) and compare with them."
        />
      </div>

      <SectionPicker
        profile={p}
        path={`${path}.sections`}
        errors={errors}
        onChange={(sections) => onChange({ sections })}
      />

      <div className="grid gap-4 sm:grid-cols-2">
        <CheckboxGroup
          label="Source tiers web search may use and the report may cite"
          path={`${path}.source_tiers`}
          errors={errors}
          values={p.source_tiers}
          options={TIER_OPTIONS}
          onChange={(v) => onChange({ source_tiers: v })}
        />
        <CheckboxGroup
          label="Valuation methods (computed in code)"
          path={`${path}.valuation_methods`}
          errors={errors}
          values={p.valuation_methods}
          options={VALUATION_OPTIONS}
          onChange={(v) => onChange({ valuation_methods: v })}
          hint="None: the valuation section states only the price."
        />
      </div>

      <fieldset className="space-y-3">
        <legend className="text-sm font-medium">Planning and report</legend>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <SelectField
            label="Mode"
            path={`${path}.mode`}
            errors={errors}
            value={p.mode}
            options={MODES}
            onChange={(v) => onChange({ mode: v })}
            hint="The kind of run; it shapes the research plan (questions, tools, searches)."
          />
          <SelectField
            label="Web emphasis"
            path={`${path}.web_emphasis`}
            errors={errors}
            value={p.web_emphasis}
            options={WEB_EMPHASES}
            onChange={(v) => onChange({ web_emphasis: v })}
          />
          <TextField
            label="Date horizon (days)"
            path={`${path}.date_horizon_days`}
            errors={errors}
            value={p.date_horizon_days}
            inputMode="numeric"
            placeholder="No horizon"
            onChange={(v) => onChange({ date_horizon_days: v })}
            hint={`Prefer evidence from this many days back; older sources are named as old. Blank, or ${BOUNDS.date_horizon_days.min} to ${BOUNDS.date_horizon_days.max}.`}
          />
          <SelectField
            label="Report template"
            path={`${path}.template_id`}
            errors={errors}
            value={p.template_id}
            options={templateOptions}
            onChange={(v) => onChange({ template_id: v })}
            hint="Decides which sections appear, in what order, under what titles. Edited under Report templates."
          />
          <SelectField
            label="Authoring mode"
            path={`${path}.authoring`}
            errors={errors}
            value={p.authoring}
            options={AUTHORING_MODES}
            onChange={(v) => onChange({ authoring: v })}
            hint="None of these changes your active thesis; you accept a proposal field by field."
          />
          <TextField
            label="Cited sentences for a section to count as answered"
            path={`${path}.min_sentences`}
            errors={errors}
            value={p.min_sentences}
            inputMode="numeric"
            onChange={(v) => onChange({ min_sentences: v })}
            hint={`${BOUNDS.min_sentences.min} to ${BOUNDS.min_sentences.max}.`}
          />
        </div>
        <div className="grid items-start gap-3 sm:grid-cols-2">
          <CheckboxField
            label="Search again for thin sections"
            checked={p.iterate}
            onChange={(v) => onChange({ iterate: v })}
            hint="After the first pass, search for sections left thin, corroborate single-source claims, and stop when the sections have enough or the budget is used."
          />
          <TextField
            label="Follow-up rounds"
            path={`${path}.max_rounds`}
            errors={errors}
            value={p.max_rounds}
            inputMode="numeric"
            onChange={(v) => onChange({ max_rounds: v })}
            hint={`Used only when searching again. ${BOUNDS.max_rounds.min} to ${BOUNDS.max_rounds.max}.`}
          />
        </div>
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="text-sm font-medium">Research workers</legend>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <SelectField
            label="Engine"
            path={`${path}.engine`}
            errors={errors}
            value={p.engine}
            options={ENGINES}
            onChange={(v) => onChange({ engine: v })}
            hint="Agentic: one cheap-model worker per lens reads the app's records and searches the web within this profile's step and search budget. The report is still written and checked section by section. Without an AI key the fixed pipeline runs."
          />
          {p.engine === "agentic" ? (
            <>
              <TextField
                label="Worker model"
                path={`${path}.worker_model`}
                errors={errors}
                value={p.worker_model}
                list={listId}
                spellCheck={false}
                onChange={(v) => onChange({ worker_model: v })}
              />
              <SelectField
                label="Worker effort"
                path={`${path}.worker_effort`}
                errors={errors}
                value={p.worker_effort}
                options={WORKER_EFFORTS}
                onChange={(v) => onChange({ worker_effort: v })}
              />
              <TextField
                label="Steps per worker"
                path={`${path}.worker_max_steps`}
                errors={errors}
                value={p.worker_max_steps}
                inputMode="numeric"
                onChange={(v) => onChange({ worker_max_steps: v })}
                hint={`At most this many model calls for one worker; steps a worker leaves unused go to the next. ${BOUNDS.worker_max_steps.min} to ${BOUNDS.worker_max_steps.max}.`}
              />
              <TextField
                label="Web searches per worker"
                path={`${path}.worker_max_searches`}
                errors={errors}
                value={p.worker_max_searches}
                inputMode="numeric"
                onChange={(v) => onChange({ worker_max_searches: v })}
                hint={`Only the news and counter-case lenses search. All workers together stay inside this profile's searches. ${BOUNDS.worker_max_searches.min} to ${BOUNDS.worker_max_searches.max}.`}
              />
              <TextField
                label="Workers at the same time"
                path={`${path}.parallel_workers`}
                errors={errors}
                value={p.parallel_workers}
                inputMode="numeric"
                onChange={(v) => onChange({ parallel_workers: v })}
                hint={`${BOUNDS.parallel_workers.min} to ${BOUNDS.parallel_workers.max}. The first worker always runs alone.`}
              />
            </>
          ) : null}
        </div>
        {p.engine === "agentic" ? (
          <CheckboxGroup
            label="Lenses (one worker each)"
            path={`${path}.lenses`}
            errors={errors}
            values={p.lenses}
            options={LENSES}
            onChange={(v) => onChange({ lenses: v })}
            hint="Findings without a checkable source are dropped before the writer sees them."
          />
        ) : null}
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="text-sm font-medium">Cost of a run</legend>
        <p className="text-xs text-text-muted">
          None of these lowers a search, step, section or sentence limit. Turn
          one on only after comparing a report written with and without it.
        </p>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <SelectField
            label="Order sections are written in"
            path={`${path}.write_order`}
            errors={errors}
            value={p.write_order}
            options={WRITE_ORDERS}
            onChange={(v) => onChange({ write_order: v })}
            hint="The report reads the same either way. Writing the summary last lets it read the finished sections instead of every source."
          />
          <SelectField
            label="Who runs the web searches"
            path={`${path}.search_mode`}
            errors={errors}
            value={p.search_mode}
            options={SEARCH_MODES}
            onChange={(v) => onChange({ search_mode: v })}
            hint="The number of searches and the allowed outlets stay as set above. A failed scout leaves the writing model to search itself."
          />
          <SelectField
            label="Speed against price"
            path={`${path}.priority`}
            errors={errors}
            value={p.priority}
            options={PRIORITIES}
            onChange={(v) => onChange({ priority: v })}
            hint="For runs nobody is waiting on. A busy cheaper tier falls back to the standard one."
          />
          {p.search_mode === "scouts" ? (
            <>
              <TextField
                label="Scout model"
                path={`${path}.scout_model`}
                errors={errors}
                value={p.scout_model}
                list={listId}
                spellCheck={false}
                onChange={(v) => onChange({ scout_model: v })}
              />
              <SelectField
                label="Scout effort"
                path={`${path}.scout_effort`}
                errors={errors}
                value={p.scout_effort}
                options={WORKER_EFFORTS}
                onChange={(v) => onChange({ scout_effort: v })}
              />
            </>
          ) : null}
          <TextField
            label="Cheaper model"
            path={`${path}.cheap_model`}
            errors={errors}
            value={p.cheap_model}
            list={listId}
            spellCheck={false}
            onChange={(v) => onChange({ cheap_model: v })}
            hint="Writes the sections ticked below and, if chosen, the follow-ups."
          />
          <SelectField
            label="Cheaper model effort"
            path={`${path}.cheap_effort`}
            errors={errors}
            value={p.cheap_effort}
            options={WORKER_EFFORTS}
            onChange={(v) => onChange({ cheap_effort: v })}
          />
          <CheckboxField
            label="Follow-ups by the cheaper model"
            checked={p.follow_up_cheap}
            onChange={(v) => onChange({ follow_up_cheap: v })}
            hint="A follow-up that adds nothing is tried once more by the main model."
          />
        </div>
        <CheckboxGroup
          label="Sections the cheaper model writes first"
          path={`${path}.cheap_sections`}
          errors={errors}
          values={p.cheap_sections}
          options={MODEL_SECTION_OPTIONS}
          onChange={(v) => onChange({ cheap_sections: v })}
          hint="A draft with fewer cited sentences than the minimum above is rewritten by the main model."
        />
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="text-sm font-medium">Source policy</legend>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <TextAreaField
            label="Trusted domains"
            path={`${path}.allow_domains`}
            errors={errors}
            value={p.allow_domains}
            rows={4}
            onChange={(v) => onChange({ allow_domains: v })}
            hint={`One per line, e.g. example.com. Searched in addition to the tier lists, labelled owner-trusted, never counted as proof. Up to ${BOUNDS.allow_domains.max}.`}
          />
          <TextAreaField
            label="Blocked domains"
            path={`${path}.deny_domains`}
            errors={errors}
            value={p.deny_domains}
            rows={4}
            onChange={(v) => onChange({ deny_domains: v })}
            hint={`One per line. Never searched, never cited. Up to ${BOUNDS.deny_domains.max}.`}
          />
          <div className="space-y-3">
            <CheckboxField
              label="Allow forums and social media"
              checked={p.allow_forums}
              onChange={(v) => onChange({ allow_forums: v })}
              hint="Their sentences are labelled as forum."
            />
            <TextAreaField
              label="Extra forum domains"
              path={`${path}.forum_domains`}
              errors={errors}
              value={p.forum_domains}
              rows={3}
              onChange={(v) => onChange({ forum_domains: v })}
              hint={`One per line, added to the built-in list; searched only when forums are allowed. Up to ${BOUNDS.forum_domains.max}.`}
            />
          </div>
        </div>
        <fieldset className="space-y-1">
          <legend className="text-sm font-medium">
            Days before a cited source counts as stale
          </legend>
          <p className="text-xs text-text-muted">
            Leave blank to keep the built-in limit shown in each box.
          </p>
          <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
            {STALE_KINDS.map((k) => (
              <TextField
                key={k.kind}
                label={k.label}
                path={`${path}.stale_days.${k.kind}`}
                errors={errors}
                value={p.stale_days[k.kind] ?? ""}
                inputMode="numeric"
                placeholder={String(k.builtin)}
                onChange={(v) =>
                  onChange({ stale_days: { ...p.stale_days, [k.kind]: v } })
                }
              />
            ))}
          </div>
          {errorsAt(errors, `${path}.stale_days`).length > 0 ? (
            <p className="text-xs text-text">
              {errorsAt(errors, `${path}.stale_days`).join(" ")}
            </p>
          ) : null}
        </fieldset>
      </fieldset>
    </section>
  );
}

/**
 * Ordered multi-select: tick a section to add it at the end; move selected sections up or
 * down. The report follows this order.
 */
function SectionPicker({
  profile,
  path,
  errors,
  onChange,
}: {
  profile: ProfileForm;
  path: string;
  errors: FieldErrors;
  onChange: (sections: ResearchSectionId[]) => void;
}) {
  const id = useId();
  const messages = [
    ...errorsAt(errors, path),
    ...Object.entries(errors)
      .filter(([p]) => p.startsWith(`${path}.`))
      .flatMap(([, ms]) => ms),
  ];
  const selected = profile.sections;
  const unselected = RESEARCH_SECTIONS.filter((s) => !selected.includes(s.id));
  return (
    <fieldset
      className="space-y-2"
      aria-describedby={messages.length > 0 ? `${id}-errors` : undefined}
    >
      <legend className="text-sm font-medium">Sections, in report order</legend>
      <ol className="space-y-1">
        {selected.map((sectionId, i) => {
          const info = RESEARCH_SECTIONS.find((s) => s.id === sectionId);
          return (
            <li
              key={sectionId}
              className="flex flex-wrap items-center gap-2 rounded-md border border-border px-2 py-1 text-sm"
            >
              <label className="inline-flex flex-1 items-center gap-2">
                <input
                  type="checkbox"
                  checked
                  onChange={() => onChange(toggleInList(selected, sectionId))}
                  className="size-4 accent-accent"
                />
                <span className="num w-5 text-text-muted">{i + 1}.</span>
                <span className="font-medium">{sectionLabel(sectionId)}</span>
                {info ? (
                  <span className="text-xs text-text-muted">
                    {WRITTEN_BY[info.writtenBy].label} · {info.description}
                  </span>
                ) : null}
              </label>
              <span className="flex gap-0.5">
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7"
                  disabled={i === 0}
                  aria-label={`Move ${sectionLabel(sectionId)} up`}
                  onClick={() => onChange(moveInList(selected, sectionId, -1))}
                >
                  <ArrowUpIcon aria-hidden="true" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7"
                  disabled={i === selected.length - 1}
                  aria-label={`Move ${sectionLabel(sectionId)} down`}
                  onClick={() => onChange(moveInList(selected, sectionId, 1))}
                >
                  <ArrowDownIcon aria-hidden="true" />
                </Button>
              </span>
            </li>
          );
        })}
      </ol>
      {selected.length === 0 ? (
        <p className="text-sm text-text-muted">No section picked.</p>
      ) : null}
      {unselected.length > 0 ? (
        <div className="space-y-1">
          <p className="text-xs text-text-muted">
            Not in this profile (tick to add at the end):
          </p>
          <ul className="flex flex-wrap gap-x-4 gap-y-1">
            {unselected.map((s) => (
              <li key={s.id}>
                <label
                  className={cn(
                    "inline-flex items-center gap-1.5 text-sm text-text-muted",
                  )}
                  title={s.description}
                >
                  <input
                    type="checkbox"
                    checked={false}
                    onChange={() => onChange(toggleInList(selected, s.id))}
                    className="size-4 accent-accent"
                  />
                  {s.label}
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
