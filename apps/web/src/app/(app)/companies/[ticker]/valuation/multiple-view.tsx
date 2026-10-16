"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Absent, ResourceGate } from "@/components/company/primitives";
import { CalcChain } from "@/components/insight/calc-chain";
import { ChartWithFooter } from "@/components/insight/chart-footer";
import { ProjectionChart } from "@/components/insight/projection-chart";
import { VerdictLine } from "@/components/insight/verdict-line";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatCompact } from "@/lib/charts/format";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { num } from "@/lib/insight/insight";
import {
  fromMultipleDraft,
  projection,
  setPerYear,
  sortMultiple,
  toMultipleDraft,
  withYears,
  type MultipleAssumptions,
  type MultipleDraft,
  type MultipleValuation,
  type PathKey,
  type SavedMultiple,
} from "@/lib/insight/multiple";
import { cn } from "@/lib/utils";

export type Headline = { method: string; scenario: string; version: number | null; value: number | null; currency: string | null; price: number | null };

const pctText = (v: unknown, d = 1) => {
  const n = num(v as number | string | null);
  return n === null ? null : `${(n * 100).toFixed(d)}%`;
};
const moneyText = (v: unknown, d = 2) => {
  const n = num(v as number | string | null);
  return n === null ? null : n.toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d });
};

function Input({ label, value, onChange, suffix, className }: { label: string; value: string; onChange: (v: string) => void; suffix?: string; className?: string }) {
  return (
    <label className={cn("text-xs text-text-muted", className)}>
      {label}
      <span className="mt-0.5 flex items-center border border-border bg-surface">
        <input inputMode="decimal" value={value} onChange={(e) => onChange(e.target.value)} className="num w-full min-w-0 bg-transparent px-1.5 py-1 text-right text-sm text-text" />
        {suffix ? <span className="pr-1.5 text-text-muted">{suffix}</span> : null}
      </span>
    </label>
  );
}

/** The company's own history and the peer value beside an input (the seeds the defaults used). */
function SeedNote({ v, k, unit }: { v: MultipleValuation | null; k: string; unit: "pct" | "x" }) {
  const s = v?.seeds.find((x) => x.key === k);
  if (!s) return null;
  const f = (x: unknown) => (unit === "pct" ? pctText(x) : num(x as number) !== null ? `${num(x as number)!.toFixed(1)}×` : null);
  return (
    <p className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-text-muted">
      <span>
        Company: <span className="num text-text">{f(s.company) ?? "—"}</span>
        {s.company_basis ? ` (${s.company_basis})` : ""}
      </span>
      <span>
        Peers: <span className="num text-text">{f(s.peer) ?? "—"}</span>
        {s.peer_basis ? ` (${s.peer_basis})` : " (none comparable)"}
      </span>
    </p>
  );
}

function PathRow({ label, k, d, seed, v, onChange }: { label: string; k: PathKey; d: MultipleDraft; seed: string; v: MultipleValuation | null; onChange: (d: MultipleDraft) => void }) {
  const years = Number(d.years) || 5;
  const perYear = d[k].length > 1;
  return (
    <div className="border-t border-border-soft pt-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm">{label}</span>
        <label className="flex items-center gap-1.5 text-xs text-text-muted">
          <input type="checkbox" checked={perYear} onChange={(e) => onChange({ ...d, [k]: setPerYear(d[k], e.target.checked, years) })} />
          Year by year
        </label>
      </div>
      <div className={cn("mt-1 grid gap-1.5", perYear ? "grid-cols-5" : "grid-cols-2")}>
        {d[k].map((x, i) => (
          <Input key={i} label={perYear ? `Year ${i + 1}` : "Every year"} suffix="%" value={x} onChange={(nv) => onChange({ ...d, [k]: d[k].map((y, j) => (j === i ? nv : y)) })} />
        ))}
      </div>
      <SeedNote v={v} k={seed} unit="pct" />
    </div>
  );
}

