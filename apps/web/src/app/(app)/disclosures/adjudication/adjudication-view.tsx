"use client";

import { useCallback, useEffect, useState } from "react";

import { IdentifierSuggestions } from "@/components/disclosure/identifiers";
import { Panel } from "@/components/oversight/panel";
import { PageHeader } from "@/components/page-header";
import { SELECT_CLASS } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { callApi } from "@/app/(app)/relationships/decision-form";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { INSIDER_CLASS_LABELS, OWNERSHIP_MATCH_LABELS, RELATIONSHIP_LABELS } from "@/lib/disclosure/alerts";

type Sample = components["schemas"]["AdjudicationSampleOut"];
type Gate = components["schemas"]["AdjudicationGateOut"];
type DatasetKey = "relationship_claim" | "insider_class" | "ownership_match";

const DATASETS: { key: DatasetKey; label: string; size: number; what: string; options: readonly { value: string; label: string }[] }[] = [
  { key: "relationship_claim", label: "Customer and supplier claims", size: 100, what: "Read each passage and say what it is. A few passages are deliberate non-relationships: calling one a current relationship fails the check.", options: RELATIONSHIP_LABELS },
  { key: "insider_class", label: "Insider transaction classes", size: 50, what: "Read each filed line and say what kind of transaction it is.", options: INSIDER_CLASS_LABELS },
  { key: "ownership_match", label: "Manager holdings matched to a company", size: 50, what: "Read the name a manager filed and the company it was matched to, and say whether they are one company.", options: OWNERSHIP_MATCH_LABELS },
];

/**
 * Spot checks of the machine’s work. You label a reproducible sample without seeing the machine’s
 * answer first; the gate then says whether the labelled sample clears the bar for that dataset. Only
 * a person’s labels count, and a deliberate false positive that is called true fails the whole run.
 */
