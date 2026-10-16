"use client";

import { normaliseSeparators } from "@/lib/number-input";
import { useCallback, useEffect, useRef, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Absent, ResourceGate, SourceLink } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import {
  fractionToPercent,
  gridMatrix,
  percentToFraction,
  sortScenarios,
  toNumber,
  type Assumptions,
  type SavedScenario,
  type Valuation,
} from "@/lib/company/company";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

import type { Headline } from "./multiple-view";

type Draft = {
  name: string;
  growth: string[];
  terminal_growth: string;
  risk_free: string;
  equity_risk_premium: string;
  beta: string;
  extra_premium: string;
  discount_rate: string;
  base_fcf: string;
};

function toDraft(a: Assumptions): Draft {
  return {
    name: a.name,
    growth: a.growth.map((g) => fractionToPercent(g)),
    terminal_growth: fractionToPercent(a.terminal_growth),
    risk_free: fractionToPercent(a.risk_free),
    equity_risk_premium: fractionToPercent(a.equity_risk_premium),
    beta: String(toNumber(a.beta) ?? ""),
    extra_premium: fractionToPercent(a.extra_premium),
    discount_rate: fractionToPercent(a.discount_rate),
    base_fcf: a.base_fcf === null || a.base_fcf === undefined ? "" : String(a.base_fcf),
  };
}

/** Returns the assumptions, or the first field that is not a number. */
function fromDraft(d: Draft): Assumptions | string {
  const growth = d.growth.map(percentToFraction);
  if (growth.some((g) => g === null)) return `${d.name}: every growth year needs a number.`;
  const terminal = percentToFraction(d.terminal_growth);
  const rf = percentToFraction(d.risk_free);
  const erp = percentToFraction(d.equity_risk_premium);
  const extra = percentToFraction(d.extra_premium);
  const beta = Number(normaliseSeparators(d.beta));
  if (terminal === null || rf === null || erp === null || extra === null || !Number.isFinite(beta)) {
    return `${d.name}: terminal growth, risk-free rate, equity risk premium, extra premium and beta must be numbers.`;
  }
  const override = d.discount_rate.trim() === "" ? null : percentToFraction(d.discount_rate);
  if (d.discount_rate.trim() !== "" && override === null) return `${d.name}: the discount-rate override is not a number.`;
  const fcf = d.base_fcf.trim() === "" ? null : Number(normaliseSeparators(d.base_fcf));
  if (fcf !== null && !Number.isFinite(fcf)) return `${d.name}: starting free cash flow is not a number.`;
  return {
    name: d.name,
    years: growth.length,
    growth: growth as number[],
    terminal_growth: terminal,
    risk_free: rf,
    equity_risk_premium: erp,
    beta,
    extra_premium: extra,
    discount_rate: override,
    base_fcf: fcf,
  };
}

function Field({ label, value, onChange, suffix, wide }: { label: string; value: string; onChange: (v: string) => void; suffix?: string; wide?: boolean }) {
  return (
    <label className={cn("text-xs text-text-muted", wide && "col-span-2")}>
      {label}
      <span className="mt-0.5 flex items-center border border-border bg-surface">
        <input inputMode="decimal" value={value} onChange={(e) => onChange(e.target.value)} className="num w-full min-w-0 bg-transparent px-1.5 py-1 text-right text-sm text-text" />
        {suffix ? <span className="pr-1.5 text-text-muted">{suffix}</span> : null}
      </span>
    </label>
  );
}

