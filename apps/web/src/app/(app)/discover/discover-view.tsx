"use client";

import { LoaderCircleIcon, PlayIcon, SnowflakeIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { BlueprintCorners } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import {
  apiToTyped,
  buildFilters,
  DEFAULT_FUND_COLUMNS,
  DEFAULT_STOCK_COLUMNS,
  IDEA_PIPELINE_NOTE,
  UNIVERSES,
  type ApiFilter,
  type CatalogueResponse,
  type ColumnSet,
  type DiscoveryResult,
  type FilterDraft,
  type Kind,
  type ResultRow,
  type ScreenSummary,
  type UniverseId,
} from "@/lib/discover/discover";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

import { CompareTray } from "./compare-tray";
import { FilterBuilder, SELECT } from "./filter-builder";
import { ProviderPanel } from "./provider-panel";
import { ResultsPanel } from "./results-panel";
import { SavedScreens } from "./saved-screens";

type Run =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "error"; title: string; detail: string }
  | { kind: "ok"; result: DiscoveryResult };

function errorDetail(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    return Array.isArray(d) ? d.join(" ") : String(d);
  }
  return "";
}

/**
 * Discover: screen the app's covered stock universe or its funds. Coverage is shown before a
 * condition is applied, every member of the universe is accounted for in the result, unknown
 * values stay unknown, and each row explains why it matched.
 */