export function AdjudicationPageView() {
  const [dataset, setDataset] = useState<DatasetKey>("relationship_claim");
  const [sample, setSample] = useState<Sample | null>(null);
  const [gate, setGate] = useState<Gate | null>(null);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [reveal, setReveal] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const meta = DATASETS.find((d) => d.key === dataset)!;

  const loadGate = useCallback(async (key: string, sampleKey: string) => {
    try {
      const r = await getBrowserApiClient().GET("/disclosures/adjudication/{dataset}/gate", { params: { path: { dataset: key as DatasetKey }, query: { sample_key: sampleKey } } });
      if (r.data) setGate(r.data);
    } catch {
      setGate(null);
    }
  }, []);

  function choose(key: DatasetKey) {
    // Switching the check clears everything that belonged to the old one.
    setDataset(key);
    setSample(null);
    setGate(null);
    setLabels({});
    setNote(null);
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await getBrowserApiClient().GET("/disclosures/adjudication/{dataset}/sample", { params: { path: { dataset }, query: { size: meta.size } } });
        if (cancelled) return;
        if (r.data) {
          setSample(r.data);
          void loadGate(dataset, r.data.sample_key);
        } else setNote({ ok: false, text: r.response.status === 401 || r.response.status === 403 ? "Sign in again (with MFA) and retry." : r.response.status === 422 ? "There is nothing to sample for this check yet." : `The sample failed to load (HTTP ${r.response.status}).` });
      } catch (e) {
        if (!cancelled) setNote({ ok: false, text: `Backend unreachable: ${e instanceof Error ? e.message : String(e)}` });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [dataset, meta.size, loadGate]);

  async function label(itemRef: string, machine: string | null, value: string) {
    if (!sample || !value) return;
    setLabels((l) => ({ ...l, [itemRef]: value }));
    const r = await callApi(
      () => getBrowserApiClient().POST("/disclosures/adjudication/{dataset}/label", { params: { path: { dataset } }, body: { sample_key: sample.sample_key, item_ref: itemRef, machine_value: machine, human_value: value } }),
      "Saved.",
    );
    setNote({ ok: r.ok, text: r.ok ? `Saved. ${Object.keys(labels).length + 1} of ${sample.items.length} labelled this session.` : r.text });
    if (r.ok) await loadGate(dataset, sample.sample_key);
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader kicker="Research & Verification" title="Accuracy checks" description="Label a sample of what the machine read and see whether it is accurate enough to rely on." />

      <Panel id="identifiers" title="Suggested identifiers" description="Company and share-class identifiers found for companies that lack them. Each is a suggestion until a person confirms it.">
        <IdentifierSuggestions />
      </Panel>

      <div role="group" aria-label="What to check" className="flex flex-wrap gap-1">
        {DATASETS.map((d) => (
          <button
            key={d.key}
            type="button"
            aria-pressed={d.key === dataset}
            onClick={() => choose(d.key)}
            className={`h-9 border px-3 text-sm focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none ${d.key === dataset ? "border-accent bg-accent text-accent-contrast" : "border-border bg-surface-raised hover:bg-surface-muted"}`}
          >
            {d.label}
          </button>
        ))}
      </div>

      {note ? (
        <p role={note.ok ? "status" : "alert"} className={note.ok ? "border border-border bg-surface-muted px-3 py-2 text-sm" : "border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm"}>
          {note.text}
        </p>
      ) : null}

      <Panel id="gate" title="Does it clear the bar?" description="Measured over the labels you have saved for this sample.">
        {gate ? <GateSummary gate={gate} /> : <p className="text-sm text-text-muted">{sample ? "No labels saved yet." : "Loading…"}</p>}
      </Panel>

      <Panel id="sample" title={meta.label} description={meta.what}>
        <label className="mb-3 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={reveal} onChange={(e) => setReveal(e.target.checked)} className="size-4" />
          Show the machine’s answer (this can bias your label)
        </label>
        {sample === null ? (
          <p className="text-sm text-text-muted">Loading the sample…</p>
        ) : sample.items.length === 0 ? (
          <p className="text-sm text-text-muted">Nothing to label yet: no rows of this kind exist.</p>
        ) : (
          <ol className="space-y-3">
            {sample.items.map((it) => (
              <li key={it.item_ref} className="grid gap-2 border border-border bg-surface-raised p-3 sm:grid-cols-[1fr_16rem] sm:items-start">
                <div className="min-w-0 space-y-1">
                  <p className="max-w-[72ch] text-sm">{it.text}</p>
                  {it.hints.length ? <p className="text-xs text-text-muted">{it.hints.join(" · ")}</p> : null}
                  {reveal ? <p className="text-xs text-text-muted">Machine said: {(it.machine_value ?? "nothing").replaceAll("_", " ")}</p> : null}
                </div>
                <select
                  aria-label={`Your label for item ${it.item_ref}`}
                  value={labels[it.item_ref] ?? ""}
                  onChange={(e) => void label(it.item_ref, it.machine_value, e.target.value)}
                  className={SELECT_CLASS}
                >
                  <option value="">Choose a label</option>
                  {meta.options.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </li>
            ))}
          </ol>
        )}
        {sample && sample.items.length > 0 ? (
          <div className="mt-3">
            <Button type="button" size="sm" variant="outline" onClick={() => void loadGate(dataset, sample.sample_key)}>Refresh the result</Button>
          </div>
        ) : null}
      </Panel>
    </div>
  );
}

function GateSummary({ gate }: { gate: Gate }) {
  const pct = (v: string | null) => (v == null ? "—" : `${(Number(v) * 100).toFixed(1)}%`);
  return (
    <div className="space-y-2 text-sm">
      <p className="text-base font-semibold">{gate.met ? "The bar is met." : "The bar is not met yet."}</p>
      <p className="max-w-[72ch]">{gate.reason}</p>
      <dl className="grid max-w-md grid-cols-[auto_1fr] gap-x-4 gap-y-0.5">
        <dt className="text-text-muted">Labelled</dt>
        <dd className="num">{gate.labelled} of at least {gate.needed}</dd>
        <dt className="text-text-muted">Correct</dt>
        <dd className="num">{gate.correct}{gate.rate ? ` (${pct(gate.rate)})` : ""}, bar {pct(gate.bar)}</dd>
        {gate.planted > 0 ? (
          <>
            <dt className="text-text-muted">Deliberate traps caught</dt>
            <dd className="num">{gate.planted_caught} of {gate.planted}</dd>
          </>
        ) : null}
      </dl>
    </div>
  );
}
