"use client";

import { useCallback, useState, type FormEvent } from "react";

import { ResourceGate, SourceLink } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { BENCHMARK_BASIS_OPTIONS, FACT_LABEL, REPLICATION_OPTIONS, checkFactValue, isNumericFact, STATUS_LABEL, STATUS_RANK, type Checklist, type Criterion, type FundFact } from "@/lib/fundresearch/fund-research";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

const MARK: Record<string, string> = { good: "✓", watch: "!", concern: "✕", info: "i", unknown: "?" };
const TONE: Record<string, string> = { good: "text-gain", watch: "text-unconfirmed", concern: "text-loss", info: "text-text-muted", unknown: "text-text-muted" };

function CriterionRow({ c }: { c: Criterion }) {
  return (
    <li className="grid gap-x-4 gap-y-1 border-b border-border-soft py-3 sm:grid-cols-[9rem_1fr]">
      <div className={cn("flex items-center gap-2 text-sm font-semibold", TONE[c.status])}>
        <span aria-hidden="true" className="inline-flex size-5 items-center justify-center border border-current text-xs">{MARK[c.status]}</span>
        {STATUS_LABEL[c.status]}
      </div>
      <div className="min-w-0 text-sm">
        <p className="font-medium">
          {c.label}
          {c.critical ? <span className="ml-2 text-xs font-normal text-text-muted">needed for a summary</span> : null}
        </p>
        <p className={cn(c.status === "unknown" && "text-text-muted")}>{c.observed}</p>
        <p className="text-xs text-text-muted">
          Rule: {c.rule}.{c.source ? ` Source: ${c.source}.` : ""} {c.source_url ? <SourceLink href={c.source_url}>document</SourceLink> : null}
        </p>
      </div>
    </li>
  );
}

function FactForm({ isin, onSaved }: { isin: string; onSaved: () => void }) {
  const [key, setKey] = useState("replication_method");
  const [value, setValue] = useState("physical_full");
  const [sourceUrl, setSourceUrl] = useState("");
  const [passage, setPassage] = useState("");
  const [asOf, setAsOf] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return; // a double click must not append the same fact twice
    const checked = checkFactValue(key, value);
    if ("error" in checked) return setError(checked.error);
    if (!/^https?:\/\//.test(sourceUrl.trim())) return setError("A source is required: paste the web address of the document that states it.");
    setError(null);
    setBusy(true);
    let failed = false;
    try {
      const { error: err } = await getBrowserApiClient().PUT("/funds/{isin}/facts", {
        params: { path: { isin } },
        body: { key, value: checked.value, source_url: sourceUrl.trim(), passage: passage.trim() || null, as_of: asOf || null },
      });
      failed = Boolean(err);
    } catch {
      failed = true;
    }
    setBusy(false);
    if (failed) return setError("The fact could not be saved. Check the value and the address.");
    setSourceUrl("");
    setPassage("");
    onSaved();
  }

  return (
    <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
      <label className="text-sm">
        <span className="mb-1 block text-xs text-text-muted">Fact</span>
        <select
          value={key}
          onChange={(e) => {
            setKey(e.target.value);
            setValue(e.target.value === "replication_method" ? "physical_full" : e.target.value === "benchmark_basis" ? "price" : "");
          }}
          className="w-full border border-border bg-surface px-2 py-1.5"
        >
          {Object.entries(FACT_LABEL).map(([k, t]) => (
            <option key={k} value={k}>{t}</option>
          ))}
        </select>
      </label>
      <label className="text-sm">
        <span className="mb-1 block text-xs text-text-muted">Value</span>
        {key === "replication_method" ? (
          <select value={value} onChange={(e) => setValue(e.target.value)} className="w-full border border-border bg-surface px-2 py-1.5">
            {REPLICATION_OPTIONS.map(([v, t]) => (
              <option key={v} value={v}>{t}</option>
            ))}
          </select>
        ) : key === "benchmark_basis" ? (
          <select value={value} onChange={(e) => setValue(e.target.value)} className="w-full border border-border bg-surface px-2 py-1.5">
            {BENCHMARK_BASIS_OPTIONS.map(([v, t]) => (
              <option key={v} value={v}>{t}</option>
            ))}
          </select>
        ) : (
          <input
            value={value}
            onChange={(e) => setValue(key === "benchmark_currency" ? e.target.value.toUpperCase() : e.target.value)}
            inputMode={isNumericFact(key) ? "decimal" : undefined}
            maxLength={key === "benchmark_currency" ? 3 : undefined}
            className="w-full border border-border bg-surface px-2 py-1.5"
          />
        )}
      </label>
      <label className="text-sm sm:col-span-2">
        <span className="mb-1 block text-xs text-text-muted">Source (web address of the factsheet, KID or prospectus)</span>
        <input value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} className="w-full border border-border bg-surface px-2 py-1.5 font-mono text-xs" />
      </label>
      <label className="text-sm">
        <span className="mb-1 block text-xs text-text-muted">Quoted passage (optional)</span>
        <input value={passage} onChange={(e) => setPassage(e.target.value)} className="w-full border border-border bg-surface px-2 py-1.5" />
      </label>
      <label className="text-sm">
        <span className="mb-1 block text-xs text-text-muted">Document date (optional)</span>
        <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} className="w-full border border-border bg-surface px-2 py-1.5" />
      </label>
      <div className="flex items-center gap-3 sm:col-span-2">
        <button type="submit" disabled={busy} className="border border-accent px-4 py-1.5 text-sm font-semibold hover:bg-surface-muted disabled:opacity-50">{busy ? "Saving…" : "Record fact"}</button>
        {error ? <p role="alert" className="text-sm text-loss">{error}</p> : null}
      </div>
    </form>
  );
}

