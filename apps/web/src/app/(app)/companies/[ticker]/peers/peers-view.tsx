"use client";

import Link from "next/link";
import { useCallback, useMemo, useState, type FormEvent } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Absent, ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { companyHref, formatRatio, percentileLabel, toNumber, type PeerCompare, type PeerGroup } from "@/lib/company/company";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

type Scope = "confirmed" | "group" | "home_market" | "global";
type IndexMetric = "revenue" | "operating_income" | "net_income" | "fcf";

const SCOPES: readonly (readonly [Scope, string])[] = [
  ["confirmed", "Confirmed peers"],
  ["group", "Saved group"],
  ["home_market", "Home market, same sector"],
  ["global", "Global, same sector"],
];
const INDEX_METRICS: readonly (readonly [IndexMetric, string])[] = [
  ["revenue", "Revenue"],
  ["operating_income", "Operating income"],
  ["net_income", "Net income"],
  ["fcf", "Free cash flow"],
];

type Point = { offset: number; index: number | null };

function pointsOf(raw: readonly Record<string, unknown>[]): Point[] {
  return raw.map((p) => ({ offset: Number(p.offset), index: toNumber(p.index as string | number | null) }));
}

/** Each company's own first positive year = 100. A gap breaks the line; it is not drawn as zero. */
function IndexChart({ data }: { data: PeerCompare }) {
  const series = data.index_series.map((s) => ({ ticker: s.ticker, subject: s.ticker === data.ticker, pts: pointsOf(s.points) }));
  const all = series.flatMap((s) => s.pts.map((p) => p.index).filter((v): v is number => v !== null));
  if (all.length < 2) return <p className="text-sm text-text-muted">There are too few comparable years to draw an index.</p>;
  const lo = Math.min(...all, 100);
  const hi = Math.max(...all, 100);
  const offsets = series.flatMap((s) => s.pts.map((p) => p.offset));
  const minO = Math.min(...offsets);
  const maxO = Math.max(...offsets);
  const W = 640;
  const H = 220;
  const pad = { l: 40, r: 56, t: 10, b: 22 };
  const x = (o: number) => pad.l + (maxO === minO ? 0 : ((o - minO) / (maxO - minO)) * (W - pad.l - pad.r));
  const y = (v: number) => pad.t + (hi === lo ? 0.5 : 1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);
  const path = (pts: Point[]) => {
    let d = "";
    let pen = false;
    for (const p of pts) {
      if (p.index === null) {
        pen = false;
        continue;
      }
      d += `${pen ? "L" : "M"}${x(p.offset).toFixed(1)} ${y(p.index).toFixed(1)} `;
      pen = true;
    }
    return d;
  };
  const ticks = [lo, 100, hi].filter((v, i, a) => a.indexOf(v) === i);
  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full max-w-3xl" role="img" aria-label="Index of each company's annual figure, first positive year equals 100">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} className={t === 100 ? "stroke-border" : "stroke-border-soft"} strokeDasharray={t === 100 ? "" : "2 3"} />
            <text x={pad.l - 6} y={y(t) + 3} textAnchor="end" className="fill-text-muted text-[10px]">
              {Math.round(t)}
            </text>
          </g>
        ))}
        {series
          .slice()
          .sort((a, b) => Number(a.subject) - Number(b.subject))
          .map((s) => {
            const last = [...s.pts].reverse().find((p) => p.index !== null);
            return (
              <g key={s.ticker}>
                <path d={path(s.pts)} fill="none" className={s.subject ? "stroke-chart-line" : "stroke-chart-benchmark"} strokeWidth={s.subject ? 2.25 : 1} />
                {last && last.index !== null ? (
                  <text x={x(last.offset) + 5} y={y(last.index) + 3} className={cn("text-[10px]", s.subject ? "fill-text font-semibold" : "fill-text-muted")}>
                    {s.ticker}
                  </text>
                ) : null}
              </g>
            );
          })}
        <text x={pad.l} y={H - 6} className="fill-text-muted text-[10px]">
          {Math.abs(minO)} years back
        </text>
        <text x={W - pad.r} y={H - 6} textAnchor="end" className="fill-text-muted text-[10px]">
          latest fiscal year
        </text>
      </svg>
    </figure>
  );
}

