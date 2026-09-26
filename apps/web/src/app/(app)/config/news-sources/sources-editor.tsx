"use client";

import { LoaderCircleIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ConfigHeader } from "@/components/config/config-header";
import { CheckboxField, SelectField, TextAreaField, TextField } from "@/components/config/fields";
import { SaveBar } from "@/components/config/save-bar";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type NewsSourcesConfig } from "@/lib/api/client";
import {
  DEFAULT_SOURCES,
  newSource,
  PER_HOLDING,
  PER_HOLDING_NOTES,
  SOURCE_TYPES,
  sourcesFromBody,
  sourcesToBody,
  type SourceForm,
  type SourcesForm,
} from "@/lib/config/news-forms";
import { configSaveQuery, useConfigEditor } from "@/lib/hooks/use-config-editor";
import { TIERS } from "@/lib/news/labels";

const api = () => getBrowserApiClient();
const TIER_OPTIONS = ([1, 2, 3] as const).map((t) => ({ value: t, label: `${TIERS[t].label}` }));

export function SourcesEditor() {
  const [form, setForm] = useState<SourcesForm | null>(null);
  const [baseline, setBaseline] = useState("");
  const editor = useConfigEditor<NewsSourcesConfig>({
    load: () => api().GET("/config/news-sources"),
    save: (body, note, base) => api().PUT("/config/news-sources", { body, params: { query: configSaveQuery(note, base) } }),
    validate: (body) => api().POST("/config/news-sources/validate", { body }),
    onLoaded: (body) => {
      const loaded = sourcesFromBody(body, DEFAULT_SOURCES);
      setForm(loaded);
      setBaseline(body ? JSON.stringify(sourcesToBody(loaded)) : "");
    },
  });
  const body = useMemo(() => (form ? sourcesToBody(form) : null), [form]);
  const dirty = body !== null && JSON.stringify(body) !== baseline;
  const set = (change: Partial<SourcesForm>) => setForm((f) => (f ? { ...f, ...change } : f));
  const setSource = (key: string, change: Partial<SourceForm>) =>
    setForm((f) => (f ? { ...f, sources: f.sources.map((s) => (s.key === key ? { ...s, ...change } : s)) } : f));

  async function save(note: string | null) {
    if (body && (await editor.save(body, note))) setBaseline(JSON.stringify(body));
  }

  return (
    <div className="space-y-6">
      <ConfigHeader title="News sources" domain="news-sources">
        <p>
          The feeds the news check reads and how far each outlet is trusted. A source added here is
          read on the next run. Tier 1 (the company, regulators, official newswires) confirms a
          story alone; tier 2 needs a second independent outlet; tier 3 is sentiment only.
        </p>
      </ConfigHeader>
      {editor.status.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading sources…
        </p>
      ) : null}
      {editor.status.kind === "error" ? <AlertMessage title="Could not load the sources">{editor.status.message}</AlertMessage> : null}
      {editor.status.kind === "ready" && editor.status.version === null ? (
        <AlertMessage tone="info" title="Defaults in use">Nothing saved yet; these are the built-in sources and outlet tiers.</AlertMessage>
      ) : null}

      {form ? (
        <>
          <section aria-labelledby="sources-heading" className="space-y-3">
            <h2 id="sources-heading" className="text-lg font-semibold">
              Sources
            </h2>
            {form.sources.map((s, i) => {
              const perHolding = PER_HOLDING.includes(s.type);
              const path = `sources.${i}`;
              return (
                <div key={s.key} role="group" aria-label={`Source ${s.name || i + 1}`} className="space-y-3 rounded-lg border border-border bg-surface-raised p-4">
                  <div className="grid items-start gap-3 sm:grid-cols-2 lg:grid-cols-[1fr_1.4fr_0.8fr_1.4fr_auto_auto]">
                    <TextField label="Id" path={`${path}.id`} errors={editor.errors} value={s.id} onChange={(v) => setSource(s.key, { id: v })} placeholder="apple-ir" />
                    <TextField label="Name" path={`${path}.name`} errors={editor.errors} value={s.name} onChange={(v) => setSource(s.key, { name: v })} />
                    <SelectField label="Tier" path={`${path}.tier`} errors={editor.errors} value={s.tier} options={TIER_OPTIONS} onChange={(v) => setSource(s.key, { tier: v })} />
                    <SelectField label="Type" path={`${path}.type`} errors={editor.errors} value={s.type} options={SOURCE_TYPES} onChange={(v) => setSource(s.key, { type: v })} />
                    <CheckboxField className="lg:mt-7" label="Enabled" checked={s.enabled} onChange={(v) => setSource(s.key, { enabled: v })} />
                    <Button variant="ghost" size="icon" className="lg:mt-6" aria-label={`Remove source ${s.name || i + 1}`} onClick={() => set({ sources: form.sources.filter((x) => x.key !== s.key) })}>
                      <Trash2Icon aria-hidden="true" />
                    </Button>
                  </div>
                  {perHolding ? (
                    <p className="text-sm text-text-muted">
                      Built per holding from its profile; no URL needed. {PER_HOLDING_NOTES[s.type] ?? ""}
                    </p>
                  ) : (
                    <div className="grid gap-3 lg:grid-cols-[2fr_1fr]">
                      <TextField label={s.type === "rss" ? "Feed URL" : "Page URL"} type="url" path={`${path}.url`} errors={editor.errors} value={s.url} onChange={(v) => setSource(s.key, { url: v })} />
                      <TextAreaField label="About these holdings (optional)" path={`${path}.tickers`} errors={editor.errors} value={s.tickers} onChange={(v) => setSource(s.key, { tickers: v })} rows={1} hint="One ticker per line: items link to them without a name match (a company's own IR feed)." />
                    </div>
                  )}
                  <CheckboxField
                    label="Macro or central-bank source"
                    checked={s.macro}
                    onChange={(v) => setSource(s.key, { macro: v })}
                    hint="Its items are kept without matching a holding and appear under Markets and macro in the digest."
                  />
                </div>
              );
            })}
            <Button variant="outline" onClick={() => set({ sources: [...form.sources, newSource()] })}>
              <PlusIcon aria-hidden="true" />
              Add source
            </Button>
          </section>

          <section aria-labelledby="trust-heading" className="space-y-3 rounded-lg border border-border bg-surface-raised p-4">
            <h2 id="trust-heading" className="text-lg font-semibold">
              Outlet trust and freshness
            </h2>
            <div className="grid gap-3 lg:grid-cols-2">
              <TextAreaField
                label="Outlet tiers"
                path="domain_tiers"
                errors={editor.errors}
                value={form.domain_tiers}
                onChange={(v) => set({ domain_tiers: v })}
                rows={10}
                hint='One "domain tier" per line, e.g. "reuters.com 2". Subdomains included. Every holding profile&apos;s IR domains count as tier 1.'
              />
              <div className="space-y-3">
                <TextAreaField label="Blocked outlets" path="blocked_domains" errors={editor.errors} value={form.blocked_domains} onChange={(v) => set({ blocked_domains: v })} rows={3} hint="One domain per line; their items are dropped." />
                <TextAreaField
                  label="Allowed outlets for aggregators (optional)"
                  path="allowed_domains"
                  errors={editor.errors}
                  value={form.allowed_domains}
                  onChange={(v) => set({ allowed_domains: v })}
                  rows={3}
                  hint="One domain per line. When set, Finnhub and Google News items are kept only from these outlets and the tier-1 and tier-2 outlets above. Empty: no allow list."
                />
                <SelectField label="Tier of an unlisted outlet (Finnhub, Google News)" path="unknown_outlet_tier" errors={editor.errors} value={form.unknown_outlet_tier} options={TIER_OPTIONS} onChange={(v) => set({ unknown_outlet_tier: v })} />
                <TextField label="Freshness window (hours)" inputMode="numeric" path="freshness_hours" errors={editor.errors} value={form.freshness_hours} onChange={(v) => set({ freshness_hours: v })} hint="Older items are dropped." />
                <CheckboxField label="Keep tier-3 items (labelled sentiment)" checked={form.include_tier3} onChange={(v) => set({ include_tier3: v })} />
              </div>
            </div>
          </section>

          <SaveBar dirty={dirty} savedVersionId={editor.version?.id ?? null} busy={editor.busy} errors={editor.errors} message={editor.message} onValidate={() => body && void editor.validate(body)} onSave={(note) => void save(note)} />
        </>
      ) : null}
    </div>
  );
}
