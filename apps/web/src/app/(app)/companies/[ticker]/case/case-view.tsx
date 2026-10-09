"use client";

import { useCallback, useState, type FormEvent } from "react";

import { Absent, ResourceGate, SourceLink } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatFigure, type CaseItem, type CaseItemIn, type CaseView } from "@/lib/company/company";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";

const KINDS: readonly (readonly [string, string])[] = [
  ["guidance", "Guidance"],
  ["moat", "Moat"],
  ["bull", "Bull point"],
  ["bear", "Bear point"],
  ["concentration", "Customer or supplier concentration"],
  ["capital_allocation", "Capital allocation"],
  ["risk", "Risk"],
];
const MOATS: readonly (readonly [string, string])[] = [
  ["switching_costs", "Switching costs"],
  ["scale", "Scale"],
  ["ip", "Intellectual property"],
  ["network_effects", "Network effects"],
  ["regulation", "Regulation"],
  ["none", "None identified"],
];

type Form = { id: number | null; kind: string; moat_type: string; statement: string; citations: string; confidence: string; status: string };
const EMPTY: Form = { id: null, kind: "bull", moat_type: "scale", statement: "", citations: "", confidence: "low", status: "draft" };

/** One citation per line: "url" or "url | quoted passage". */
function parseCitations(text: string): CaseItemIn["evidence"] {
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => {
      const [url, ...rest] = l.split("|");
      const passage = rest.join("|").trim();
      return { url: url.trim(), passage: passage || null };
    });
}

function label(list: readonly (readonly [string, string])[], v: string | null | undefined): string {
  return list.find(([k]) => k === v)?.[1] ?? v ?? "";
}