function ScenarioEditor({ draft, selected, onChange, onSelect }: { draft: Draft; selected: boolean; onChange: (d: Draft) => void; onSelect: () => void }) {
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => onChange({ ...draft, [k]: v });
  return (
    <fieldset className={cn("space-y-3 border px-3 py-3", selected ? "border-accent" : "border-border")}>
      <legend className="px-1 text-sm font-semibold capitalize">{draft.name}</legend>
      <label className="flex items-center gap-2 text-xs">
        <input type="radio" name="selected-scenario" checked={selected} onChange={onSelect} />
        Measure margin of safety against this scenario
      </label>
      <div className="grid grid-cols-5 gap-1.5">
        {draft.growth.map((g, i) => (
          <Field key={i} label={`Year ${i + 1}`} suffix="%" value={g} onChange={(v) => set("growth", draft.growth.map((x, j) => (j === i ? v : x)))} />
        ))}
      </div>
      <p className="-mt-1 text-xs text-text-muted">Free-cash-flow growth in each year.</p>
      <div className="grid grid-cols-2 gap-1.5">
        <Field label="Terminal growth" suffix="%" value={draft.terminal_growth} onChange={(v) => set("terminal_growth", v)} />
        <Field label="Risk-free rate" suffix="%" value={draft.risk_free} onChange={(v) => set("risk_free", v)} />
        <Field label="Equity risk premium" suffix="%" value={draft.equity_risk_premium} onChange={(v) => set("equity_risk_premium", v)} />
        <Field label="Beta" value={draft.beta} onChange={(v) => set("beta", v)} />
        <Field label="Extra premium" suffix="%" value={draft.extra_premium} onChange={(v) => set("extra_premium", v)} />
        <Field label="Discount rate override" suffix="%" value={draft.discount_rate} onChange={(v) => set("discount_rate", v)} />
        <Field wide label="Starting free cash flow (blank = trailing twelve months)" value={draft.base_fcf} onChange={(v) => set("base_fcf", v)} />
      </div>
    </fieldset>
  );
}