function GroupForm({ ticker, onSaved }: { ticker: string; onSaved: () => void }) {
  const [name, setName] = useState("");
  const [peers, setPeers] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const list = peers
      .split(/[\s,;]+/)
      .map((p) => p.trim().toUpperCase())
      .filter(Boolean);
    if (!name.trim() || list.length === 0) {
      setError("Give the group a name and at least one ticker.");
      return;
    }
    setBusy(true);
    setError(null);
    const { error: err } = await getBrowserApiClient().POST("/companies/{ticker}/peer-groups", { params: { path: { ticker } }, body: { name: name.trim(), peers: list } });
    setBusy(false);
    if (err) {
      setError("The group could not be saved. Check the tickers and try again.");
      return;
    }
    setName("");
    setPeers("");
    onSaved();
  }

  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
      <label className="text-sm">
        <span className="mb-1 block text-xs text-text-muted">Group name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} className="border border-border bg-surface px-2 py-1.5" />
      </label>
      <label className="min-w-64 flex-1 text-sm">
        <span className="mb-1 block text-xs text-text-muted">Tickers, separated by commas or spaces</span>
        <input value={peers} onChange={(e) => setPeers(e.target.value)} className="w-full border border-border bg-surface px-2 py-1.5" />
      </label>
      <button type="submit" disabled={busy} className="border border-border px-3 py-1.5 text-sm hover:border-accent disabled:opacity-50">
        Save group
      </button>
      {error ? <p role="alert" className="w-full text-sm text-loss">{error}</p> : null}
    </form>
  );
}