function Editor({ d, v, onChange }: { d: MultipleDraft; v: MultipleValuation | null; onChange: (d: MultipleDraft) => void }) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
        <Input label="Horizon (years)" value={d.years} onChange={(x) => onChange(Number.isInteger(Number(x)) && Number(x) >= 3 && Number(x) <= 10 ? withYears(d, Number(x)) : { ...d, years: x })} />
        <Input label="Starting revenue (blank = latest)" value={d.baseRevenue} onChange={(x) => onChange({ ...d, baseRevenue: x })} className="sm:col-span-2" />
      </div>
      <PathRow label="Revenue growth" k="growth" seed="revenue_growth" d={d} v={v} onChange={onChange} />
      <PathRow label="Net margin" k="margin" seed="net_margin" d={d} v={v} onChange={onChange} />
      <PathRow label="Diluted share change (negative = buybacks)" k="share" seed="share_change" d={d} v={v} onChange={onChange} />
      <div className="border-t border-border-soft pt-2">
        <Input label="Exit P/E at the end of the horizon" value={d.exitPe} onChange={(x) => onChange({ ...d, exitPe: x })} suffix="×" className="block max-w-48" />
        <SeedNote v={v} k="exit_pe" unit="x" />
      </div>
      <div className="grid grid-cols-2 gap-1.5 border-t border-border-soft pt-2 sm:grid-cols-5">
        <Input label="Risk-free rate" suffix="%" value={d.riskFree} onChange={(x) => onChange({ ...d, riskFree: x })} />
        <Input label="Equity risk premium" suffix="%" value={d.erp} onChange={(x) => onChange({ ...d, erp: x })} />
        <Input label="Beta" value={d.beta} onChange={(x) => onChange({ ...d, beta: x })} />
        <Input label="Extra premium" suffix="%" value={d.extra} onChange={(x) => onChange({ ...d, extra: x })} />
        <Input label="Rate override" suffix="%" value={d.discountRate} onChange={(x) => onChange({ ...d, discountRate: x })} />
      </div>
    </div>
  );
}

