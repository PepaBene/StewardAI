"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { IdentifierSuggestions } from "@/components/disclosure/identifiers";
import { Panel } from "@/components/oversight/panel";
import { PageHeader } from "@/components/page-header";
import { SELECT_CLASS } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { callApi } from "@/app/(app)/relationships/decision-form";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { DEAL_STATE_LABELS, INSIDER_CLASS_LABELS, OWNERSHIP_MATCH_LABELS, RELATIONSHIP_LABELS } from "@/lib/disclosure/alerts";

type Sample = components["schemas"]["AdjudicationSampleOut"];
type Gate = components["schemas"]["AdjudicationGateOut"];
type DatasetKey = "relationship_claim" | "insider_class" | "ownership_match" | "deal_state";

const DATASETS: { key: DatasetKey; label: string; size: number; what: string; options: readonly { value: string; label: string }[] }[] = [
  { key: "relationship_claim", label: "Customer and supplier claims", size: 100, what: "Read each passage and say what it is. A few passages are deliberate non-relationships: calling one a current relationship fails the check.", options: RELATIONSHIP_LABELS },
  { key: "insider_class", label: "Insider transaction classes", size: 50, what: "Read each filed line and say what kind of transaction it is.", options: INSIDER_CLASS_LABELS },
  { key: "ownership_match", label: "Manager holdings matched to a company", size: 50, what: "Read the name a manager filed and the company it was matched to, and say whether they are one company.", options: OWNERSHIP_MATCH_LABELS },
  { key: "deal_state", label: "Deal states", size: 30, what: "Read the passage each stored deal state rests on and name the state it supports. Paying off a loan is not a terminated deal.", options: DEAL_STATE_LABELS },
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
  const [gateError, setGateError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"prelabel" | "accept" | null>(null);
  const [saving, setSaving] = useState<Record<string, true>>({});
  const [confirmAccept, setConfirmAccept] = useState(false);
  // The dataset currently selected: a response for another dataset is ignored.
  const datasetRef = useRef<DatasetKey>("relationship_claim");
  const meta = DATASETS.find((d) => d.key === dataset)!;

  const loadGate = useCallback(async (key: string, sampleKey: string) => {
    try {
      const r = await getBrowserApiClient().GET("/disclosures/adjudication/{dataset}/gate", { params: { path: { dataset: key as DatasetKey }, query: { sample_key: sampleKey } } });
      if (datasetRef.current !== key) return;
      if (r.data) {
        setGate(r.data);
        setGateError(null);
      } else {
        setGate(null);
        setGateError(`The result could not be loaded (HTTP ${r.response.status}). Press Refresh the result to try again.`);
      }
    } catch (e) {
      if (datasetRef.current !== key) return;
      setGate(null);
      setGateError(`The result could not be loaded: ${e instanceof Error ? e.message : String(e)}.`);
    }
  }, []);

  function choose(key: DatasetKey) {
    // Switching the check clears everything that belonged to the old one.
    datasetRef.current = key;
    setDataset(key);
    setSample(null);
    setGateError(null);
    setBusy(null);
    setSaving({});
    setConfirmAccept(false);
    setGate(null);
    setLabels({});
    setNote(null);
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await getBrowserApiClient().GET("/disclosures/adjudication/{dataset}/sample", { params: { path: { dataset }, query: { size: meta.size } } });
        if (cancelled || datasetRef.current !== dataset) return;
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

  async function reloadSample() {
    const r = await getBrowserApiClient().GET("/disclosures/adjudication/{dataset}/sample", { params: { path: { dataset }, query: { size: meta.size } } });
    if (r.data && datasetRef.current === dataset) setSample(r.data);
  }

  async function prelabel() {
    if (busy) return; // a double click must not ask the model twice
    setBusy("prelabel");
    setNote({ ok: true, text: "Asking the model for suggestions…" });
    const r = await callApi(
      () => getBrowserApiClient().POST("/disclosures/adjudication/{dataset}/prelabel", { params: { path: { dataset }, query: { size: meta.size } } }),
      "Suggestions saved.",
    );
    if (datasetRef.current === dataset) {
      setNote({ ok: r.ok, text: r.ok ? "Suggestions saved. Items marked “Label this yourself” still need your own label." : r.text });
      if (r.ok) await reloadSample();
    }
    setBusy(null);
  }

  async function acceptRest() {
    if (!sample || busy) return;
    setBusy("accept");
    setConfirmAccept(false);
    const r = await callApi(
      () => getBrowserApiClient().POST("/disclosures/adjudication/{dataset}/accept-suggestions", { params: { path: { dataset }, query: { sample_key: sample.sample_key } } }),
      "Saved.",
    );
    if (datasetRef.current === dataset) {
      setNote({ ok: r.ok, text: r.ok ? "The unflagged suggestions are saved, recorded as “accepted suggestion” (not as labels you gave yourself). The result below is reloaded from the server." : r.text });
      // reload from the server rather than trusting local state
      if (r.ok) await Promise.all([loadGate(dataset, sample.sample_key), reloadSample()]);
    }
    setBusy(null);
  }

  async function label(itemRef: string, machine: string | null, value: string) {
    if (!sample || !value || saving[itemRef]) return;
    // No optimistic success: show saving, and keep the label only once the server has it.
    setSaving((x) => ({ ...x, [itemRef]: true }));
    const forDataset = dataset;
    const r = await callApi(
      () => getBrowserApiClient().POST("/disclosures/adjudication/{dataset}/label", { params: { path: { dataset } }, body: { sample_key: sample.sample_key, item_ref: itemRef, machine_value: machine, human_value: value } }),
      "Saved.",
    );
    if (datasetRef.current !== forDataset) return;
    setSaving((x) => {
      const rest = { ...x };
      delete rest[itemRef];
      return rest;
    });
    if (r.ok) {
      const labelled = Object.keys(labels).filter((k) => k !== itemRef).length + 1;
      setLabels((l) => ({ ...l, [itemRef]: value }));
      setNote({ ok: true, text: `Saved. ${labelled} of ${sample.items.length} labelled this session.` });
      await loadGate(dataset, sample.sample_key);
    } else setNote({ ok: false, text: `Not saved, so the choice was rolled back: ${r.text}` });
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader kicker="Research & Verification" title="Accuracy checks" description="Label a sample of what the machine read and see whether it is accurate enough to rely on." />

      <Panel id="identifiers" title="Suggested identifiers" description="Company and share-class identifiers found for companies that lack them. Each stays a suggestion until you confirm or reject it.">
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
        {gate ? <GateSummary gate={gate} /> : gateError ? <p role="alert" className="border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">{gateError}</p> : <p className="text-sm text-text-muted">{sample ? "No labels saved yet." : "Loading…"}</p>}
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
                  {it.must_review && !reveal ? <p className="text-xs text-text-muted">Label this yourself.</p> : null}
                  {reveal && it.suggested_value ? (
                    <p className="text-xs text-text-muted">
                      Model suggests {it.suggested_value.replaceAll("_", " ")}
                      {it.suggestion_reason ? `: ${it.suggestion_reason}` : ""}
                      {it.must_review ? ` · Label this yourself (${(it.review_reasons ?? []).join(", ")})` : ""}
                    </p>
                  ) : null}
                </div>
                <select
                  aria-label={`Your label for item ${it.item_ref}`}
                  value={labels[it.item_ref] ?? ""}
                  disabled={saving[it.item_ref] === true}
                  onChange={(e) => void label(it.item_ref, it.machine_value, e.target.value)}
                  className={SELECT_CLASS}
                >
                  <option value="">{saving[it.item_ref] ? "Saving…" : "Choose a label"}</option>
                  {meta.options.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </li>
            ))}
          </ol>
        )}
        {sample && sample.items.length > 0 ? (
          <div className="mt-3 flex flex-wrap gap-2">
            <Button type="button" size="sm" variant="outline" onClick={() => void loadGate(dataset, sample.sample_key)}>Refresh the result</Button>
            <Button type="button" size="sm" variant="outline" disabled={busy !== null} onClick={() => void prelabel()}>Ask the model to suggest labels</Button>
            {sample.items.some((i) => i.suggested_value && !i.must_review) ? (
              confirmAccept ? (
                <span role="alert" className="flex flex-wrap items-center gap-2 border border-dashed border-border bg-surface-muted px-3 py-1.5 text-sm">
                  Save {sample.items.filter((i) => i.suggested_value && !i.must_review).length} model suggestions? They are recorded as “accepted suggestion”, not as labels you gave yourself.
                  <Button type="button" size="sm" disabled={busy !== null} onClick={() => void acceptRest()}>Yes, save them</Button>
                  <Button type="button" size="sm" variant="ghost" onClick={() => setConfirmAccept(false)}>Cancel</Button>
                </span>
              ) : (
                <Button type="button" size="sm" disabled={busy !== null} onClick={() => setConfirmAccept(true)}>Accept the unflagged suggestions</Button>
              )
            ) : null}
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