export function PeersView({ ticker }: { ticker: string }) {
  const [scope, setScope] = useState<Scope>("confirmed");
  const [groupId, setGroupId] = useState<number | null>(null);
  const [indexMetric, setIndexMetric] = useState<IndexMetric>("revenue");

  const groupsFetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/peer-groups", { params: { path: { ticker } } }), [ticker]);
  const { state: groups, reload: reloadGroups } = useApiResource(groupsFetcher);
  const saved: PeerGroup[] = useMemo(() => (groups.kind === "ok" ? groups.data : []), [groups]);

  const fetcher = useCallback(
    () =>
      getBrowserApiClient().GET("/companies/{ticker}/peers", {
        params: { path: { ticker }, query: { scope, index_metric: indexMetric, ...(scope === "group" && groupId !== null ? { group_id: groupId } : {}) } },
      }),
    [ticker, scope, groupId, indexMetric],
  );
  const { state } = useApiResource(fetcher);

  async function remove(id: number) {
    await getBrowserApiClient().DELETE("/companies/{ticker}/peer-groups/{group_id}", { params: { path: { ticker, group_id: id } } });
    if (groupId === id) setGroupId(null);
    reloadGroups();
  }

  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="space-y-4 px-[22px] py-4">
        <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
          <label className="text-sm">
            <span className="mb-1 block text-xs text-text-muted">Peer set</span>
            <select value={scope} onChange={(e) => setScope(e.target.value as Scope)} className="border border-border bg-surface px-2 py-1.5">
              {SCOPES.map(([v, t]) => (
                <option key={v} value={v}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          {scope === "group" ? (
            <label className="text-sm">
              <span className="mb-1 block text-xs text-text-muted">Saved group</span>
              <select value={groupId ?? ""} onChange={(e) => setGroupId(e.target.value === "" ? null : Number(e.target.value))} className="border border-border bg-surface px-2 py-1.5">
                <option value="">Choose a group…</option>
                {saved.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name} ({g.peers.join(", ")})
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <label className="text-sm">
            <span className="mb-1 block text-xs text-text-muted">Growth chart measures</span>
            <select value={indexMetric} onChange={(e) => setIndexMetric(e.target.value as IndexMetric)} className="border border-border bg-surface px-2 py-1.5">
              {INDEX_METRICS.map(([v, t]) => (
                <option key={v} value={v}>
                  {t}
                </option>
              ))}
            </select>
          </label>
        </div>
        <details className="text-sm">
          <summary className="cursor-pointer text-accent-ink">Save your own peer group{saved.length ? ` (${saved.length} saved)` : ""}</summary>
          <div className="mt-3 space-y-3">
            <GroupForm ticker={ticker} onSaved={reloadGroups} />
            {saved.length > 0 ? (
              <ul className="space-y-1">
                {saved.map((g) => (
                  <li key={g.id} className="flex items-center gap-3">
                    <span className="font-medium">{g.name}</span>
                    <span className="text-text-muted">{g.peers.join(", ")}</span>
                    <button type="button" onClick={() => remove(g.id)} className="ml-auto text-xs text-loss hover:underline">
                      Delete
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        </details>
      </Blueprint>

      <ResourceGate state={state} label="the peer comparison">
        {(data: PeerCompare) => (
          <>
            <Blueprint aria-labelledby="peers-table" className="space-y-4 px-[22px] py-5">
              <PanelHeading kicker={data.scope_label} title={<span id="peers-table">Side by side</span>} />
              {data.warnings.map((w) => (
                <AlertMessage key={w} title="Note">{w}</AlertMessage>
              ))}
              {data.excluded.length > 0 ? (
                <p className="text-sm text-text-muted">Left out: {data.excluded.map((p) => `${p.ticker} (${p.reason})`).join("; ")}.</p>
              ) : null}
              {data.companies.length < 2 ? (
                <p className="text-sm text-text-muted">There are no peers to compare in this set. Confirm peers on the relationship review, or save a group above.</p>
              ) : (
                <div className="overflow-x-auto border border-border">
                  <table className="w-full border-collapse text-sm">
                    <thead>
                      <tr className="bg-surface-sunken">
                        <th scope="col" className="sticky left-0 min-w-48 bg-surface-sunken px-3 py-1.5 text-left font-medium">Metric</th>
                        {data.companies.map((c) => (
                          <th key={c.ticker} scope="col" className={cn("px-2 py-1.5 text-right font-medium", c.is_subject && "text-accent-ink")}>
                            {c.is_subject ? c.ticker : <Link className="hover:underline" href={companyHref(c.ticker, "ratios")}>{c.ticker}</Link>}
                          </th>
                        ))}
                        <th scope="col" className="px-2 py-1.5 text-right font-medium">Median</th>
                        <th scope="col" className="px-2 py-1.5 text-right font-medium">Range</th>
                        <th scope="col" className="px-2 py-1.5 text-right font-medium">Position</th>
                      </tr>
                      <tr className="text-xs text-text-muted">
                        <th scope="row" className="sticky left-0 bg-surface px-3 py-1 text-left font-normal">Fiscal year ends</th>
                        {data.companies.map((c) => (
                          <td key={c.ticker} className="px-2 py-1 text-right">
                            {c.fiscal_end ?? "unknown"}
                            {c.fiscal_distance_days && c.fiscal_distance_days > 0 ? <span className={cn("block", c.fiscal_distance_days > 45 && "text-unconfirmed")}>{c.fiscal_distance_days} d apart</span> : null}
                          </td>
                        ))}
                        <td colSpan={3} />
                      </tr>
                    </thead>
                    <tbody>
                      {data.rows.map((r) => (
                        <tr key={r.id} className="border-t border-border-soft">
                          <th scope="row" className="sticky left-0 bg-surface px-3 py-1 text-left font-normal">{r.label}</th>
                          {data.companies.map((c) => {
                            const cell = r.cells[c.ticker];
                            return (
                              <td key={c.ticker} className={cn("num px-2 py-1 text-right", c.is_subject && "font-semibold")}>
                                {cell ? (formatRatio(cell.value, r.unit) ?? <Absent reason={cell.reason} />) : <Absent reason="missing: no data" />}
                              </td>
                            );
                          })}
                          <td className="num px-2 py-1 text-right">{formatRatio(r.median, r.unit) ?? <Absent reason={r.withheld_reason ?? "peer median withheld"} />}</td>
                          <td className="num px-2 py-1 text-right text-xs text-text-muted">
                            {r.low !== null && r.high !== null ? `${formatRatio(r.low, r.unit)} to ${formatRatio(r.high, r.unit)}` : ""}
                          </td>
                          <td className="px-2 py-1 text-right text-xs">{percentileLabel(r.percentile) ?? ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="text-xs text-text-muted">Method {data.method_version}. A median needs at least five comparable peers. Figures stay in each company&apos;s own currency; ratios are currency-free.</p>
            </Blueprint>
            <Blueprint aria-labelledby="peers-index" className="space-y-3 px-[22px] py-5">
              <PanelHeading kicker="Each company's first year = 100" title={<span id="peers-index">Growth since the first comparable year</span>} />
              <IndexChart data={data} />
            </Blueprint>
          </>
        )}
      </ResourceGate>
    </div>
  );
}