export function CaseTab({ ticker }: { ticker: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/case", { params: { path: { ticker } } }), [ticker]);
  const { state, reload } = useApiResource(fetcher);
  const [form, setForm] = useState<Form>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => ({ ...f, [k]: v }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    const body: CaseItemIn = {
      kind: form.kind,
      moat_type: form.kind === "moat" ? form.moat_type : null,
      statement: form.statement.trim(),
      evidence: parseCitations(form.citations),
      confidence: form.confidence,
      status: form.status,
    };
    if (body.statement.length < 3) {
      setError("Write the assessment first.");
      return;
    }
    if (body.status === "confirmed" && (body.evidence ?? []).length === 0) {
      setError("A confirmed assessment needs at least one citation. Save it as a draft, or add a source.");
      return;
    }
    setError(null);
    const client = getBrowserApiClient();
    const { error: err } =
      form.id === null
        ? await client.POST("/companies/{ticker}/case", { params: { path: { ticker } }, body })
        : await client.PUT("/companies/{ticker}/case/{item_id}", { params: { path: { ticker, item_id: form.id } }, body });
    if (err) {
      setError("The assessment could not be saved. Check the citations are full URLs.");
      return;
    }
    setForm(EMPTY);
    reload();
  }

  async function archive(id: number) {
    await getBrowserApiClient().DELETE("/companies/{ticker}/case/{item_id}", { params: { path: { ticker, item_id: id } } });
    if (form.id === id) setForm(EMPTY);
    reload();
  }

  function edit(i: CaseItem) {
    setForm({
      id: i.id,
      kind: i.kind,
      moat_type: i.moat_type ?? "scale",
      statement: i.statement,
      citations: (i.evidence ?? []).map((c) => (c.passage ? `${c.url} | ${c.passage}` : c.url)).join("\n"),
      confidence: i.confidence,
      status: i.status,
    });
  }

  return (
    <div className="flex flex-col gap-5">
      <ResourceGate state={state} label="the investment case">
        {(v: CaseView) => (
          <>
            <Blueprint aria-labelledby="case-items" className="space-y-4 px-[22px] py-5">
              <PanelHeading kicker="Your assessments, apart from reported numbers" title={<span id="case-items">Investment case</span>} />
              <p className="max-w-[70ch] text-sm text-text-muted">{v.note}</p>
              {v.items.length === 0 ? (
                <p className="text-sm text-text-muted">No assessments yet. Add the first one below.</p>
              ) : (
                <ul className="divide-y divide-border-soft">
                  {v.items.map((i) => (
                    <li key={i.id} className="space-y-1 py-3 text-sm">
                      <p className="flex flex-wrap items-baseline gap-x-3">
                        <span className="font-semibold">{label(KINDS, i.kind)}{i.moat_type ? `: ${label(MOATS, i.moat_type)}` : ""}</span>
                        <span className="text-xs text-text-muted">{i.status === "confirmed" ? "Confirmed" : "Draft"} · {i.confidence} confidence · updated {formatDate(i.updated_at)}</span>
                        <span className="ml-auto flex gap-3 text-xs">
                          <button type="button" onClick={() => edit(i)} className="text-accent-ink hover:underline">Edit</button>
                          <button type="button" onClick={() => archive(i.id)} className="text-loss hover:underline">Archive</button>
                        </span>
                      </p>
                      <p className="max-w-[72ch] leading-relaxed">{i.statement}</p>
                      {(i.evidence ?? []).length > 0 ? (
                        <ul className="space-y-0.5 text-xs text-text-muted">
                          {(i.evidence ?? []).map((c) => (
                            <li key={c.url}><SourceLink href={c.url}>{c.url.replace(/^https?:\/\//, "").slice(0, 70)}</SourceLink>{c.passage ? ` — “${c.passage}”` : ""}</li>
                          ))}
                        </ul>
                      ) : (
                        <p className="text-xs text-unconfirmed">No citation yet.</p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Blueprint>

            <Blueprint aria-labelledby="case-obs" className="space-y-3 px-[22px] py-5">
              <PanelHeading kicker="Computed by the app, not your assessments" title={<span id="case-obs">Observed in filings</span>} />
              <p className="max-w-[70ch] text-xs text-text-muted">{v.observed_note}</p>
              <ul className="divide-y divide-border-soft">
                {(v.observed ?? []).map((o) => (
                  <li key={o.id} className="grid gap-x-4 py-2 text-sm sm:grid-cols-[minmax(10rem,16rem)_8rem_1fr]">
                    <span className="font-medium">{o.label}</span>
                    <span className="num font-semibold">{o.value === null || o.value === undefined ? <Absent reason={o.reason ?? "missing: not computable"} /> : o.unit === "multiple" ? `${Number(o.value).toFixed(1)}×` : formatFigure(o.value, o.unit)}</span>
                    <span className="text-xs text-text-muted">
                      {o.detail}
                      {o.period ? ` ${o.period}.` : ""} Source: {o.source.startsWith("http") ? <SourceLink href={o.source}>filing</SourceLink> : o.source}.
                    </span>
                  </li>
                ))}
              </ul>
            </Blueprint>

            <Blueprint aria-labelledby="case-conc" className="space-y-3 px-[22px] py-5">
              <PanelHeading kicker="Confirmed disclosures, largest first" title={<span id="case-conc">Concentration</span>} />
              {v.concentration.length === 0 ? (
                <p className="text-sm text-text-muted">No confirmed customer or supplier shares are stored.</p>
              ) : (
                <ul className="space-y-1 text-sm">
                  {v.concentration.map((c, n) => (
                    <li key={n}>
                      {String(c.relationship)}: {c.counterparty_name ? String(c.counterparty_name) : c.anonymous_counterparty ? String(c.anonymous_counterparty) : <Absent reason="missing: not named" />} —{" "}
                      <span className="num">{formatFigure(c.reported_share as string | number, "fraction")}</span> of {String(c.share_basis ?? "the stated base")}
                      {c.fiscal_period ? `, ${String(c.fiscal_period)}` : ""} <SourceLink href={c.source_url as string | null}>source</SourceLink>
                    </li>
                  ))}
                </ul>
              )}
            </Blueprint>
          </>
        )}
      </ResourceGate>

      <Blueprint aria-labelledby="case-form" className="px-[22px] py-5">
        <PanelHeading kicker={form.id === null ? "New" : "Editing"} title={<span id="case-form">{form.id === null ? "Add an assessment" : "Edit assessment"}</span>} />
        <form onSubmit={submit} className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="text-sm">
            <span className="mb-1 block text-xs text-text-muted">Type</span>
            <select value={form.kind} onChange={(e) => set("kind", e.target.value)} className="w-full border border-border bg-surface px-2 py-1.5">
              {KINDS.map(([v, t]) => (
                <option key={v} value={v}>{t}</option>
              ))}
            </select>
          </label>
          {form.kind === "moat" ? (
            <label className="text-sm">
              <span className="mb-1 block text-xs text-text-muted">Kind of moat</span>
              <select value={form.moat_type} onChange={(e) => set("moat_type", e.target.value)} className="w-full border border-border bg-surface px-2 py-1.5">
                {MOATS.map(([v, t]) => (
                  <option key={v} value={v}>{t}</option>
                ))}
              </select>
            </label>
          ) : null}
          <label className="text-sm sm:col-span-2">
            <span className="mb-1 block text-xs text-text-muted">Assessment</span>
            <textarea value={form.statement} onChange={(e) => set("statement", e.target.value)} rows={3} className="w-full border border-border bg-surface px-2 py-1.5" />
          </label>
          <label className="text-sm sm:col-span-2">
            <span className="mb-1 block text-xs text-text-muted">Citations: one per line, as “https://… | quoted passage”</span>
            <textarea value={form.citations} onChange={(e) => set("citations", e.target.value)} rows={3} className="w-full border border-border bg-surface px-2 py-1.5 font-mono text-xs" />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-xs text-text-muted">Confidence</span>
            <select value={form.confidence} onChange={(e) => set("confidence", e.target.value)} className="w-full border border-border bg-surface px-2 py-1.5">
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
            </select>
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-xs text-text-muted">Status</span>
            <select value={form.status} onChange={(e) => set("status", e.target.value)} className="w-full border border-border bg-surface px-2 py-1.5">
              <option value="draft">Draft</option>
              <option value="confirmed">Confirmed (needs a citation)</option>
            </select>
          </label>
          <div className="flex items-center gap-3 sm:col-span-2">
            <button type="submit" className="border border-accent px-4 py-1.5 text-sm font-semibold hover:bg-surface-sunken">
              {form.id === null ? "Add assessment" : "Save changes"}
            </button>
            {form.id !== null ? (
              <button type="button" onClick={() => setForm(EMPTY)} className="text-sm text-text-muted hover:underline">Cancel edit</button>
            ) : null}
            {error ? <p role="alert" className="text-sm text-loss">{error}</p> : null}
          </div>
        </form>
      </Blueprint>
    </div>
  );
}