function Results({ v }: { v: MultipleValuation }) {
  const [measure, setMeasure] = useState<"earnings" | "revenue">("earnings");
  const sel = v.scenarios.find((s) => s.selected) ?? v.scenarios[0];
  const proj = projection(v, measure);
  const price = num(v.price);
  return (
    <div className="space-y-5">
      <Blueprint className="space-y-3 px-[22px] py-5">
        <PanelHeading kicker={`Multiple path · per share, ${v.currency ?? "currency unknown"}`} title="Scenario values" />
        <p className="max-w-[70ch] text-sm text-text-muted">{v.note}</p>
        {v.warnings.map((w) => (
          <AlertMessage key={w} title="Note">
            {w}
          </AlertMessage>
        ))}
        <div className="overflow-x-auto border border-border">
          <table className="w-full text-sm">
            <caption className="sr-only">Multiple-path value per share by scenario</caption>
            <thead>
              <tr className="bg-surface-muted text-left text-xs text-text-muted">
                <th scope="col" className="px-3 py-1.5 font-medium">Scenario</th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">Exit price</th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">Discount rate</th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">Value per share today</th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">Against the price</th>
              </tr>
            </thead>
            <tbody>
              {sortMultiple(v.scenarios).map((s) => (
                <tr key={s.name} className="border-t border-border-soft align-top">
                  <th scope="row" className="px-3 py-1.5 text-left font-normal capitalize">
                    {s.name}
                    {s.selected ? <span className="ml-2 border border-accent px-1 text-[11px] normal-case">selected</span> : null}
                    <span className="block max-w-72 text-[11px] normal-case text-text-muted">{s.discount_rate_build}</span>
                  </th>
                  <td className="num px-2 py-1.5 text-right">{moneyText(s.exit_price) ?? <Absent reason="missing: not computable" />}</td>
                  <td className="num px-2 py-1.5 text-right">{pctText(s.discount_rate, 2)}</td>
                  <td className="num px-2 py-1.5 text-right font-semibold">{moneyText(s.value_per_share.value) ?? <Absent reason={s.value_per_share.warnings?.[0] ?? "missing: not computable"} />}</td>
                  <td className="num px-2 py-1.5 text-right">{num(s.upside) === null ? "—" : `${num(s.upside)! >= 0 ? "+" : ""}${pctText(s.upside)}`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-sm">
          Range {moneyText(v.range_low) ?? "—"} to {moneyText(v.range_high) ?? "—"}
          {price !== null ? ` against a last close of ${moneyText(price)}${v.price_date ? ` (${formatDate(v.price_date)})` : ""}` : " (no stored price)"}. Method {v.method} {v.method_version}.
        </p>
      </Blueprint>

      <Blueprint className="space-y-3 px-[22px] py-5">
        <PanelHeading
          kicker={`${v.currency ?? ""} · filed years, then each scenario`}
          title="Projection"
          action={
            <div role="group" aria-label="Measure" className="flex border border-border text-xs">
              {(["earnings", "revenue"] as const).map((m) => (
                <button key={m} type="button" aria-pressed={measure === m} onClick={() => setMeasure(m)} className={cn("px-2.5 py-1 capitalize", measure === m ? "bg-accent-soft text-accent-ink" : "text-text-muted")}>
                  {m}
                </button>
              ))}
            </div>
          }
        />
        <ChartWithFooter
          chart={<ProjectionChart past={proj.past} scenarios={proj.scenarios} format={(n) => formatCompact(n)} label={`${measure === "earnings" ? "Net income" : "Revenue"}: filed and projected`} />}
          table={
            <table className="w-full text-sm">
              <caption className="sr-only">Selected scenario by year</caption>
              <thead>
                <tr className="bg-surface-muted text-xs text-text-muted">
                  {["Year", "Revenue", "Growth", "Net margin", "Earnings", "Shares", "EPS"].map((h) => (
                    <th key={h} scope="col" className="px-2 py-1.5 text-right font-medium first:text-left">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sel?.years.map((y) => (
                  <tr key={y.year} className="border-t border-border-soft">
                    <th scope="row" className="px-2 py-1 text-left font-normal">Year {y.year}</th>
                    <td className="num px-2 py-1 text-right">{formatCompact(num(y.revenue) ?? 0)}</td>
                    <td className="num px-2 py-1 text-right">{pctText(y.growth)}</td>
                    <td className="num px-2 py-1 text-right">{pctText(y.net_margin)}</td>
                    <td className="num px-2 py-1 text-right">{formatCompact(num(y.earnings) ?? 0)}</td>
                    <td className="num px-2 py-1 text-right">{formatCompact(num(y.shares) ?? 0)}</td>
                    <td className="num px-2 py-1 text-right">{moneyText(y.eps)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          }
          dataset="Filed annual revenue and net income; scenario paths as computed"
          source="SEC XBRL filings, your assumptions"
          learnHref="/methods/multiple_path"
        />
      </Blueprint>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Blueprint className="space-y-3 px-[22px] py-5">
          <PanelHeading kicker={`${sel?.name ?? ""} scenario, step by step`} title="How the value is calculated" />
          {sel ? <CalcChain steps={sel.chain} currency={v.currency} caption={`Calculation chain for the ${sel.name} scenario`} /> : null}
        </Blueprint>
        <Blueprint className="space-y-3 px-[22px] py-5">
          <PanelHeading kicker="What today's price implies" title="Reverse solve" />
          <VerdictLine verdict={num(v.reverse.exit_pe) === null ? "unknown" : "neutral"}>
            {num(v.reverse.exit_pe) !== null ? (
              <>
                Holding the {v.reverse.scenario ?? "selected"} scenario&apos;s growth and margins, the last close implies an exit P/E of{" "}
                <span className="num font-semibold">{num(v.reverse.exit_pe)!.toFixed(1)}×</span>.
              </>
            ) : (
              <>The implied exit P/E cannot be solved: {v.reverse.exit_pe_reason ?? "a price is needed"}.</>
            )}
          </VerdictLine>
          <VerdictLine verdict={num(v.reverse.growth) === null ? "unknown" : "neutral"}>
            {num(v.reverse.growth) !== null ? (
              <>
                Holding its exit P/E and margins, the price implies revenue growth of <span className="num font-semibold">{pctText(v.reverse.growth)}</span> a year.
              </>
            ) : (
              <>The implied growth cannot be solved: {v.reverse.growth_reason ?? "a price is needed"}.</>
            )}
          </VerdictLine>
          <p className="text-xs text-text-muted">The DCF tab shows the same question answered with free cash flow (reverse DCF).</p>
        </Blueprint>
      </div>
    </div>
  );
}

function Saved({ ticker, items, onLoad, onSaved, currentRequest }: { ticker: string; items: SavedMultiple[]; onLoad: (s: SavedMultiple) => void; onSaved: () => void; currentRequest: () => { scenarios: MultipleAssumptions[]; selected: string | null } | string }) {
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function save() {
    const req = currentRequest();
    if (typeof req === "string") return setError(req);
    if (!name.trim()) return setError("Name this set of scenarios first.");
    setError(null);
    const { error: err } = await getBrowserApiClient().POST("/companies/{ticker}/valuation/multiple/saved", {
      params: { path: { ticker } },
      body: { name: name.trim(), note: note.trim() || null, request: req },
    });
    if (err) return setError("The scenarios could not be saved.");
    setName("");
    setNote("");
    onSaved();
  }
  return (
    <Blueprint className="space-y-3 px-[22px] py-5">
      <PanelHeading kicker="Each save is a new version; none is overwritten" title="Saved multiple-path scenarios" />
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm">
          <span className="mb-1 block text-xs text-text-muted">Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} className="border border-border bg-surface px-2 py-1.5" />
        </label>
        <label className="min-w-56 flex-1 text-sm">
          <span className="mb-1 block text-xs text-text-muted">Why these assumptions (optional)</span>
          <input value={note} onChange={(e) => setNote(e.target.value)} className="w-full border border-border bg-surface px-2 py-1.5" />
        </label>
        <button type="button" onClick={() => void save()} className="border border-border px-3 py-1.5 text-sm hover:border-accent">
          Save scenarios
        </button>
      </div>
      {error ? (
        <p role="alert" className="text-sm text-loss">
          {error}
        </p>
      ) : null}
      {items.length === 0 ? (
        <p className="text-sm text-text-muted">Nothing saved yet.</p>
      ) : (
        <ul className="divide-y divide-border-soft text-sm">
          {items.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-1.5">
              <span className="font-medium">
                {s.name} <span className="text-text-muted">v{s.version}</span>
              </span>
              <span className="text-xs text-text-muted">
                {formatDate(s.created_at)} · method {s.method_version}
                {s.note ? ` · ${s.note}` : ""}
              </span>
              <button type="button" onClick={() => onLoad(s)} className="ml-auto text-accent-ink hover:underline">
                Load into editor
              </button>
            </li>
          ))}
        </ul>
      )}
    </Blueprint>
  );
}

export function MultipleView({ ticker, onHeadline }: { ticker: string; onHeadline: (h: Headline | null) => void }) {
  const defaultsFetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/valuation/multiple/defaults", { params: { path: { ticker } } }), [ticker]);
  const { state: defaults } = useApiResource(defaultsFetcher);
  const savedFetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/valuation/multiple/saved", { params: { path: { ticker } } }), [ticker]);
  const { state: saved, reload: reloadSaved } = useApiResource(savedFetcher);
  const driftFetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/valuation/multiple/drift", { params: { path: { ticker } } }), [ticker]);
  const { state: drift } = useApiResource(driftFetcher);

  const [edited, setDrafts] = useState<MultipleDraft[] | null>(null);
  const drafts = edited ?? (defaults.kind === "ok" ? sortMultiple(defaults.data).map(toMultipleDraft) : null);
  const [selected, setSelected] = useState("base");
  const [editing, setEditing] = useState("base");
  const [version, setVersion] = useState<number | null>(null);
  const [result, setResult] = useState<MultipleValuation | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const started = useRef(false);

  const request = useCallback((): { scenarios: MultipleAssumptions[]; selected: string | null } | string => {
    if (!drafts) return "The scenarios are not loaded yet.";
    const out: MultipleAssumptions[] = [];
    for (const d of drafts) {
      const a = fromMultipleDraft(d);
      if (typeof a === "string") return a;
      out.push(a);
    }
    return { scenarios: out, selected };
  }, [drafts, selected]);

  const run = useCallback(
    async (body?: { scenarios: MultipleAssumptions[]; selected: string | null }, ver: number | null = null) => {
      const req = body ?? request();
      if (typeof req === "string") return setProblem(req);
      setRunning(true);
      setProblem(null);
      const { data, error } = await getBrowserApiClient().POST("/companies/{ticker}/valuation/multiple", { params: { path: { ticker } }, body: req });
      setRunning(false);
      if (error || !data) return setProblem("The multiple-path value could not be computed. Check the figures and try again.");
      setResult(data);
      const s = data.scenarios.find((x) => x.selected) ?? data.scenarios[0];
      onHeadline(s ? { method: "Multiple path", scenario: s.name, version: ver, value: num(s.value_per_share.value), currency: data.currency ?? null, price: num(data.price) } : null);
    },
    [request, ticker, onHeadline],
  );

  useEffect(() => {
    if (defaults.kind !== "ok" || started.current) return;
    started.current = true;
    void run({ scenarios: sortMultiple(defaults.data), selected: "base" });
  }, [defaults, run]);

  function load(s: SavedMultiple) {
    const items = sortMultiple(s.assumptions);
    setDrafts(items.map(toMultipleDraft));
    const sel = s.selected ?? "base";
    setSelected(sel);
    setEditing(sel);
    setVersion(s.version);
    void run({ scenarios: items, selected: sel }, s.version);
  }

  const current = drafts?.find((d) => d.name === editing) ?? drafts?.[0] ?? null;
  const d = drift.kind === "ok" ? drift.data : null;
  return (
    <div className="flex flex-col gap-5">
      {d && (d.stale || d.drifting) ? (
        <AlertMessage tone="info" title={d.stale ? "Your saved multiple-path scenario is stale" : "Reported results have moved away from your path"}>
          {d.reasons.join(" ")}
        </AlertMessage>
      ) : null}
      <Blueprint className="space-y-4 px-[22px] py-5">
        <PanelHeading kicker="Revenue, margin and an exit multiple; the app does the arithmetic" title="Multiple-path editor" />
        <ResourceGate state={defaults} label="starting scenarios">
          {() =>
            drafts && current ? (
              <>
                <div role="tablist" aria-label="Scenario" className="flex flex-wrap items-center gap-1">
                  {drafts.map((x) => (
                    <button
                      key={x.name}
                      type="button"
                      role="tab"
                      aria-selected={editing === x.name}
                      onClick={() => setEditing(x.name)}
                      className={cn("border px-3 py-1 text-sm capitalize", editing === x.name ? "border-accent bg-accent-soft text-accent-ink" : "border-border text-text-muted")}
                    >
                      {x.name}
                      {selected === x.name ? <span className="ml-1 text-[11px] normal-case">(measured)</span> : null}
                    </button>
                  ))}
                  <label className="ml-auto flex items-center gap-2 text-xs">
                    <input type="radio" name="multiple-selected" checked={selected === current.name} onChange={() => setSelected(current.name)} />
                    Measure against the {current.name} scenario
                  </label>
                </div>
                <Editor d={current} v={result} onChange={(nd) => setDrafts(drafts.map((x) => (x.name === current.name ? nd : x)))} />
                <div className="flex items-center gap-3">
                  <button type="button" onClick={() => void run(undefined, version)} disabled={running} className="border border-accent bg-surface px-4 py-1.5 text-sm font-semibold hover:bg-surface-muted disabled:opacity-50">
                    {running ? "Computing…" : "Compute values"}
                  </button>
                  {problem ? (
                    <p role="alert" className="text-sm text-loss">
                      {problem}
                    </p>
                  ) : null}
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
