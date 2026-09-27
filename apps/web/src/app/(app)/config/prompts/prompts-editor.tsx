"use client";

import { LoaderCircleIcon, PencilIcon, RotateCcwIcon, TriangleAlertIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ConfigHeader } from "@/components/config/config-header";
import { TextAreaField } from "@/components/config/fields";
import { SaveBar } from "@/components/config/save-bar";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type PromptDefault, type PromptsConfig } from "@/lib/api/client";
import {
  characterCount,
  customize,
  overridesFromBody,
  overridesToBody,
  resetToDefault,
  setOverride,
  validatePromptsForm,
  type PromptOverrides,
} from "@/lib/config/prompts-form";
import { mergeErrors } from "@/lib/config/strategy-form";
import { configSaveQuery, useConfigEditor } from "@/lib/hooks/use-config-editor";

const api = () => getBrowserApiClient();

type Defaults = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; prompts: PromptDefault[] };

export function PromptsEditor() {
  const [overrides, setOverrides] = useState<PromptOverrides | null>(null);
  const [baseline, setBaseline] = useState("");
  const [defaults, setDefaults] = useState<Defaults>({ kind: "loading" });
  const editor = useConfigEditor<PromptsConfig>({
    load: () => api().GET("/config/prompts"),
    save: (body, note, base) => api().PUT("/config/prompts", { body, params: { query: configSaveQuery(note, base) } }),
    validate: (body) => api().POST("/config/prompts/validate", { body }),
    onLoaded: (body) => {
      const loaded = overridesFromBody(body);
      setOverrides(loaded);
      setBaseline(JSON.stringify(overridesToBody(loaded)));
    },
  });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data, response } = await api().GET("/config/prompts/defaults");
        if (cancelled) return;
        setDefaults(data ? { kind: "ready", prompts: data } : { kind: "error", message: `The API answered HTTP ${response.status}.` });
      } catch (err) {
        if (!cancelled) setDefaults({ kind: "error", message: err instanceof Error ? err.message : String(err) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const body = useMemo(() => (overrides ? overridesToBody(overrides) : null), [overrides]);
  const clientErrors = useMemo(() => (overrides ? validatePromptsForm(overrides) : {}), [overrides]);
  const errors = useMemo(() => mergeErrors(clientErrors, editor.errors), [clientErrors, editor.errors]);
  const blocked = Object.keys(clientErrors).length > 0;
  const dirty = body !== null && JSON.stringify(body) !== baseline;
  const overriddenCount = overrides ? Object.keys(overrides).length : 0;

  async function save(note: string | null) {
    if (!body || blocked) return;
    if (await editor.save(body, note)) setBaseline(JSON.stringify(body));
  }

  const loading = editor.status.kind === "loading" || defaults.kind === "loading";
  return (
    <div className="space-y-6">
      <ConfigHeader title="Prompts" domain="prompts">
        <p>
          The system prompt each AI step sends. Customize one to change its instructions or tone; the next run uses it. Parts
          filled in by code (the event types to choose from, the output format) are not part of the prompt and stay fixed, so an
          override cannot break what the step returns.
        </p>
        <p>The CI evals check the built-in prompts only: an overridden prompt is not covered by them.</p>
      </ConfigHeader>
      {loading ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading prompts…
        </p>
      ) : null}
      {editor.status.kind === "error" ? <AlertMessage title="Could not load the prompts config">{editor.status.message}</AlertMessage> : null}
      {defaults.kind === "error" ? <AlertMessage title="Could not load the built-in prompts">{defaults.message}</AlertMessage> : null}
      {editor.status.kind === "ready" && overriddenCount === 0 && !dirty ? (
        <AlertMessage tone="info" title="Built-in prompts in use">
          No prompt is overridden; every AI step sends its built-in prompt.
        </AlertMessage>
      ) : null}

      {overrides && defaults.kind === "ready" ? (
        <>
          <div className="space-y-4">
            {defaults.prompts.map((p) => (
              <PromptCard
                key={p.key}
                prompt={p}
                text={overrides[p.key]}
                errors={errors}
                onCustomize={() => setOverrides((o) => (o ? customize(o, p.key, p.default_text) : o))}
                onReset={() => setOverrides((o) => (o ? resetToDefault(o, p.key) : o))}
                onChange={(v) => setOverrides((o) => (o ? setOverride(o, p.key, v) : o))}
              />
            ))}
          </div>

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

function PromptCard({
  prompt,
  text,
  errors,
  onCustomize,
  onReset,
  onChange,
}: {
  prompt: PromptDefault;
  text: string | undefined;
  errors: Record<string, string[]>;
  onCustomize: () => void;
  onReset: () => void;
  onChange: (value: string) => void;
}) {
  const overridden = text !== undefined;
  const headingId = `prompt-${prompt.key}`;
  return (
    <section aria-labelledby={headingId} className="space-y-3 rounded-lg border border-border bg-surface-raised p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="max-w-prose space-y-1">
          <h2 id={headingId} className="flex flex-wrap items-center gap-2 text-lg font-semibold">
            {prompt.title}
            {overridden ? (
              <span className="inline-flex items-center gap-1 rounded-full border border-warning-signal/40 px-2 py-0.5 text-xs font-medium text-text">
                <TriangleAlertIcon aria-hidden="true" className="size-3.5 text-warning-signal" />
                Overridden — not covered by CI evals
              </span>
            ) : (
              <span className="rounded-full border border-border px-2 py-0.5 text-xs font-medium text-text-muted">Built-in</span>
            )}
          </h2>
          <p className="text-sm text-text-muted">{prompt.description}</p>
        </div>
        {overridden ? (
          <Button variant="outline" size="sm" onClick={onReset} aria-label={`Reset ${prompt.title} to the built-in prompt`}>
            <RotateCcwIcon aria-hidden="true" />
            Reset to default
          </Button>
        ) : (
          <Button variant="outline" size="sm" onClick={onCustomize} aria-label={`Customize ${prompt.title}`}>
            <PencilIcon aria-hidden="true" />
            Customize
          </Button>
        )}
      </div>

      {text !== undefined ? (
        <TextAreaField
          label="Prompt"
          path={`overrides.${prompt.key}`}
          errors={errors}
          value={text}
          onChange={onChange}
          rows={Math.min(24, Math.max(6, text.split("\n").length + 1))}
          hint={
            <>
              {characterCount(text)}
              {text === prompt.default_text ? " · same as the built-in prompt" : null}
            </>
          }
        />
      ) : null}

      <details className="rounded-md border border-border p-3">
        <summary className="cursor-pointer text-sm font-medium">
          Built-in prompt <span className="font-normal text-text-muted">({characterCount(prompt.default_text)})</span>
        </summary>
        <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-text">{prompt.default_text}</pre>
      </details>
    </section>
  );
}
