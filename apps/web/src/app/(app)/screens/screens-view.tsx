"use client";

import { LayoutGridIcon, ListIcon, LoaderCircleIcon, PlayIcon, PlusIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { StackedBar } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { DotStrip } from "@/components/insight/dot-strip";
import { ScoreMark } from "@/components/insight/score-mark";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { CompactRow } from "@/components/ui/compact-row";
import { Input } from "@/components/ui/input";
import { ReasonOnce } from "@/components/ui/reason-once";
import { ResponsiveTable } from "@/components/ui/responsive-table";
import { SkeletonRows } from "@/components/ui/skeleton";
import { getBrowserApiClient } from "@/lib/api/client";
import { companyHref } from "@/lib/company/company";
import { formatDate } from "@/lib/format";
import { useMarks } from "@/lib/insight/use-marks";
import { shortTicker } from "@/lib/portfolio/ticker";
import {
  activePreset,
  buildRequest,
  cellFor,
  describeDraft,
  describeFilter,
  funnel,
  MAX_FILTERS,
  metricInfo,
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
  type ScreenRow,
  type Universe,
} from "@/lib/screens/screens";
import { cn } from "@/lib/utils";

const SELECT =
  "h-9 rounded-control border border-border bg-surface-raised px-2 text-sm text-text focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none";

type Run = { kind: "idle" } | { kind: "running" } | { kind: "error"; title: string; detail: string } | { kind: "ok"; result: ScreenResult };
type Rules = { missing: MissingTreatment; reconciledOnly: boolean; valuationGateOnly: boolean };

/**
 * Screens (plan T4): filter the issuers the app holds filed statements for by profitability,
 * growth, cash conversion and leverage. Ideas first, then the conditions as sentences, then the
 * matches with their marks. The universe is only what you pick (never the market); a metric that
 * could not be computed is left out or marked Unknown, never read as zero, and every issuer left
 * out is counted with its reason.
 */
export function ScreensView() {
  const nextId = useRef(2);
  const [universe, setUniverse] = useState<Universe>("covered");
  const [drafts, setDrafts] = useState<FilterDraft[]>([{ id: 1, metric: "roic", op: "gte", value: "15" }]);
  const [rules, setRules] = useState<Rules>({ missing: "exclude", reconciledOnly: false, valuationGateOnly: false });
  const [errors, setErrors] = useState<string[]>([]);
  const [run, setRun] = useState<Run>({ kind: "idle" });

  const update = (id: number, patch: Partial<FilterDraft>) => setDrafts((d) => d.map((x) => (x.id === id ? { ...x, ...patch } : x)));

  const execute = async (next: { drafts?: FilterDraft[]; universe?: Universe } = {}) => {
    const { request, errors: found } = buildRequest({ universe: next.universe ?? universe, drafts: next.drafts ?? drafts, sortBy: null, ...rules });
    setErrors(found);
    if (!request) return;
    setRun({ kind: "running" });
    try {
      const r = await getBrowserApiClient().POST("/screens/run", { body: request });
      if (r.response.ok && r.data) setRun({ kind: "ok", result: r.data });
      else {
        const detail = r.error && typeof r.error === "object" && "detail" in r.error ? String((r.error as { detail: unknown }).detail) : "";
        setRun({ kind: "error", title: "The screen could not run", detail: detail || `The server answered ${r.response.status}. Try again in a moment.` });
      }
    } catch (err) {
      setRun({ kind: "error", title: "The app could not reach the server", detail: err instanceof Error ? err.message : String(err) });
    }
  };

  const applyPreset = (id: string) => {
    const preset = PRESETS.find((p) => p.id === id);
    if (!preset) return;
    const next = preset.filters.map((f) => ({ ...f, id: nextId.current++ }));
    setDrafts(next);
    void execute({ drafts: next });
  };

  const preset = activePreset(drafts);
  const rulesNote = [
    rules.missing === "exclude" ? "missing numbers leave the issuer out" : "missing numbers show as Unknown",
    rules.reconciledOnly ? "filed-statement facts only" : null,
    rules.valuationGateOnly ? "valuation-checked periods only" : null,
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Screens"
        description="Find companies whose filed numbers meet your conditions. Only issuers with stored statements can appear, so this screens what the app covers, not the whole market."
      />

      <section aria-labelledby="ideas-heading" className="space-y-3">
        <h2 id="ideas-heading" className="panel-title">
          Start from an idea
        </h2>
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {PRESETS.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                aria-pressed={preset === p.id}
                onClick={() => applyPreset(p.id)}
                className={cn(
                  "flex h-full w-full flex-col gap-1 rounded-card px-4 py-3.5 text-left transition-colors focus-visible:outline-2 focus-visible:outline-accent motion-reduce:transition-none",
                  preset === p.id ? "bg-accent-soft ring-1 ring-accent" : "bg-surface-muted hover:ring-1 hover:ring-border",
                )}
              >
                <span className="font-medium text-text">{p.label}</span>
                <span className="text-sm text-text-muted">{p.description}</span>
                <span className="mt-auto pt-2 text-sm font-medium text-accent-ink">{preset === p.id ? "Showing matches" : "Show matches"}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="builder-heading" className="blueprint space-y-5 px-5 py-5">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 id="builder-heading" className="panel-title">
            Your conditions
          </h2>
          <div role="group" aria-label="Screen across" className="inline-flex rounded-control bg-surface-muted p-0.5">
            {UNIVERSES.map((u) => (
              <button
                key={u.id}
                type="button"
                aria-pressed={universe === u.id}
                title={u.hint}
                onClick={() => setUniverse(u.id)}
                className={cn("rounded-control px-3 py-1 text-sm", universe === u.id ? "bg-surface-raised font-semibold text-text shadow-sm" : "text-text-muted hover:text-text")}
              >
                {u.label}
              </button>
            ))}
          </div>
        </div>
        <p className="-mt-3 text-sm text-text-muted">
          {UNIVERSES.find((u) => u.id === universe)?.hint}.{" "}
          <Link href={universe === "watchlist" ? "/watchlist" : universe === "holdings" ? "/holdings" : "/data-sources"} className="text-accent-ink hover:underline">
            {universe === "watchlist" ? "Edit the watchlist" : universe === "holdings" ? "See your holdings" : "See which issuers have data"}
          </Link>
        </p>

        <ul className="space-y-2" aria-label="Conditions">
          {drafts.map((d, i) => (
            <li key={d.id} className="flex flex-col gap-2 rounded-card bg-surface-muted px-3.5 py-2.5 md:flex-row md:items-center md:justify-between">
              <p className="text-sm">
                <span className="text-text-muted">{i === 0 ? "Where " : "and "}</span>
                <span className="font-medium">{describeDraft(d)}</span>
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <select aria-label={`Measure for condition ${i + 1}`} value={d.metric} onChange={(e) => update(d.id, { metric: e.target.value as MetricId })} className={SELECT}>
                  {SCREEN_METRICS.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label}
                    </option>
                  ))}
                </select>
                <select aria-label={`Comparison for condition ${i + 1}`} value={d.op} onChange={(e) => update(d.id, { op: e.target.value as FilterOp })} className={SELECT}>
                  {(Object.keys(OP_LABEL) as FilterOp[]).map((op) => (
                    <option key={op} value={op}>
                      {OP_LABEL[op]}
                    </option>
                  ))}
                </select>
                <span className="flex items-center gap-1">
                  <Input aria-label={`Value for condition ${i + 1}`} inputMode="decimal" value={d.value} onChange={(e) => update(d.id, { value: e.target.value })} className="w-20 text-right" />
                  <span className="w-3 text-sm text-text-muted">{unitSuffix(d.metric)}</span>
                </span>
                <Button type="button" variant="ghost" size="sm" aria-label={`Remove condition ${i + 1}`} onClick={() => setDrafts((all) => all.filter((x) => x.id !== d.id))}>
                  <XIcon aria-hidden="true" />
                </Button>
              </div>
            </li>
          ))}
        </ul>

        <div className="flex flex-wrap items-center gap-3">
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
          {drafts.length >= MAX_FILTERS ? <span className="text-xs text-text-muted">Up to {MAX_FILTERS} conditions.</span> : null}
        </div>

        <details className="rounded-card bg-surface-muted px-3.5 py-2.5 text-sm">
          <summary className="cursor-pointer">
            <span className="font-medium">Data rules</span> <span className="text-text-muted">{rulesNote}</span>
          </summary>
          <div className="mt-3 space-y-3">
            <fieldset className="space-y-1.5">
              <legend className="font-medium">When a number is missing</legend>
              {(
                [
                  ["exclude", "Leave the issuer out and count it", "A metric that could not be computed is never read as zero."],
                  ["keep_unknown", "Keep the issuer and mark it Unknown", "Useful to see what a missing number hides."],
                ] as const
              ).map(([value, label, hint]) => (
                <label key={value} className="flex items-start gap-2">
                  <input type="radio" name="missing" checked={rules.missing === value} onChange={() => setRules((r) => ({ ...r, missing: value }))} className="mt-1" />
                  <span>
                    {label}
                    <span className="block text-xs text-text-muted">{hint}</span>
                  </span>
                </label>
              ))}
            </fieldset>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={rules.reconciledOnly} onChange={(e) => setRules((r) => ({ ...r, reconciledOnly: e.target.checked }))} />
              Use only facts that match the filed statement
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={rules.valuationGateOnly} onChange={(e) => setRules((r) => ({ ...r, valuationGateOnly: e.target.checked }))} />
              Keep only periods that passed the valuation checks
            </label>
          </div>
        </details>

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
          Show matches
        </Button>
      </section>

      <Results run={run} />
    </div>
  );
}

