"use client";

import { ArrowDownIcon, ArrowUpIcon, CopyIcon, FlaskConicalIcon, LoaderCircleIcon, PlusIcon, StarIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import { useId, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ConfigHeader } from "@/components/config/config-header";
import { CheckboxField, CheckboxGroup, SelectField, TextField } from "@/components/config/fields";
import { SaveBar } from "@/components/config/save-bar";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type ResearchProfilesConfig, type ResearchSectionId } from "@/lib/api/client";
import { errorsAt, type FieldErrors } from "@/lib/api/validation-errors";
import { EFFORTS, MODEL_SUGGESTIONS } from "@/lib/config/jobs-form";
import {
  BOUNDS,
  duplicateProfile,
  LANGUAGES,
  moveInList,
  newProfile,
  researchProfilesFromBody,
  researchProfilesToBody,
  TIER_OPTIONS,
  toggleInList,
  validateResearchProfiles,
  type ProfileForm,
  type ResearchProfilesForm,
} from "@/lib/config/research-profiles-form";
import { mergeErrors } from "@/lib/config/strategy-form";
import { configSaveQuery, useConfigEditor } from "@/lib/hooks/use-config-editor";
import { DEPTHS, RESEARCH_SECTIONS, sectionLabel, VALUATION_METHODS, WRITTEN_BY } from "@/lib/research/report";
import { cn } from "@/lib/utils";

const api = () => getBrowserApiClient();
const EFFORT_OPTIONS = EFFORTS.map((e) => ({ value: e, label: e }));
const VALUATION_OPTIONS = VALUATION_METHODS.map((m) => ({ value: m.id, label: m.label }));

/**
 * Research profiles: the shapes a research run can take. Every field of every profile is
 * editable; profiles can be added, duplicated and removed, and one is the default. Checked
 * here against the backend's bounds before Save, and validated again by the backend.
 */
