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
import { MoreSettings, ShareBar, SummaryPanel } from "@/components/config/summary";

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
      setBaseline(JSON.stringify(sourcesToBody(loaded)));
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
          <SummaryPanel
            title="How far each tier is trusted"
            reading="Sources that are read, by trust tier. Tier 1 confirms a story alone, tier 2 needs a second outlet, tier 3 is sentiment only."
          >
            <ShareBar
              label="Sources by trust tier"
              segments={([1, 2, 3] as const).map((t) => ({
                label: TIERS[t].label,
                value: form.sources.filter((s) => s.enabled && s.tier === t).length,
                tone: t === 1 ? "accent" : t === 2 ? "warning" : "muted",
              }))}
            />
          </SummaryPanel>
          <section aria-labelledby="sources-heading" className="space-y-3">
            <h2 id="sources-heading" className="text-lg font-semibold">
              Sources
            </h2>
            {form.sources.map((s, i) => {
              const perHolding = PER_HOLDING.includes(s.type);
              const path = `sources.${i}`;
              return (
                <details key={s.key} open={!s.name || undefined} aria-label={`Source ${s.name || i + 1}`} className="group rounded-card bg-surface-muted">
                  <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 px-5 py-3 outline-none focus-visible:ring-2 focus-visible:ring-focus/30">
                    <span className="font-medium">{s.name || "New source"}</span>
                    <span className="text-sm text-text-muted">
                      {TIERS[s.tier].label} · {SOURCE_TYPES.find((t) => t.value === s.type)?.label ?? s.type}
                    </span>
                    <span className="ml-auto text-sm">{s.enabled ? "✓ Reading" : "Paused"}</span>
                  </summary>
                  <div className="space-y-3 px-5 pb-5">
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
                </details>
              );
            })}
            <Button variant="outline" onClick={() => set({ sources: [...form.sources, newSource()] })}>
              <PlusIcon aria-hidden="true" />
              Add source
            </Button>
          </section>

          <MoreSettings title="More settings" hint="Outlet tiers, topics, blocked outlets and freshness">
          <section aria-labelledby="trust-heading" className="space-y-3">
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
                <TextAreaField
                  label="Portfolio watch topics"
                  path="portfolio_topics"
                  errors={editor.errors}
                  value={form.portfolio_topics}
                  onChange={(v) => set({ portfolio_topics: v })}
                  rows={6}
                  hint='One topic per line: "id | label | kind | keyword, keyword" (kinds: macro, regulation, rates, currency, sector, supply_chain, other). Start a line with "off " to disable it. Wire items that mention a keyword are tagged with the topic; a tag never links a holding or alerts.'
                />
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
                <TextField
                  label="Google News language"
                  path="google_news_locale.language"
                  errors={editor.errors}
                  value={form.google_news_language}
                  spellCheck={false}
                  onChange={(v) => set({ google_news_language: v })}
                  hint="The edition searched by Google News sources, e.g. en-US, en-GB, de, cs."
                />
                <TextField
                  label="Google News country"
                  path="google_news_locale.country"
                  errors={editor.errors}
                  value={form.google_news_country}
                  spellCheck={false}
                  onChange={(v) => set({ google_news_country: v })}
                  hint="Two-letter code, e.g. US, GB, DE, CZ."
                />
              </div>
            </div>
          </section>
          </MoreSettings>

          <SaveBar dirty={dirty} savedVersionId={editor.version?.id ?? null} busy={editor.busy} errors={editor.errors} message={editor.message} onValidate={() => body && void editor.validate(body)} onSave={(note) => void save(note)} />
        </>
      ) : null}
    </div>
  );
}