export function ChecklistTab({ isin }: { isin: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/funds/{isin}/checklist", { params: { path: { isin } } }), [isin]);
  const { state, reload } = useApiResource(fetcher);
  const historyFetcher = useCallback(() => getBrowserApiClient().GET("/funds/{isin}/facts/history", { params: { path: { isin } } }), [isin]);
  const { state: history, reload: reloadHistory } = useApiResource(historyFetcher);
  const facts: FundFact[] = history.kind === "ok" ? history.data : [];

  return (
    <div className="flex flex-col gap-5">
      <ResourceGate state={state} label="the checklist">
        {(c: Checklist) => (
          <Blueprint aria-labelledby="cl-main" className="space-y-3 px-[22px] py-5">
            <PanelHeading kicker={`As of ${formatDate(c.as_of)} · each criterion shows its rule`} title={<span id="cl-main">Due diligence checklist</span>} />
            {c.summary_available ? (
              <p className="max-w-[72ch] text-sm">{c.summary}</p>
            ) : (
              <p className="max-w-[72ch] border border-dashed border-border px-3 py-2 text-sm text-text-muted">{c.abstain_reason}</p>
            )}
            <ul>
              {[...c.criteria].sort((a, b) => (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9)).map((cr) => (
                <CriterionRow key={cr.id} c={cr} />
              ))}
            </ul>
            <p className="text-xs text-text-muted">Method {c.method_version}. Thresholds are StewardAI&apos;s own and are stated on each row; they are not a rating.</p>
          </Blueprint>
        )}
      </ResourceGate>

      <Blueprint aria-labelledby="cl-facts" className="space-y-4 px-[22px] py-5">
        <PanelHeading kicker="Not in the issuer data; you record it with a source" title={<span id="cl-facts">Facts about this fund</span>} />
        <FactForm isin={isin} onSaved={() => { reload(); reloadHistory(); }} />
        {history.kind === "loading" ? (
          <p className="text-sm text-text-muted">Loading the recorded facts…</p>
        ) : history.kind === "error" ? (
          <p role="alert" className="text-sm text-loss">
            The recorded facts could not be loaded, so they are unknown, not empty.{" "}
            <button type="button" className="underline underline-offset-2" onClick={() => reloadHistory()}>Try again</button>
          </p>
        ) : facts.length === 0 ? (
          <p className="text-sm text-text-muted">Nothing recorded yet.</p>
        ) : (
          <ul className="divide-y divide-border-soft text-sm">
            {facts.map((f) => (
              <li key={`${f.key}-${f.recorded_at}`} className="flex flex-wrap gap-x-4 py-1.5">
                <span className="w-48 shrink-0 text-text-muted">{FACT_LABEL[f.key] ?? f.key}</span>
                <span className="min-w-0 flex-1">{f.value}{f.passage ? <span className="block text-xs text-text-muted">“{f.passage}”</span> : null}</span>
                <span className="text-xs text-text-muted">recorded {formatDate(f.recorded_at)} <SourceLink href={f.source_url}>source</SourceLink></span>
              </li>
            ))}
          </ul>
        )}
        <p className="text-xs text-text-muted">Every entry is kept. A correction is a new entry and the newest one counts, so an earlier assessment can be explained.</p>
      </Blueprint>
    </div>
  );
}