function money(v: number | string | null | undefined, digits = 2): string | null {
  const n = toNumber(v);
  return n === null ? null : n.toLocaleString("en-GB", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function Results({ v }: { v: Valuation }) {
  const price = toNumber(v.price);
  const lo = toNumber(v.range_low);
  const hi = toNumber(v.range_high);
  const grid = gridMatrix(v.sensitivity);
  const gridValues = v.sensitivity.map((c) => toNumber(c.value)).filter((n): n is number => n !== null);
  const mid = grid.rowShifts.findIndex((s) => s === 0);
  const midC = grid.colShifts.findIndex((s) => s === 0);
  return (
    <div className="space-y-5">
      <Blueprint aria-labelledby="val-results" className="space-y-4 px-[22px] py-5">
        <PanelHeading kicker={`Per share, ${v.currency ?? "currency unknown"}`} title={<span id="val-results">Your scenarios</span>} />
        <p className="max-w-[70ch] text-sm text-text-muted">{v.note}</p>
        {v.warnings.map((w) => (
          <AlertMessage key={w} title="Note">{w}</AlertMessage>
        ))}
        <div className="overflow-x-auto border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-surface-muted text-left">
                <th scope="col" className="px-3 py-1.5 font-medium">Scenario</th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">Discount rate</th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">Enterprise value</th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">Value per share</th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">Margin of safety</th>
              </tr>
            </thead>
            <tbody>
              {sortScenarios(v.scenarios).map((s) => (
                <tr key={s.name} className="border-t border-border-soft align-top">
                  <th scope="row" className="px-3 py-1.5 text-left font-normal capitalize">
                    {s.name}
                    {s.selected ? <span className="ml-2 border border-accent px-1 text-xs normal-case">selected</span> : null}
                    <span className="block max-w-72 text-xs normal-case text-text-muted">{s.discount_rate_build}</span>
                  </th>
                  <td className="num px-2 py-1.5 text-right">{`${(Number(s.discount_rate) * 100).toFixed(2)}%`}</td>
                  <td className="num px-2 py-1.5 text-right">{money(s.enterprise_value, 0) ?? <Absent reason="missing: no enterprise value" />}</td>
                  <td className="num px-2 py-1.5 text-right font-semibold">{money(s.value_per_share.value) ?? <Absent reason={s.value_per_share.warnings?.[0] ?? "missing: not computable"} />}</td>
                  <td className="num px-2 py-1.5 text-right">
                    {s.selected ? (toNumber(s.margin_of_safety) !== null ? `${(Number(s.margin_of_safety) * 100).toFixed(1)}%` : <Absent reason="missing: price or value" />) : <span className="text-text-muted">only for the selected scenario</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {lo !== null && hi !== null ? (
          <p className="text-sm">
            The range your scenarios span: <span className="num font-semibold">{money(lo)}</span> to <span className="num font-semibold">{money(hi)}</span>
            {price !== null ? <> against a last close of <span className="num">{money(price)}</span>{v.price_date ? ` (${formatDate(v.price_date)})` : ""}</> : " (no stored price)"}.
          </p>
        ) : null}
      </Blueprint>

      <div className="grid gap-5 lg:grid-cols-2">
        <Blueprint aria-labelledby="val-grid" className="space-y-3 px-[22px] py-5">
          <PanelHeading kicker="Selected scenario" title={<span id="val-grid">Sensitivity: growth against discount rate</span>} />
          {grid.rowShifts.length === 0 ? (
            <p className="text-sm text-text-muted">No sensitivity grid is available for this scenario.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-sm">
                <caption className="sr-only">Value per share when growth and discount rate shift from the scenario&apos;s own figures</caption>
                <thead>
                  <tr>
                    <th scope="col" className="px-2 py-1 text-left text-xs font-normal text-text-muted">growth ↓ rate →</th>
                    {grid.colShifts.map((c) => (
                      <th key={c} scope="col" className="num px-2 py-1 text-right text-xs font-normal text-text-muted">{c > 0 ? "+" : ""}{(c * 100).toFixed(1)} pt</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {grid.rowShifts.map((r, i) => (
                    <tr key={r}>
                      <th scope="row" className="num px-2 py-1 text-left text-xs font-normal text-text-muted">{r > 0 ? "+" : ""}{(r * 100).toFixed(1)} pt</th>
                      {grid.colShifts.map((c, j) => {
                        const val = grid.at(i, j);
                        const aboveClose = val !== null && price !== null && val >= price;
                        return (
                          <td key={c} className={cn("num border border-border-soft px-2 py-1 text-right", i === mid && j === midC && "border-accent font-semibold")}>
                            {val !== null ? money(val) : <Absent reason="missing: not computable" />}
                            {val !== null && price !== null ? <span aria-hidden="true" className={cn("ml-1 text-xs", aboveClose ? "text-gain" : "text-loss")}>{aboveClose ? "▲" : "▼"}</span> : null}
                            {val !== null && price !== null ? <span className="sr-only">{aboveClose ? " at or above the last close" : " below the last close"}</span> : null}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-2 text-xs text-text-muted">▲ at or above the last close, ▼ below it. The outlined cell is your own scenario.{gridValues.length ? ` Cells span ${money(Math.min(...gridValues))} to ${money(Math.max(...gridValues))}.` : ""}</p>
            </div>
          )}
        </Blueprint>

        <Blueprint aria-labelledby="val-reverse" className="space-y-3 px-[22px] py-5">
          <PanelHeading kicker="What the price implies" title={<span id="val-reverse">Reverse DCF</span>} />
          {v.reverse_dcf && toNumber(v.reverse_dcf.value) !== null ? (
            <p className="max-w-[60ch] text-sm leading-relaxed">
              At the selected discount rate
              {toNumber(v.reverse_dcf_rate) !== null ? ` (${(Number(v.reverse_dcf_rate) * 100).toFixed(2)}%)` : ""}, the last close implies free-cash-flow growth of about{" "}
              <span className="num font-semibold">{(Number(v.reverse_dcf.value) * 100).toFixed(1)}%</span> a year for the explicit years. Compare it with the growth you entered.
            </p>
          ) : (
            <p className="text-sm text-text-muted">
              The implied growth cannot be solved. {v.reverse_dcf?.warnings?.[0] ?? "A price, free cash flow and net debt are needed."}
            </p>
          )}
          <PanelHeading kicker="Where the market has priced it" title={<span>Reference multiples</span>} />
          {v.reference.length === 0 ? (
            <p className="text-sm text-text-muted">No own-history or peer multiples are available.</p>
          ) : (
            <ul className="space-y-1.5 text-sm">
              {v.reference.map((r) => (
                <li key={`${r.method}-${r.basis}`}>
                  <span className="font-medium">{r.method}</span> <span className="text-text-muted">({r.basis})</span>:{" "}
                  {money(r.mid) !== null ? <span className="num">{money(r.low)} to {money(r.high)}, middle {money(r.mid)}</span> : <Absent reason={r.reason ?? "missing: no reference"} />}
                </li>
              ))}
            </ul>
          )}
        </Blueprint>
      </div>

      <Blueprint aria-labelledby="val-inputs" className="space-y-3 px-[22px] py-5">
        <PanelHeading kicker="Taken from filings, not typed" title={<span id="val-inputs">Inputs and normalized figures</span>} />
        <div className="grid gap-x-8 gap-y-1 sm:grid-cols-2">
          {[...v.inputs, ...v.normalized].map((i) => (
            <div key={i.key} className="flex flex-wrap justify-between gap-2 border-b border-border-soft py-1 text-sm">
              <span>
                {i.label}
                {i.detail ? <span className="block text-xs text-text-muted">{i.detail}</span> : null}
              </span>
              <span className="num">
                {money(i.value, 0) ?? <Absent reason="missing: not filed" />}
                {i.filing_url ? <> <SourceLink href={i.filing_url}>filing</SourceLink></> : null}
              </span>
            </div>
          ))}
        </div>
        <p className="text-xs text-text-muted">Method {v.method_version}.</p>
      </Blueprint>
    </div>
  );
}

function Saved({ ticker, items, onLoad, onSaved, currentRequest }: { ticker: string; items: SavedScenario[]; onLoad: (s: SavedScenario) => void; onSaved: () => void; currentRequest: () => { scenarios: Assumptions[]; selected: string | null } | string }) {
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function save() {
    const req = currentRequest();
    if (typeof req === "string") {
      setError(req);
      return;
    }
    if (!name.trim()) {
      setError("Name this set of scenarios first.");
      return;
    }
    setError(null);
    const { error: err } = await getBrowserApiClient().POST("/companies/{ticker}/valuation/saved", {
      params: { path: { ticker } },
      body: { name: name.trim(), note: note.trim() || null, request: req },
    });
    if (err) {
      setError("The scenarios could not be saved.");
      return;
    }
    setName("");
    setNote("");
    onSaved();
  }

  return (
    <Blueprint aria-labelledby="val-saved" className="space-y-3 px-[22px] py-5">
      <PanelHeading kicker="Each save is a new version; none is overwritten" title={<span id="val-saved">Saved scenarios</span>} />
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm">
          <span className="mb-1 block text-xs text-text-muted">Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} className="border border-border bg-surface px-2 py-1.5" />
        </label>
        <label className="min-w-56 flex-1 text-sm">
          <span className="mb-1 block text-xs text-text-muted">Why these assumptions (optional)</span>
          <input value={note} onChange={(e) => setNote(e.target.value)} className="w-full border border-border bg-surface px-2 py-1.5" />
        </label>
        <button type="button" onClick={save} className="border border-border px-3 py-1.5 text-sm hover:border-accent">Save scenarios</button>
      </div>
      {error ? <p role="alert" className="text-sm text-loss">{error}</p> : null}
      {items.length === 0 ? (
        <p className="text-sm text-text-muted">Nothing saved yet.</p>
      ) : (
        <ul className="divide-y divide-border-soft text-sm">
          {items.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-1.5">
              <span className="font-medium">{s.name} <span className="text-text-muted">v{s.version}</span></span>
              <span className="text-xs text-text-muted">{formatDate(s.created_at)} · method {s.method_version}{s.note ? ` · ${s.note}` : ""}</span>
              <button type="button" onClick={() => onLoad(s)} className="ml-auto text-accent-ink hover:underline">Load into editor</button>
            </li>
          ))}
        </ul>
      )}
    </Blueprint>
  );
}

export function DcfView({ ticker, onHeadline }: { ticker: string; onHeadline: (h: Headline | null) => void }) {
  const defaultsFetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/valuation/defaults", { params: { path: { ticker } } }), [ticker]);
  const { state: defaults } = useApiResource(defaultsFetcher);
  const savedFetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/valuation/saved", { params: { path: { ticker } } }), [ticker]);
  const { state: saved, reload: reloadSaved } = useApiResource(savedFetcher);

  // Until the owner edits or loads something, the editor shows the starting scenarios.
  const [edited, setDrafts] = useState<Draft[] | null>(null);
  const drafts = edited ?? (defaults.kind === "ok" ? sortScenarios(defaults.data).map(toDraft) : null);
  const [selected, setSelected] = useState<string>("base");
  const [version, setVersion] = useState<number | null>(null);
  const [result, setResult] = useState<Valuation | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const started = useRef(false);

  const request = useCallback((): { scenarios: Assumptions[]; selected: string | null } | string => {
    if (!drafts) return "The scenarios are not loaded yet.";
    const parsed: Assumptions[] = [];
    for (const d of drafts) {
      const a = fromDraft(d);
      if (typeof a === "string") return a;
      parsed.push(a);
    }
    return { scenarios: parsed, selected };
  }, [drafts, selected]);

  const run = useCallback(
    async (body?: { scenarios: Assumptions[]; selected: string | null }, ver: number | null = null) => {
      const req = body ?? request();
      if (typeof req === "string") {
        setProblem(req);
        return;
      }
      setRunning(true);
      setProblem(null);
      const { data, error } = await getBrowserApiClient().POST("/companies/{ticker}/valuation", { params: { path: { ticker } }, body: req });
      setRunning(false);
      if (error || !data) {
        setProblem("The valuation could not be computed. Check the figures and try again.");
        return;
      }
      setResult(data);
      const s = data.scenarios.find((x) => x.selected) ?? data.scenarios[0];
      onHeadline(s ? { method: "DCF", scenario: s.name, version: ver, value: toNumber(s.value_per_share.value), currency: data.currency ?? null, price: toNumber(data.price) } : null);
    },
    [request, ticker, onHeadline],
  );

  useEffect(() => {
    if (defaults.kind !== "ok" || started.current) return;
    started.current = true;
    void run({ scenarios: sortScenarios(defaults.data), selected: "base" });
  }, [defaults, run]);

  function load(s: SavedScenario) {
    const items = sortScenarios(s.assumptions);
    setDrafts(items.map(toDraft));
    const sel = s.selected ?? "base";
    setSelected(sel);
    setVersion(s.version);
    void run({ scenarios: items, selected: sel }, s.version);
  }

  return (
    <div className="flex flex-col gap-5">
      <Blueprint aria-labelledby="val-edit" className="space-y-4 px-[22px] py-5">
        <PanelHeading kicker="You set the assumptions; the app does the arithmetic" title={<span id="val-edit">Scenario editor</span>} />
        <ResourceGate state={defaults} label="starting scenarios">
          {() =>
            drafts ? (
              <>
                <div className="grid gap-4 lg:grid-cols-3">
                  {drafts.map((d, i) => (
                    <ScenarioEditor key={d.name} draft={d} selected={selected === d.name} onSelect={() => setSelected(d.name)} onChange={(nd) => setDrafts(drafts.map((x, j) => (j === i ? nd : x)))} />
                  ))}
                </div>
                <div className="flex items-center gap-3">
                  <button type="button" onClick={() => run(undefined, version)} disabled={running} className="border border-accent bg-surface px-4 py-1.5 text-sm font-semibold hover:bg-surface-muted disabled:opacity-50">
                    {running ? "Computing…" : "Compute values"}
                  </button>
                  {problem ? <p role="alert" className="text-sm text-loss">{problem}</p> : null}
                </div>
              </>
            ) : null
          }
        </ResourceGate>
      </Blueprint>
      {result ? <Results v={result} /> : null}
      <Saved ticker={ticker} items={saved.kind === "ok" ? saved.data : []} onLoad={load} onSaved={reloadSaved} currentRequest={request} />
    </div>
  );
}