export function DiscoverView() {
  const nextId = useRef(1);
  const [kind, setKind] = useState<Kind>("stock");
  const [catalogue, setCatalogue] = useState<CatalogueResponse | null>(null);
  const [catalogueError, setCatalogueError] = useState<string | null>(null);
  const [universe, setUniverse] = useState<UniverseId>("covered");
  const [snapshotId, setSnapshotId] = useState("");
  const [asOf, setAsOf] = useState("");
  const [drafts, setDrafts] = useState<FilterDraft[]>([]);
  const [missing, setMissing] = useState<"exclude" | "keep_unknown">("exclude");
  const [reconciledOnly, setReconciledOnly] = useState(false);
  const [ideaPipeline, setIdeaPipeline] = useState(false);
  const [sortBy, setSortBy] = useState("");
  const [columns, setColumns] = useState<string[]>([...DEFAULT_STOCK_COLUMNS]);
  const [errors, setErrors] = useState<string[]>([]);
  const [run, setRun] = useState<Run>({ kind: "idle" });
  const [rows, setRows] = useState<ResultRow[]>([]);
  const [loadingMore, setLoadingMore] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [selected, setSelected] = useState<Map<string, ResultRow>>(new Map());
  const [watched, setWatched] = useState<Set<string>>(new Set());
  const [screens, setScreens] = useState<ScreenSummary[]>([]);
  const [columnSets, setColumnSets] = useState<ColumnSet[]>([]);
  const [activeScreen, setActiveScreen] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const api = getBrowserApiClient();
  const [basic, setBasic] = useState(false);
  const allMetrics = useMemo(() => catalogue?.metrics ?? [], [catalogue]);
  // The basic screen hides every metric that depends on an optional provider key.
  const metrics = useMemo(() => (basic ? allMetrics.filter((m) => !m.needs_provider) : allMetrics), [basic, allMetrics]);
  const chooseBasic = (next: boolean) => {
    setBasic(next);
    if (next) {
      const keyFree = new Set(allMetrics.filter((m) => !m.needs_provider).map((m) => m.id));
      setDrafts((cur) => cur.filter((d) => keyFree.has(d.metric)));
      setColumns((cur) => cur.filter((c) => keyFree.has(c)));
      setSortBy((cur) => (keyFree.has(cur) ? cur : ""));
    }
  };

  // Catalogue: definitions, enabled state and coverage, reloaded per kind and data date.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const r = await getBrowserApiClient().GET("/discover/catalogue", { params: { query: { kind, as_of: asOf || undefined } } });
        if (cancelled) return;
        if (r.response.ok && r.data) {
          setCatalogue(r.data);
          setCatalogueError(null);
        } else setCatalogueError(`The metric catalogue failed to load (HTTP ${r.response.status}). ${errorDetail(r.error)}`);
      } catch (err) {
        if (!cancelled) setCatalogueError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [kind, asOf]);

  // Saved screens, column sets and the watchlist for the current kind.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const client = getBrowserApiClient();
        const [s, c, w] = await Promise.all([
          client.GET("/discover/screens", { params: { query: { kind } } }),
          client.GET("/discover/column-sets", { params: { query: { kind } } }),
          client.GET("/watchlist"),
        ]);
        if (cancelled) return;
        if (s.data) setScreens(s.data);
        if (c.data) setColumnSets(c.data);
        if (w.data) setWatched(new Set(w.data.map((i) => i.ticker)));
      } catch {
        /* the builder works without saved items */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [kind]);

  const switchKind = (next: Kind) => {
    if (next === kind) return;
    setKind(next);
    setCatalogue(null);
    setCatalogueError(null);
    setDrafts([]);
    setRun({ kind: "idle" });
    setRows([]);
    setSelected(new Map());
    setActiveScreen(null);
    setSortBy("");
    setColumns([...(next === "fund" ? DEFAULT_FUND_COLUMNS : DEFAULT_STOCK_COLUMNS)]);
  };

  const request = (filters: ApiFilter[], cursor?: string) => {
    const common = { filters, missing, sort_by: sortBy || undefined, limit: 50, cursor, as_of: asOf || undefined };
    return kind === "fund"
      ? common
      : {
          ...common,
          universe,
          snapshot_id: universe === "snapshot" && snapshotId ? Number(snapshotId) : undefined,
          reconciled_only: reconciledOnly,
          preset: ideaPipeline ? ("idea_pipeline" as const) : undefined,
        };
  };

  const execute = async (cursor?: string) => {
    const built = buildFilters(drafts, metrics);
    setErrors(built.errors);
    if (built.errors.length > 0) return;
    if (cursor) setLoadingMore(true);
    else setRun({ kind: "running" });
    try {
      const client = getBrowserApiClient();
      const r =
        kind === "fund"
          ? await client.POST("/discover/funds", { body: request(built.filters, cursor) })
          : await client.POST("/discover/stocks", { body: request(built.filters, cursor) as never });
      if (r.response.ok && r.data) {
        setRun({ kind: "ok", result: r.data });
        setRows((cur) => (cursor ? [...cur, ...r.data!.rows] : r.data!.rows));
      } else if (!cursor) {
        setRun({ kind: "error", title: `The screen was not run (HTTP ${r.response.status})`, detail: errorDetail(r.error) });
      } else {
        setNotice(`Loading more failed (HTTP ${r.response.status}).`);
      }
    } catch (err) {
      if (!cursor) setRun({ kind: "error", title: "Backend unreachable", detail: err instanceof Error ? err.message : String(err) });
      else setNotice(err instanceof Error ? err.message : String(err));
    } finally {
      setLoadingMore(false);
    }
  };

  const toggleSelect = (key: string) =>
    setSelected((cur) => {
      const next = new Map(cur);
      const row = rows.find((r) => r.key === key);
      if (next.has(key)) next.delete(key);
      else if (row) next.set(key, row);
      return next;
    });

  const watch = async (key: string) => {
    const r = await api.POST("/watchlist", { body: { ticker: key, list_name: "Default" } });
    if (r.response.ok || r.response.status === 409) setWatched((cur) => new Set(cur).add(key));
    else setNotice(`Could not add ${key} to the watchlist (HTTP ${r.response.status}).`);
  };

  const exportCsv = async () => {
    const built = buildFilters(drafts, metrics);
    if (built.errors.length > 0) return setErrors(built.errors);
    setExporting(true);
    try {
      const body = {
        kind,
        columns,
        ...(kind === "fund" ? { fund: request(built.filters) } : { stock: request(built.filters) }),
      };
      const r = await api.POST("/discover/export", { body: body as never, parseAs: "text" });
      if (r.response.ok && typeof r.data === "string") {
        const url = URL.createObjectURL(new Blob([r.data], { type: "text/csv" }));
        const a = document.createElement("a");
        a.href = url;
        a.download = `discover-${kind}-${new Date().toISOString().slice(0, 10)}.csv`;
        a.click();
        URL.revokeObjectURL(url);
      } else setNotice(`Export failed (HTTP ${r.response.status}).`);
    } finally {
      setExporting(false);
    }
  };

  const freeze = async () => {
    setBusy(true);
    const r = await api.POST("/discover/universe/snapshots");
    setBusy(false);
    if (r.response.ok && r.data) {
      setNotice(`Universe frozen for ${r.data.as_of}: ${r.data.members} members, ${r.data.active} active (snapshot ${r.data.id}).`);
      setSnapshotId(String(r.data.id));
      setAsOf((a) => a); // refresh nothing else; the snapshot list reloads with the catalogue
    } else setNotice(`Could not freeze the universe (HTTP ${r.response.status}).`);
  };

  // Saved screens.
  const refreshScreens = async () => {
    const s = await api.GET("/discover/screens", { params: { query: { kind } } });
    if (s.data) setScreens(s.data);
  };
  const saveScreen = async (name: string) => {
    const built = buildFilters(drafts, metrics);
    setErrors(built.errors);
    if (built.errors.length > 0) return;
    setBusy(true);
    const def = request(built.filters);
    const payload = { name, kind, ...(kind === "fund" ? { fund: def } : { stock: def }) } as never;
    const existing = screens.find((s) => s.id === activeScreen && s.name === name);
    const r = existing
      ? await api.PUT("/discover/screens/{screen_id}", { params: { path: { screen_id: existing.id } }, body: payload })
      : await api.POST("/discover/screens", { body: payload });
    setBusy(false);
    if (r.response.ok && r.data) {
      setActiveScreen(r.data.id);
      setNotice(`Saved "${r.data.name}" as version ${r.data.latest_version}.`);
      await refreshScreens();
    } else setNotice(r.response.status === 409 ? "A screen with this name exists. Load it, change it and save a new version." : `Could not save (HTTP ${r.response.status}).`);
  };
  const loadScreen = async (id: number) => {
    const r = await api.GET("/discover/screens/{screen_id}", { params: { path: { screen_id: id } } });
    const latest = r.data?.versions[0];
    if (!r.data || !latest) return setNotice("Could not load that screen.");
    const def = latest.definition as { filters?: ApiFilter[]; missing?: "exclude" | "keep_unknown"; universe?: UniverseId; snapshot_id?: number | null; sort_by?: string | null; preset?: string | null; reconciled_only?: boolean };
    setDrafts(
      (def.filters ?? []).map((f) => {
        const info = metrics.find((m) => m.id === f.metric);
        const value = f.op === "in" ? (f.value as string[]).join(", ") : String(apiToTyped(Number(f.value), info?.unit ?? ""));
        return { id: nextId.current++, metric: f.metric, op: f.op, value };
      }),
    );
    setMissing(def.missing ?? "exclude");
    if (kind === "stock") {
      setUniverse(def.universe ?? "covered");
      setSnapshotId(def.snapshot_id ? String(def.snapshot_id) : "");
      setIdeaPipeline(def.preset === "idea_pipeline");
      setReconciledOnly(Boolean(def.reconciled_only));
    }
    setSortBy(def.sort_by ?? "");
    setActiveScreen(id);
    setNotice(`Loaded "${r.data.name}" version ${latest.version}. Run it to see today's result.`);
  };
  const runSaved = async (id: number) => {
    setRun({ kind: "running" });
    const r = await api.POST("/discover/screens/{screen_id}/run", { params: { path: { screen_id: id } } });
    if (r.response.ok && r.data) {
      setRun({ kind: "ok", result: r.data });
      setRows(r.data.rows);
      setActiveScreen(id);
    } else setRun({ kind: "error", title: `The saved screen was not run (HTTP ${r.response.status})`, detail: errorDetail(r.error) });
  };
  const archiveScreen = async (id: number) => {
    setBusy(true);
    await api.DELETE("/discover/screens/{screen_id}", { params: { path: { screen_id: id } } });
    setBusy(false);
    if (activeScreen === id) setActiveScreen(null);
    await refreshScreens();
  };
  const saveColumns = async (name: string) => {
    const r = await api.POST("/discover/column-sets", { body: { kind, name, columns } });
    if (r.response.ok) {
      const c = await api.GET("/discover/column-sets", { params: { query: { kind } } });
      if (c.data) setColumnSets(c.data);
      setNotice(`Saved column set "${name}".`);
    } else setNotice(`Could not save the column set (HTTP ${r.response.status}).`);
  };

  const info = catalogue?.universe as Record<string, unknown> | undefined;
  const selectedRows = [...selected.values()];

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Research & Verification"
        title="Discover"
        description="Screen the instruments and funds the app covers. Coverage is shown before you filter, and what a filter leaves out is counted, not hidden."
        aside={
          catalogue ? (
            <span className="num">
              Data date {formatDate(catalogue.as_of)} · method {catalogue.method_version}
            </span>
          ) : null
        }
      />

      <div role="tablist" aria-label="What to screen" className="flex gap-1 border-b border-border">
        {(["stock", "fund"] as const).map((k) => (
          <button
            key={k}
            type="button"
            role="tab"
            aria-selected={kind === k}
            onClick={() => switchKind(k)}
            className={cn(
              "-mb-px border-b-2 px-4 py-2 text-sm focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none",
              kind === k ? "border-accent font-semibold text-text" : "border-transparent text-text-muted hover:text-text",
            )}
          >
            {k === "stock" ? "Stocks" : "Funds"}
          </button>
        ))}
      </div>

      {catalogueError ? <AlertMessage title="Discovery is unavailable">{catalogueError}</AlertMessage> : null}
      {notice ? (
        <p role="status" className="border border-border bg-surface-muted px-3 py-2 text-sm">
          {notice}
        </p>
      ) : null}

      <section aria-labelledby="builder-heading" className="blueprint space-y-4 px-5 py-5">
        <BlueprintCorners />
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 id="builder-heading" className="text-[22px] leading-tight font-semibold">
              Conditions
            </h2>
            {info ? (
              <p className="max-w-[72ch] text-sm text-text-muted">
                {kind === "stock" ? (
                  <>
                    <span className="num">{String(info.members)}</span> instruments in scope,{" "}
                    <span className="num">{String(info.with_statements)}</span> with filed statements,{" "}
                    <span className="num">{String(info.with_prices)}</span> with price history. Markets:{" "}
                    {Object.entries((info.countries as Record<string, number>) ?? {})
                      .slice(0, 8)
                      .map(([c, n]) => `${c} ${n}`)
                      .join(", ") || "none yet"}
                    .
                  </>
                ) : (
                  <>
                    <span className="num">{String(info.members)}</span> funds, <span className="num">{String(info.with_holdings)}</span> with stored holdings,{" "}
                    <span className="num">{String(info.with_factsheet)}</span> with a factsheet.
                  </>
                )}
              </p>
            ) : (
              <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
                <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
                Measuring coverage…
              </p>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-end gap-4">
          {kind === "stock" ? (
            <label className="space-y-1 text-sm">
              <span className="block font-medium">Screen across</span>
              <select value={universe} onChange={(e) => setUniverse(e.target.value as UniverseId)} className={cn(SELECT, "w-56")}>
                {UNIVERSES.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {kind === "stock" && universe === "snapshot" ? (
            <label className="space-y-1 text-sm">
              <span className="block font-medium">Frozen on</span>
              <select value={snapshotId} onChange={(e) => setSnapshotId(e.target.value)} className={cn(SELECT, "w-56")}>
                <option value="">Latest on or before the data date</option>
                {(catalogue?.snapshots ?? []).map((s) => (
                  <option key={String(s.id)} value={String(s.id)}>
                    {formatDate(String(s.as_of))} ({String(s.members)} members)
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <label className="space-y-1 text-sm">
            <span className="block font-medium">Data date</span>
            <Input type="date" value={asOf} max={new Date().toISOString().slice(0, 10)} onChange={(e) => setAsOf(e.target.value)} className="w-44" />
          </label>
          {kind === "stock" ? (
            <Button type="button" variant="outline" size="sm" onClick={() => void freeze()} disabled={busy}>
              <SnowflakeIcon aria-hidden="true" />
              Freeze today&rsquo;s universe
            </Button>
          ) : null}
        </div>
        {kind === "stock" ? (
          <p className="max-w-[72ch] text-xs text-text-muted">
            {UNIVERSES.find((u) => u.id === universe)?.hint}.
            {asOf ? " A past date uses each listing's known active interval, so delisted names stay in; a member with no known end date is named in the result." : ""}
          </p>
        ) : null}

        {catalogue ? <ProviderPanel catalogue={catalogue} basic={basic} onBasic={chooseBasic} /> : null}

        {catalogue ? (
          <FilterBuilder catalogue={metrics} drafts={drafts} onChange={setDrafts} nextId={() => nextId.current++} />
        ) : null}

        <fieldset className="space-y-1.5 text-sm">
          <legend className="font-medium">When a number is missing</legend>
          {(
            [
              ["exclude", "Leave it out and count it", "A metric that could not be computed is never read as zero."],
              ["keep_unknown", "Keep it and mark it Unknown", "Shows what a gap hides."],
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
        {kind === "stock" ? (
          <div className="space-y-1.5 text-sm">
            <label className="flex items-start gap-2">
              <input type="checkbox" checked={ideaPipeline} onChange={(e) => setIdeaPipeline(e.target.checked)} className="mt-1" />
              <span>
                Idea pipeline
                <span className="block max-w-[72ch] text-xs text-text-muted">{IDEA_PIPELINE_NOTE}</span>
              </span>
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={reconciledOnly} onChange={(e) => setReconciledOnly(e.target.checked)} />
              Use only facts that match the filed statement
            </label>
          </div>
        ) : null}

        <div className="flex flex-wrap items-end gap-4">
          <label className="space-y-1 text-sm">
            <span className="block font-medium">Sort by</span>
            <select value={sortBy} onChange={(e) => setSortBy(e.target.value)} className={cn(SELECT, "w-56")}>
              <option value="">First condition</option>
              {metrics
                .filter((m) => m.enabled && m.kind === "numeric")
                .map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
            </select>
          </label>
          <details className="text-sm">
            <summary className="cursor-pointer py-1.5 text-accent-ink hover:underline">Columns ({columns.length})</summary>
            <ul className="mt-2 grid max-h-56 grid-cols-1 gap-x-6 gap-y-0.5 overflow-y-auto pr-2 sm:grid-cols-2">
              {metrics.map((m) => (
                <li key={m.id}>
                  <label className={cn("flex items-center gap-2 text-xs", !m.enabled && "text-text-faint")}>
                    <input
                      type="checkbox"
                      checked={columns.includes(m.id)}
                      onChange={(e) => setColumns((cur) => (e.target.checked ? [...cur, m.id] : cur.filter((c) => c !== m.id)))}
                    />
                    {m.label}
                  </label>
                </li>
              ))}
            </ul>
          </details>
        </div>

        {errors.length > 0 ? (
          <AlertMessage title="Fix these conditions to run the screen">
            <ul className="list-disc pl-5">
              {errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          </AlertMessage>
        ) : null}

        <Button type="button" onClick={() => void execute()} disabled={run.kind === "running" || !catalogue}>
          {run.kind === "running" ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <PlayIcon aria-hidden="true" />}
          Run screen
        </Button>
        {kind === "stock" ? (
          <p className="text-xs text-text-muted">
            Want to see how a rule would have done?{" "}
            <Link href="/discover/backtest" className="text-accent-ink hover:underline">
              Test a screen on the past
            </Link>
            .
          </p>
        ) : null}

        <details className="border-t border-border pt-3" open={screens.length > 0 || columnSets.length > 0}>
          <summary className="cursor-pointer text-sm font-medium text-accent-ink hover:underline">Saved screens and column sets</summary>
          <div className="mt-3">
            <SavedScreens
              screens={screens}
              columnSets={columnSets}
              activeId={activeScreen}
              onSave={(n) => void saveScreen(n)}
              onLoad={(id) => void loadScreen(id)}
              onRun={(id) => void runSaved(id)}
              onDelete={(id) => void archiveScreen(id)}
              onSaveColumns={(n) => void saveColumns(n)}
              onLoadColumns={(id) => setColumns(columnSets.find((c) => c.id === id)?.columns ?? columns)}
              busy={busy}
              canSave={Boolean(catalogue)}
            />
          </div>
        </details>

        {catalogue?.keyless_note ? <p className="max-w-[72ch] text-xs text-text-faint">{catalogue.keyless_note}</p> : null}
      </section>

      {run.kind === "idle" ? (
        <EmptyState title="No screen run yet">Set conditions above and run the screen. Every member of the universe is counted in the result, matched or not.</EmptyState>
      ) : null}
      {run.kind === "running" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Running the screen…
        </p>
      ) : null}
      {run.kind === "error" ? <AlertMessage title={run.title}>{run.detail}</AlertMessage> : null}
      {run.kind === "ok" ? (
        <ResultsPanel
          kind={kind}
          result={run.result}
          rows={rows}
          catalogue={metrics}
          columns={columns}
          selected={new Set(selected.keys())}
          onToggleSelect={toggleSelect}
          onLoadMore={() => void execute(run.result.next_cursor ?? undefined)}
          loadingMore={loadingMore}
          onExport={() => void exportCsv()}
          exporting={exporting}
          onWatch={(k) => void watch(k)}
          watched={watched}
        />
      ) : null}

      <CompareTray
        rows={selectedRows}
        catalogue={metrics}
        showBaseline={kind === "stock"}
        onRemove={(key) =>
          setSelected((cur) => {
            const next = new Map(cur);
            next.delete(key);
            return next;
          })
        }
        onClear={() => setSelected(new Map())}
      />
    </div>
  );
}