function metricText(row: ScreenRow, metric: MetricId, r: ScreenResult) {
  const c = cellFor(row, metric, r);
  return c.unknown ? <span className="rounded-control bg-surface-muted px-1.5 text-xs text-text-muted">Unknown</span> : c.text;
}

/** Where the matches sit on the first condition's measure, with the threshold drawn in. */
/** The funnel's parts are outcomes, not identities: matches in the accent, the rest in quiet greys. */
const FUNNEL_FILL: Record<string, string> = {
  matched: "bg-accent",
  filtered_out: "bg-chart-benchmark",
  no_data: "bg-unconfirmed",
  no_period: "bg-chart-unknown",
};

function FirstConditionStrip({ r }: { r: ScreenResult }) {
  const f = r.filters[0];
  if (!f || r.rows.length < 3) return null;
  const info = metricInfo(f.metric);
  const asPct = info?.kind !== "multiple";
  const fmt = (v: number) => (asPct ? `${(v * 100).toFixed(0)}%` : `${v.toFixed(1)}×`);
  return (
    <DotStrip
      label={`${info?.label ?? f.metric} across the matches`}
      format={fmt}
      refs={[{ label: `Your condition, ${OP_LABEL[f.op]}`, value: Number(f.value), dashed: true }]}
      dots={r.rows.map((row) => {
        const raw = row.metrics[f.metric];
        const n = raw === null || raw === undefined ? null : Number(raw);
        return { key: row.ticker, label: shortTicker(row.ticker), value: n !== null && Number.isFinite(n) ? n : null };
      })}
    />
  );
}

