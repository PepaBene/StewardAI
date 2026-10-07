"use client";

import { DownloadIcon, XIcon } from "lucide-react";
import { useCallback, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Absent, ResourceGate, SourceLink } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import {
  formatFigure,
  HEADLINE_KEYS,
  UNIT_LABEL,
  type StatementCell,
  type StatementColumn,
  type StatementLine,
  type StatementsView,
} from "@/lib/company/company";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

type Query = {
  basis: "standardized" | "reported";
  frequency: "annual" | "quarterly";
  view: "restated" | "original";
  units: "1" | "1000" | "1000000";
  transform: "none" | "common_size" | "yoy";
  per_share: boolean;
};

const DEFAULTS: Query = { basis: "standardized", frequency: "annual", view: "restated", units: "1000000", transform: "none", per_share: false };

type Picked = { line: StatementLine; column: StatementColumn; cell: StatementCell };

function Choice<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: readonly (readonly [T, string])[]; onChange: (v: T) => void }) {
  return (
    <fieldset className="min-w-0">
      <legend className="mb-1 text-xs text-text-muted">{label}</legend>
      <div className="flex border border-border">
        {options.map(([v, text]) => (
          <label
            key={v}
            className={cn(
              "cursor-pointer border-r border-border px-2.5 py-1 text-sm last:border-r-0 has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-focus/30",
              value === v ? "bg-surface-sunken font-semibold" : "text-text-muted hover:text-text",
            )}
          >
            <input type="radio" name={label} value={v} checked={value === v} onChange={() => onChange(v)} className="sr-only" />
            {text}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function CellView({ cell, line, onPick }: { cell: StatementCell | undefined; line: StatementLine; onPick: () => void }) {
  if (!cell) return <Absent reason="missing: not filed" />;
  const unit = cell.unit ?? line.unit;
  const text = formatFigure(cell.value, unit);
  if (text === null) return <Absent reason={cell.reason} />;
  const marks = [cell.derived ? "d" : "", cell.restated ? "r" : "", cell.reconciled === false ? "!" : ""].join("");
  return (
    <button
      type="button"
      onClick={onPick}
      className="num inline-flex items-baseline gap-1 px-1 text-right hover:bg-surface-sunken focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none"
      aria-label={`${text}${cell.derived ? ", derived" : ""}${cell.restated ? ", restated" : ""}${cell.reconciled === false ? ", does not reconcile" : ""}. Open source`}
    >
      {text}
      {marks ? <sup className="text-[10px] text-unconfirmed">{marks}</sup> : null}
    </button>
  );
}

function Inspector({ pick, onClose }: { pick: Picked; onClose: () => void }) {
  const { cell, line, column } = pick;
  const dims = (line.dimensions ?? []).filter((d) => d.column === column.id);
  return (
    <aside aria-label="Where this figure comes from" className="space-y-2 border border-border bg-surface px-4 py-3 text-sm lg:sticky lg:top-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-semibold">{line.label}</p>
          <p className="text-xs text-text-muted">{column.label} · period ending {formatDate(column.end)}</p>
        </div>
        <button type="button" onClick={onClose} aria-label="Close source" className="p-1 hover:bg-surface-sunken">
          <XIcon aria-hidden="true" className="size-4" />
        </button>
      </div>
      <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1">
        <dt className="text-text-muted">Value</dt>
        <dd className="num">{formatFigure(cell.value, cell.unit ?? line.unit) ?? <Absent reason={cell.reason} />}</dd>
        {cell.concept ? (
          <>
            <dt className="text-text-muted">Concept</dt>
            <dd className="font-mono text-xs break-all">{cell.concept}</dd>
          </>
        ) : null}
        {cell.form ? (
          <>
            <dt className="text-text-muted">Filing</dt>
            <dd>
              {cell.form}
              {cell.accession ? ` ${cell.accession}` : ""} <SourceLink href={cell.filing_url ?? cell.source_url}>Open filing</SourceLink>
            </dd>
          </>
        ) : null}
        {cell.known_at ? (
          <>
            <dt className="text-text-muted">Known at</dt>
            <dd>{formatDate(cell.known_at)}</dd>
          </>
        ) : null}
        {cell.derived ? (
          <>
            <dt className="text-text-muted">Derived</dt>
            <dd>{cell.derived}</dd>
          </>
        ) : null}
        {cell.restated ? (
          <>
            <dt className="text-text-muted">Originally</dt>
            <dd className="num">{formatFigure(cell.original_value, cell.unit ?? line.unit) ?? "unknown"}</dd>
          </>
        ) : null}
        {cell.reconciled === false ? (
          <>
            <dt className="text-text-muted">Check</dt>
            <dd className="text-loss">Does not reconcile with the filed total.</dd>
          </>
        ) : null}
        {cell.reason && cell.value !== null && cell.value !== undefined ? (
          <>
            <dt className="text-text-muted">Note</dt>
            <dd>{cell.reason}</dd>
          </>
        ) : null}
      </dl>
      {line.concepts && line.concepts.length > 0 ? (
        <p className="text-xs text-text-muted">Maps from: {line.concepts.join(", ")}</p>
      ) : null}
      {dims.length > 0 ? (
        <div>
          <p className="text-xs text-text-muted">Dimensional detail</p>
          <ul className="space-y-0.5 text-xs">
            {dims.map((d) => (
              <li key={Object.values(d.members).join("|")} className="flex justify-between gap-3">
                <span>{Object.values(d.members).join(" / ")}</span>
                <span className="num">{formatFigure(d.value, line.unit)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </aside>
  );
}

function Table({ view, onPick, headlineOnly }: { view: StatementsView; onPick: (p: Picked) => void; headlineOnly: boolean }) {
  return (
    <div className="space-y-6">
      {view.statements.map((st) => {
        const lines = headlineOnly ? st.lines.filter((l) => HEADLINE_KEYS.has(l.key) || l.kind === "total") : st.lines;
        return (
          <section key={st.statement} aria-labelledby={`st-${st.statement}`}>
            <h3 id={`st-${st.statement}`} className="mb-1 text-sm font-semibold">
              {st.label}
            </h3>
            <div className="overflow-x-auto border border-border">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="bg-surface-sunken">
                    <th scope="col" className="sticky left-0 z-10 min-w-56 bg-surface-sunken px-3 py-1.5 text-left font-medium">
                      {UNIT_LABEL[String(view.units)] ?? ""} {view.currency ?? ""}
                    </th>
                    {view.columns.map((c) => (
                      <th key={c.id} scope="col" className="px-2 py-1.5 text-right font-medium whitespace-nowrap" title={(c.notes ?? []).join(" ")}>
                        {c.label}
                        {c.notes && c.notes.length > 0 ? <sup className="text-unconfirmed"> *</sup> : null}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {lines.map((l) => (
                    <tr key={l.key} className={cn("border-t border-border-soft", l.kind === "total" && "font-semibold", l.kind === "residual" && "text-text-muted")}>
                      <th scope="row" className="sticky left-0 z-10 bg-surface px-3 py-1 text-left font-normal">
                        <span className={cn(l.kind === "total" ? "font-semibold" : "")}>{l.label}</span>
                      </th>
                      {view.columns.map((c) => (
                        <td key={c.id} className="px-2 py-1 text-right whitespace-nowrap">
                          <CellView cell={l.cells?.[c.id]} line={l} onPick={() => { const cell = l.cells?.[c.id]; if (cell) onPick({ line: l, column: c, cell }); }} />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        );
      })}
    </div>
  );
}

export function FinancialsView({ ticker }: { ticker: string }) {
  const [q, setQ] = useState<Query>(DEFAULTS);
  const [picked, setPicked] = useState<Picked | null>(null);
  const [headlineOnly, setHeadlineOnly] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const set = <K extends keyof Query>(k: K, v: Query[K]) => setQ((p) => ({ ...p, [k]: v }));

  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/companies/{ticker}/statements", { params: { path: { ticker }, query: q } }),
    [ticker, q],
  );
  const { state } = useApiResource(fetcher);

  async function exportCsv() {
    setExportError(null);
    const { data, error } = await getBrowserApiClient().GET("/companies/{ticker}/statements.csv", {
      params: { path: { ticker }, query: q },
      parseAs: "text",
    });
    if (error || typeof data !== "string") {
      setExportError("The export could not be created. Try again.");
      return;
    }
    const url = URL.createObjectURL(new Blob([data], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${ticker}-statements-${q.basis}-${q.frequency}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="px-[22px] py-4">
        <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
          <Choice label="Basis" value={q.basis} onChange={(v) => set("basis", v)} options={[["standardized", "Standardized"], ["reported", "As reported"]]} />
          <Choice label="Period" value={q.frequency} onChange={(v) => set("frequency", v)} options={[["annual", "Annual"], ["quarterly", "Quarterly"]]} />
          <Choice label="Figures" value={q.view} onChange={(v) => set("view", v)} options={[["restated", "Restated"], ["original", "As first filed"]]} />
          <Choice label="Units" value={q.units} onChange={(v) => set("units", v)} options={[["1", "Units"], ["1000", "Thousands"], ["1000000", "Millions"]]} />
          <Choice label="Show as" value={q.transform} onChange={(v) => set("transform", v)} options={[["none", "Amounts"], ["common_size", "Common-size"], ["yoy", "Year on year"]]} />
          <label className="flex items-center gap-2 pb-1 text-sm">
            <input type="checkbox" checked={q.per_share} onChange={(e) => set("per_share", e.target.checked)} />
            Per share
          </label>
          <label className="flex items-center gap-2 pb-1 text-sm">
            <input type="checkbox" checked={headlineOnly} onChange={(e) => setHeadlineOnly(e.target.checked)} />
            Headline lines only
          </label>
          <button
            type="button"
            onClick={exportCsv}
            className="ml-auto inline-flex items-center gap-1.5 border border-border px-3 py-1.5 text-sm hover:border-accent focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none"
          >
            <DownloadIcon aria-hidden="true" className="size-4" />
            Download CSV
          </button>
        </div>
        {exportError ? <p role="alert" className="mt-2 text-sm text-loss">{exportError}</p> : null}
      </Blueprint>

      <ResourceGate state={state} label="statements">
        {(view: StatementsView) => (
          <div className={cn("grid items-start gap-5", picked && "xl:grid-cols-[1fr_20rem]")}>
            <Blueprint aria-labelledby="statements-heading" className="space-y-4 px-[22px] py-5">
              <PanelHeading
                kicker={`${view.basis === "standardized" ? "Standardized" : "As reported"} · ${view.frequency}`}
                title={<span id="statements-heading">Financial statements</span>}
              />
              <p className="text-xs text-text-muted">
                Click a figure to see its filing. Marks: <b>d</b> derived (for example a quarter computed from year-to-date figures), <b>r</b> restated since first filed, <b>!</b> does not
                reconcile. An empty cell reads Unknown (not filed) or n/m (not meaningful), never zero.
              </p>
              {view.warnings.map((w) => (
                <AlertMessage key={w} title="Note">{w}</AlertMessage>
              ))}
              {view.statements.length === 0 ? (
                <p className="text-sm text-text-muted">No statements are stored for this company in this view.</p>
              ) : (
                <Table view={view} onPick={setPicked} headlineOnly={headlineOnly} />
              )}
              {view.missing_periods.length > 0 ? (
                <p className="text-sm text-text-muted">Periods with no filed data: {view.missing_periods.join(", ")}.</p>
              ) : null}
            </Blueprint>
            {picked ? <Inspector pick={picked} onClose={() => setPicked(null)} /> : null}
            {view.checks.length > 0 ? (
              <Blueprint aria-labelledby="checks-heading" className="space-y-2 px-[22px] py-5 xl:col-span-2">
                <PanelHeading kicker="Do the statements add up" title={<span id="checks-heading">Checks</span>} />
                <ul className="grid gap-x-8 gap-y-1 text-sm sm:grid-cols-2">
                  {view.checks.map((c) => (
                    <li key={`${c.name}-${c.statement}-${c.column}`} className="flex gap-2">
                      <span className={cn("w-16 shrink-0 font-semibold", c.passed === true ? "text-gain" : c.passed === false ? "text-loss" : "text-text-muted")}>
                        {c.passed === true ? "Passed" : c.passed === false ? "Failed" : "Not run"}
                      </span>
                      <span>
                        {c.name} · {c.column}
                        <span className="block text-xs text-text-muted">{c.detail}</span>
                      </span>
                    </li>
                  ))}
                </ul>
                <p className="text-xs text-text-muted">Method {view.method_version}.</p>
              </Blueprint>
            ) : null}
          </div>
        )}
      </ResourceGate>
    </div>
  );
}
