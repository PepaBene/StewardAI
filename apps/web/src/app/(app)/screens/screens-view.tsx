"use client";

import { LoaderCircleIcon, PlusIcon, PlayIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { StackedBar } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { BlueprintCorners } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import {
  buildRequest,
  cellFor,
  describeFilter,
  funnel,
  MAX_FILTERS,
  nextMetric,
  OP_LABEL,
  PRESETS,
  SCREEN_METRICS,
  UNIVERSES,
  unitSuffix,
  type FilterDraft,
  type FilterOp,
  type MetricId,
  type MissingTreatment,
  type ScreenResult,
  type Universe,
} from "@/lib/screens/screens";
import { cn } from "@/lib/utils";

const SELECT = "h-9 border border-border bg-surface-raised px-2 text-sm text-text focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none";

type Run = { kind: "idle" } | { kind: "running" } | { kind: "error"; title: string; detail: string } | { kind: "ok"; result: ScreenResult };

/**
 * Screens: filter the issuers the app holds filed statements for by profitability, growth, cash
 * conversion and leverage. The universe is only what you pick (never the market), a metric that
 * could not be computed is excluded or marked Unknown but never read as zero, and every issuer
 * left out is counted with its reason.
 */
export function ScreensView() {
  const nextId = useRef(2);
  const [universe, setUniverse] = useState<Universe>("covered");
  const [drafts, setDrafts] = useState<FilterDraft[]>([{ id: 1, metric: "roic", op: "gte", value: "15" }]);
  const [missing, setMissing] = useState<MissingTreatment>("exclude");
  const [reconciledOnly, setReconciledOnly] = useState(false);
  const [valuationGateOnly, setValuationGateOnly] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [run, setRun] = useState<Run>({ kind: "idle" });

  const update = (id: number, patch: Partial<FilterDraft>) => setDrafts((d) => d.map((x) => (x.id === id ? { ...x, ...patch } : x)));

  const applyPreset = (id: string) => {
    const preset = PRESETS.find((p) => p.id === id);
    if (!preset) return;
    setDrafts(preset.filters.map((f) => ({ ...f, id: nextId.current++ })));
    setErrors([]);
  };

  const execute = async () => {
    const { request, errors: found } = buildRequest({ universe, drafts, missing, sortBy: null, reconciledOnly, valuationGateOnly });
    setErrors(found);
    if (!request) return;
    setRun({ kind: "running" });
    try {
      const r = await getBrowserApiClient().POST("/screens/run", { body: request });
      if (r.response.ok && r.data) setRun({ kind: "ok", result: r.data });
      else {
        const detail = r.error && typeof r.error === "object" && "detail" in r.error ? String((r.error as { detail: unknown }).detail) : "";
        setRun({ kind: "error", title: `The screen failed (HTTP ${r.response.status})`, detail });
      }
    } catch (err) {
      setRun({ kind: "error", title: "Backend unreachable", detail: err instanceof Error ? err.message : String(err) });
    }
  };

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Research & Verification"
        title="Screens"
        description="Find issuers whose filed numbers meet your conditions. Only issuers with stored filed statements can appear, so this is a screen of what the app covers, not of the market."
      />

      <section aria-labelledby="builder-heading" className="blueprint space-y-4 px-5 py-5">
        <BlueprintCorners />
        <div className="flex flex-wrap items-end justify-between gap-3">
          <h2 id="builder-heading" className="text-[22px] leading-tight font-semibold">
            Conditions
          </h2>
          <div className="flex flex-wrap items-center gap-2 text-xs text-text-muted">
            <span>Start from:</span>
            {PRESETS.map((p) => (
              <button
                key={p.id}
                type="button"
                title={p.description}
                onClick={() => applyPreset(p.id)}
                className="border border-border px-2 py-1 text-text hover:bg-border-soft focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none"
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        <label className="block space-y-1 text-sm">
          <span className="block font-medium">Screen across</span>
          <select value={universe} onChange={(e) => setUniverse(e.target.value as Universe)} className={cn(SELECT, "w-full max-w-sm")}>
            {UNIVERSES.map((u) => (
              <option key={u.id} value={u.id}>
                {u.label}
              </option>
            ))}
          </select>
          <span className="block text-xs text-text-muted">
            {UNIVERSES.find((u) => u.id === universe)?.hint}{" "}
            {universe === "watchlist" ? (
              <Link href="/watchlist" className="text-accent-ink hover:underline">
                Edit the watchlist
              </Link>
            ) : universe === "holdings" ? (
              <Link href="/holdings" className="text-accent-ink hover:underline">
                See your holdings
              </Link>
            ) : (
              <Link href="/data-sources" className="text-accent-ink hover:underline">
                See which issuers have data
              </Link>
            )}
          </span>
        </label>

        <ul className="space-y-2" aria-label="Filters">
          {drafts.map((d, i) => (
            <li key={d.id} className="flex flex-wrap items-center gap-2">
              <span className="w-12 text-xs text-text-muted">{i === 0 ? "Where" : "and"}</span>
              <select
                aria-label={`Metric ${i + 1}`}
                value={d.metric}
                onChange={(e) => update(d.id, { metric: e.target.value as MetricId })}
                className={cn(SELECT, "min-w-[14rem]")}
              >
                {SCREEN_METRICS.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
              </select>
              <select aria-label={`Condition ${i + 1}`} value={d.op} onChange={(e) => update(d.id, { op: e.target.value as FilterOp })} className={SELECT}>
                {(Object.keys(OP_LABEL) as FilterOp[]).map((op) => (
                  <option key={op} value={op}>
                    {OP_LABEL[op]}
                  </option>
                ))}
              </select>
              <span className="flex items-center gap-1">
                <Input
                  aria-label={`Value ${i + 1}`}
                  inputMode="decimal"
                  value={d.value}
                  onChange={(e) => update(d.id, { value: e.target.value })}
                  className="w-24 text-right"
                />
                <span className="text-sm text-text-muted">{unitSuffix(d.metric)}</span>
              </span>
              <Button type="button" variant="ghost" size="sm" aria-label={`Remove filter ${i + 1}`} onClick={() => setDrafts((all) => all.filter((x) => x.id !== d.id))}>
                <XIcon aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={drafts.length >= MAX_FILTERS}
          onClick={() => setDrafts((all) => [...all, { id: nextId.current++, metric: nextMetric(all), op: "gte", value: "" }])}
        >
          <PlusIcon aria-hidden="true" />
          Add condition
        </Button>

        <fieldset className="space-y-1.5 text-sm">
          <legend className="font-medium">When a number is missing</legend>
          {(
            [
              ["exclude", "Leave the issuer out and count it", "A metric that could not be computed is never read as zero."],
              ["keep_unknown", "Keep the issuer and mark it Unknown", "Useful to see what a missing number hides."],
            ] as const
          ).map(([value, label, hint]) => (
            <label key={value} className="flex items-start gap-2">
              <input type="radio" name="missing" checked={missing === value} onChange={() => setMissing(value)} className="mt-1" />
              <span>
                {label}
                <span className="block text-xs text-text-muted">{hint}</span>
              </span>
            </label>
          ))}
        </fieldset>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={reconciledOnly} onChange={(e) => setReconciledOnly(e.target.checked)} />
          Use only facts that match the filed statement
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={valuationGateOnly} onChange={(e) => setValuationGateOnly(e.target.checked)} />
          Keep only periods that passed the valuation checks
        </label>

        {errors.length > 0 ? (
          <AlertMessage title="Fix these conditions to run the screen">
            <ul className="list-disc pl-5">
              {errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          </AlertMessage>
        ) : null}

        <Button type="button" onClick={() => void execute()} disabled={run.kind === "running"}>
          {run.kind === "running" ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <PlayIcon aria-hidden="true" />}
          Run screen
        </Button>
      </section>

      <Results run={run} />
    </div>
  );
}

function Results({ run }: { run: Run }) {
  if (run.kind === "idle") {
    return (
      <EmptyState title="No screen run yet">
        Set your conditions above and run the screen. Every issuer in the universe is counted in the result, matched or not.
      </EmptyState>
    );
  }
  if (run.kind === "running") {
    return (
      <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
        <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
        Running the screen…
      </p>
    );
  }
  if (run.kind === "error") return <AlertMessage title={run.title}>{run.detail}</AlertMessage>;

  const r = run.result;
  const steps = funnel(r);
  return (
    <section aria-labelledby="results-heading" className="blueprint space-y-4 px-5 py-5">
      <BlueprintCorners />
      <div>
        <h2 id="results-heading" className="text-[22px] leading-tight font-semibold">
          {r.matched} of {r.universe_size} issuers match
        </h2>
        <p className="max-w-[72ch] text-sm text-text-muted">
          {r.universe}: {r.universe_definition}{" "}
          {r.filters.length > 0 ? `Conditions: ${r.filters.map(describeFilter).join("; ")}.` : "No conditions, so every issuer with a computable period matches."}
        </p>
      </div>

      <div className="space-y-2">
        <StackedBar title="Where each issuer ended up" segments={steps.map((s) => ({ label: s.label, value: s.value }))} />
        <ul className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
          {steps.map((s) => (
            <li key={s.id}>
              <span className="num font-medium">{s.value}</span> <span className="text-text-muted">{s.label.toLowerCase()}</span>
            </li>
          ))}
        </ul>
      </div>

      {r.rows.length === 0 ? (
        <EmptyState title="No issuer matches">Loosen a condition, or switch to keeping issuers with a missing number to see what is hidden.</EmptyState>
      ) : (
        <div className="relative overflow-x-auto border border-border">
          <table className="w-full min-w-[56rem] text-sm">
            <caption className="sr-only">Issuers that match the screen. Columns used by a condition are marked.</caption>
            <thead className="bg-surface-muted">
              <tr className="border-b border-border text-xs text-text-muted">
                <th scope="col" className="px-3 py-2 text-left font-medium">Issuer</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">Period</th>
                {SCREEN_METRICS.map((m) => (
                  <th key={m.id} scope="col" className="px-3 py-2 text-right font-medium">
                    {m.label}
                    {r.filters.some((f) => f.metric === m.id) ? <span className="block text-[11px] font-normal text-accent-ink">in screen</span> : null}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {r.rows.map((row) => (
                <tr key={row.ticker} className="border-b border-border align-top last:border-0">
                  <th scope="row" className="px-3 py-2 text-left font-normal">
                    <Link href={`/holdings/${encodeURIComponent(row.ticker)}`} className="font-medium hover:text-accent-ink hover:underline">
                      {row.name ?? row.ticker}
                    </Link>
                    <span className="block font-mono text-xs text-text-muted">{row.ticker}</span>
                    <span className="mt-0.5 flex gap-2 text-xs">
                      <Link href={`/companies/${encodeURIComponent(row.ticker)}/financials`} className="text-text-muted hover:text-accent-ink hover:underline">Financials</Link>
                      <Link href={`/companies/${encodeURIComponent(row.ticker)}/ratios`} className="text-text-muted hover:text-accent-ink hover:underline">Ratios</Link>
                    </span>
                  </th>
                  <td className="num px-3 py-2 text-xs">
                    {formatDate(row.period_end)}
                    <span className="block text-text-muted">{row.basis}</span>
                  </td>
                  {SCREEN_METRICS.map((m) => {
                    const c = cellFor(row, m.id, r);
                    return (
                      <td key={m.id} className={cn("num px-3 py-2 text-right", c.unknown && "text-text-muted", c.filtered && !c.unknown && "font-medium")}>
                        {c.unknown ? <span className="border border-dashed border-border px-1 text-xs">Unknown</span> : c.text}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {r.matched > r.shown ? <p className="text-xs text-text-muted">Showing the first {r.shown} of {r.matched} matches.</p> : null}

      {r.excluded.length > 0 ? (
        <details className="text-sm">
          <summary className="cursor-pointer text-accent-ink hover:underline">
            Why {r.universe_size - r.matched} issuer{r.universe_size - r.matched === 1 ? " was" : "s were"} left out
          </summary>
          <ul className="mt-2 space-y-1.5 text-xs">
            {r.excluded.map((g) => (
              <li key={g.reason}>
                <span className="num font-medium">{g.count}</span> <span>{g.reason}</span>
                <span className="block text-text-muted">
                  {g.tickers.join(", ")}
                  {g.count > g.tickers.length ? `, and ${g.count - g.tickers.length} more` : ""}
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <ul className="space-y-0.5 text-xs text-text-muted">
        {r.caveats.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ul>
    </section>
  );
}