function Results({ run }: { run: Run }) {
  const [view, setView] = useState<"list" | "cards">("list");
  const rows = run.kind === "ok" ? run.result.rows : [];
  const marks = useMarks(rows.map((r) => r.ticker));

  if (run.kind === "idle") {
    return (
      <EmptyState title="Pick an idea or set your conditions">
        Every issuer in the universe is counted in the result, matched or not, so you can see what each condition removed.
      </EmptyState>
    );
  }
  if (run.kind === "running") {
    return (
      <section aria-busy="true" aria-label="Running the screen" className="blueprint px-5 py-5">
        <SkeletonRows rows={5} />
      </section>
    );
  }
  if (run.kind === "error") return <AlertMessage title={run.title}>{run.detail}</AlertMessage>;

  const r = run.result;
  const steps = funnel(r);
  const matchedLine = r.matched > r.shown ? `Showing the first ${r.shown} of ${r.matched} matches.` : null;
  const used = new Set(r.filters.map((f) => f.metric));
  const shownMetrics = [...SCREEN_METRICS].sort((a, b) => Number(used.has(b.id)) - Number(used.has(a.id)));
  const mark = (row: ScreenRow, size: "row" | "sidebar") => {
    const petals = marks.petals.get(row.ticker);
    return petals ? (
      <ScoreMark petals={petals} size={size} label={row.name ?? row.ticker} className="shrink-0" />
    ) : (
      <span aria-hidden="true" className={cn("shrink-0 rounded-mark bg-chart-track", size === "row" ? "size-6" : "size-[120px]")} />
    );
  };

  return (
    <section aria-labelledby="results-heading" className="flex flex-col gap-5">
      <div className="blueprint space-y-4 px-5 py-5">
        <div>
          <h2 id="results-heading" className="page-title mb-1 text-[26px] md:text-[28px]">
            {r.matched} of {r.universe_size} issuers match
          </h2>
          <p className="max-w-[72ch] text-sm text-text-muted">
            {r.filters.length > 0 ? `${r.filters.map(describeFilter).join(", ")}.` : "No conditions, so every issuer with a computable period matches."} {r.universe_definition}
          </p>
        </div>
        <StackedBar title="Where each issuer ended up" segments={steps.map((s) => ({ label: s.label, value: s.value, fill: FUNNEL_FILL[s.id] }))} />
        <FirstConditionStrip r={r} />
      </div>

      {r.rows.length === 0 ? (
        <EmptyState title="No issuer matches">Loosen a condition, or open Data rules and keep issuers with a missing number to see what it hides.</EmptyState>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-text-muted">{matchedLine}</p>
            <div role="group" aria-label="Show matches as" className="inline-flex rounded-control bg-surface-muted p-0.5">
              {(
                [
                  ["list", "List", ListIcon],
                  ["cards", "Cards", LayoutGridIcon],
                ] as const
              ).map(([id, label, Icon]) => (
                <button
                  key={id}
                  type="button"
                  aria-pressed={view === id}
                  onClick={() => setView(id)}
                  className={cn("inline-flex items-center gap-1.5 rounded-control px-2.5 py-1 text-sm", view === id ? "bg-surface-raised font-semibold text-text shadow-sm" : "text-text-muted hover:text-text")}
                >
                  <Icon aria-hidden="true" className="size-4" />
                  {label}
                </button>
              ))}
            </div>
          </div>

          {view === "cards" ? (
            <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {r.rows.map((row) => (
                <li key={row.ticker}>
                  <Link
                    href={companyHref(row.ticker)}
                    className="flex h-full flex-col items-center gap-3 rounded-card bg-surface-muted px-4 py-4 text-center hover:ring-1 hover:ring-border focus-visible:outline-2 focus-visible:outline-accent"
                  >
                    {mark(row, "sidebar")}
                    <span>
                      <span className="block font-medium">{row.name ?? shortTicker(row.ticker)}</span>
                      <span className="block text-xs text-text-muted">
                        {shortTicker(row.ticker)}, {formatDate(row.period_end)}
                      </span>
                    </span>
                    <dl className="grid w-full grid-cols-2 gap-x-3 gap-y-1 text-left text-xs">
                      {shownMetrics
                        .filter((m) => used.has(m.id))
                        .map((m) => (
                          <div key={m.id} className="contents">
                            <dt className="text-text-muted">{m.label}</dt>
                            <dd className="num text-right font-medium">{metricText(row, m.id, r)}</dd>
                          </div>
                        ))}
                    </dl>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <ResponsiveTable
              rows={r.rows}
              rowKey={(row) => row.ticker}
              caption="Issuers that match the screen; the measures your conditions use come first"
              csvName="screen-matches.csv"
              columns={[
                {
                  key: "issuer",
                  header: "Company",
                  csv: (row) => row.name ?? row.ticker,
                  cell: (row) => (
                    <span className="flex items-center gap-2.5">
                      {mark(row, "row")}
                      <span className="min-w-0">
                        <Link href={companyHref(row.ticker)} className="block truncate font-medium hover:text-accent-ink hover:underline">
                          {row.name ?? shortTicker(row.ticker)}
                        </Link>
                        <span className="block text-xs text-text-muted">{shortTicker(row.ticker)}</span>
                      </span>
                    </span>
                  ),
                },
                {
                  key: "period",
                  header: "Period",
                  csv: (row) => row.period_end,
                  cell: (row) => (
                    <span className="text-xs">
                      {formatDate(row.period_end)}
                      <span className="block text-text-muted">{row.basis}</span>
                    </span>
                  ),
                },
                ...shownMetrics.map((m) => ({
                  key: m.id,
                  numeric: true,
                  header: <span className={used.has(m.id) ? "font-semibold text-text" : undefined}>{m.label}</span>,
                  csv: (row: ScreenRow) => {
                    const v = row.metrics[m.id];
                    return v === null || v === undefined ? null : Number(v);
                  },
                  cell: (row: ScreenRow) => <span className={used.has(m.id) ? "font-medium" : "text-text-muted"}>{metricText(row, m.id, r)}</span>,
                })),
              ]}
              compact={(row) => (
                <CompactRow
                  avatar={mark(row, "row")}
                  name={row.name ?? shortTicker(row.ticker)}
                  symbol={shortTicker(row.ticker)}
                  primary={r.filters[0] ? metricText(row, r.filters[0].metric, r) : formatDate(row.period_end)}
                  secondary={r.filters.slice(1, 3).map((f) => `${metricInfo(f.metric)?.label ?? f.metric}: ${cellFor(row, f.metric, r).text}`)}
                  href={companyHref(row.ticker)}
                />
              )}
            />
          )}
        </div>
      )}

      {r.excluded.length > 0 ? (
        <details className="rounded-card bg-surface-muted px-4 py-3 text-sm">
          <summary className="cursor-pointer font-medium">
            Why {r.universe_size - r.matched} issuer{r.universe_size - r.matched === 1 ? " was" : "s were"} left out
          </summary>
          <ul className="mt-2 space-y-1.5 text-xs">
            {r.excluded.map((g) => (
              <li key={g.reason}>
                <span className="num font-medium">{g.count}</span> <span>{g.reason}</span>
                <span className="block text-text-muted">
                  {g.tickers.map(shortTicker).join(", ")}
                  {g.count > g.tickers.length ? `, and ${g.count - g.tickers.length} more` : ""}
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <ReasonOnce reasons={r.caveats} title="What this screen leaves out" />
    </section>
  );
}