export function ResearchProfilesEditor() {
  const [form, setForm] = useState<ResearchProfilesForm | null>(null);
  const [baseline, setBaseline] = useState("");
  const editor = useConfigEditor<ResearchProfilesConfig>({
    load: () => api().GET("/config/research-profiles"),
    save: (body, note, base) => api().PUT("/config/research-profiles", { body, params: { query: configSaveQuery(note, base) } }),
    validate: (body) => api().POST("/config/research-profiles/validate", { body }),
    onLoaded: (body) => {
      const loaded = researchProfilesFromBody(body);
      setForm(loaded);
      setBaseline(body ? JSON.stringify(researchProfilesToBody(loaded)) : "");
    },
  });
  const body = useMemo(() => (form ? researchProfilesToBody(form) : null), [form]);
  const clientErrors = useMemo(() => (form ? validateResearchProfiles(form) : {}), [form]);
  const errors = useMemo(() => mergeErrors(clientErrors, editor.errors), [clientErrors, editor.errors]);
  const blocked = Object.keys(clientErrors).length > 0;
  const dirty = body !== null && JSON.stringify(body) !== baseline;

  const setProfile = (key: string, change: Partial<ProfileForm>) =>
    setForm((f) => (f ? { ...f, profiles: f.profiles.map((p) => (p.key === key ? { ...p, ...change } : p)) } : f));
  const ids = form?.profiles.map((p) => p.id) ?? [];

  async function save(note: string | null) {
    if (!body || blocked) return;
    if (await editor.save(body, note)) setBaseline(JSON.stringify(body));
  }

  return (
    <div className="space-y-6">
      <ConfigHeader title="Research profiles" domain="research-profiles">
        <p>
          The shapes a research run can take: which sections, in which order, how deep, which model and budget, which source
          tiers, peers, valuation methods and language. A quick screen and a thorough workup are two profiles. Used from the
          next run.
        </p>
        <p>
          <Link href="/research" className="text-accent underline-offset-4 hover:underline">
            Start research
          </Link>{" "}
          with any profile.
        </p>
      </ConfigHeader>

      {editor.status.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading research profiles…
        </p>
      ) : null}
      {editor.status.kind === "error" ? <AlertMessage title="Could not load the research profiles">{editor.status.message}</AlertMessage> : null}
      {editor.status.kind === "ready" && editor.status.version === null ? (
        <AlertMessage tone="info" title="Defaults in use">
          Nothing saved yet: the two profiles below (standard, quick) are the backend&rsquo;s defaults.
        </AlertMessage>
      ) : null}

      {form ? (
        <>
          <section aria-labelledby="default-profile-heading" className="space-y-2 rounded-lg border border-border bg-surface-raised p-4">
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
                ...form.profiles.map((p) => ({ value: p.id, label: `${p.name || "(no name)"} (${p.id || "no id"})` })),
                ...(form.profiles.some((p) => p.id === form.default_profile)
                  ? []
                  : [{ value: form.default_profile, label: `${form.default_profile} (not a profile)` }]),
              ]}
              onChange={(v) => setForm((f) => (f ? { ...f, default_profile: v } : f))}
            />
          </section>

          {errorsAt(errors, "profiles").length > 0 ? (
            <AlertMessage title="Profiles">{errorsAt(errors, "profiles").join(" ")}</AlertMessage>
          ) : null}

          {form.profiles.map((p, i) => (
            <ProfileCard
              key={p.key}
              profile={p}
              index={i}
              errors={errors}
              isDefault={p.id === form.default_profile}
              onChange={(change) => {
                // Renaming the default profile's id keeps it the default.
                if (change.id !== undefined && p.id === form.default_profile) {
                  setForm((f) =>
                    f
                      ? { ...f, default_profile: change.id!, profiles: f.profiles.map((x) => (x.key === p.key ? { ...x, ...change } : x)) }
                      : f,
                  );
                } else setProfile(p.key, change);
              }}
              onMakeDefault={() => setForm((f) => (f ? { ...f, default_profile: p.id } : f))}
              onDuplicate={() =>
                setForm((f) => {
                  if (!f) return f;
                  const copy = duplicateProfile(p, f.profiles.map((x) => x.id));
                  const at = f.profiles.findIndex((x) => x.key === p.key);
                  return { ...f, profiles: [...f.profiles.slice(0, at + 1), copy, ...f.profiles.slice(at + 1)] };
                })
              }
              onRemove={
                form.profiles.length > 1
                  ? () => setForm((f) => (f ? { ...f, profiles: f.profiles.filter((x) => x.key !== p.key) } : f))
                  : undefined
              }
            />
          ))}

          <Button variant="outline" size="sm" onClick={() => setForm((f) => (f ? { ...f, profiles: [...f.profiles, newProfile(ids)] } : f))}>
            <PlusIcon aria-hidden="true" />
            Add profile
          </Button>

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
  onChange,
  onMakeDefault,
  onDuplicate,
  onRemove,
}: {
  profile: ProfileForm;
  index: number;
  errors: FieldErrors;
  isDefault: boolean;
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
  return (
    <section aria-labelledby={headingId} className="space-y-4 rounded-lg border border-border bg-surface-raised p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h2 id={headingId} className="flex flex-wrap items-center gap-2 text-[22px] leading-tight font-semibold">
          <FlaskConicalIcon aria-hidden="true" className="size-4 text-text-muted" />
          {p.name || "(no name)"} <span className="font-mono text-sm font-normal text-text-muted">{p.id}</span>
          {isDefault ? (
            <span className="inline-flex items-center gap-1 rounded-sm border border-border px-1.5 py-0.5 text-xs font-medium">
              <StarIcon aria-hidden="true" className="size-3" />
              Default
            </span>
          ) : null}
        </h2>
        <div className="flex flex-wrap gap-1">
          {!isDefault ? (
            <Button variant="ghost" size="sm" onClick={onMakeDefault} aria-label={`Make ${label} the default profile`}>
              <StarIcon aria-hidden="true" />
              Make default
            </Button>
          ) : null}
          <Button variant="ghost" size="sm" onClick={onDuplicate} aria-label={`Duplicate ${label}`}>
            <CopyIcon aria-hidden="true" />
            Duplicate
          </Button>
          {onRemove ? (
            <Button variant="ghost" size="sm" onClick={onRemove} aria-label={`Remove ${label}`} disabled={isDefault} title={isDefault ? "Make another profile the default first" : undefined}>
              <Trash2Icon aria-hidden="true" />
              Remove
            </Button>
          ) : null}
        </div>
      </div>
      {own.length > 0 ? <p className="text-xs text-text">{own.join(" ")}</p> : null}

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
        <TextField label="Name" path={`${path}.name`} errors={errors} value={p.name} maxLength={BOUNDS.name.max} onChange={(v) => onChange({ name: v })} />
        <SelectField label="Language" path={`${path}.language`} errors={errors} value={p.language} options={LANGUAGES} onChange={(v) => onChange({ language: v })} />
        <SelectField label="Depth" path={`${path}.depth`} errors={errors} value={p.depth} options={DEPTHS} onChange={(v) => onChange({ depth: v })} />
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
        <SelectField label="Effort" path={`${path}.effort`} errors={errors} value={p.effort} options={EFFORT_OPTIONS} onChange={(v) => onChange({ effort: v })} />
        <TextField
          label="Step cap per run"
          path={`${path}.max_steps`}
          errors={errors}
          value={p.max_steps}
          inputMode="numeric"
          onChange={(v) => onChange({ max_steps: v })}
          hint={`Claude calls plus web searches. ${BOUNDS.max_steps.min} to ${BOUNDS.max_steps.max}.`}
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

      <SectionPicker profile={p} path={`${path}.sections`} errors={errors} onChange={(sections) => onChange({ sections })} />

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
    <fieldset className="space-y-2" aria-describedby={messages.length > 0 ? `${id}-errors` : undefined}>
      <legend className="text-sm font-medium">Sections, in report order</legend>
      <ol className="space-y-1">
        {selected.map((sectionId, i) => {
          const info = RESEARCH_SECTIONS.find((s) => s.id === sectionId);
          return (
            <li key={sectionId} className="flex flex-wrap items-center gap-2 rounded-md border border-border px-2 py-1 text-sm">
              <label className="inline-flex flex-1 items-center gap-2">
                <input type="checkbox" checked onChange={() => onChange(toggleInList(selected, sectionId))} className="size-4 accent-accent" />
                <span className="num w-5 text-text-muted">{i + 1}.</span>
                <span className="font-medium">{sectionLabel(sectionId)}</span>
                {info ? <span className="text-xs text-text-muted">{WRITTEN_BY[info.writtenBy].label} · {info.description}</span> : null}
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
      {selected.length === 0 ? <p className="text-sm text-text-muted">No section picked.</p> : null}
      {unselected.length > 0 ? (
        <div className="space-y-1">
          <p className="text-xs text-text-muted">Not in this profile (tick to add at the end):</p>
          <ul className="flex flex-wrap gap-x-4 gap-y-1">
            {unselected.map((s) => (
              <li key={s.id}>
                <label className={cn("inline-flex items-center gap-1.5 text-sm text-text-muted")} title={s.description}>
                  <input type="checkbox" checked={false} onChange={() => onChange(toggleInList(selected, s.id))} className="size-4 accent-accent" />
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
