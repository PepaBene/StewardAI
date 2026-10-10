"use client";

import { ChevronRightIcon } from "lucide-react";
import { useCallback, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Absent, RangeStrip, ResourceGate, SourceLink } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatRatio, percentileLabel, toNumber, type PeerGroup, type RatioRow, type RatiosView } from "@/lib/company/company";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

type Scope = "none" | "confirmed" | "group" | "home_market" | "global";

const SCOPES: readonly (readonly [Scope, string])[] = [
  ["none", "No peers"],
  ["confirmed", "Confirmed peers"],
  ["group", "Saved group"],
  ["home_market", "Home market, same sector"],
  ["global", "Global, same sector"],
];

function Sparkline({ row }: { row: RatioRow }) {
  const pts = row.points.map((p) => toNumber(p.value));
  const nums = pts.filter((n): n is number => n !== null);
  if (nums.length < 2) return <span className="text-xs text-text-muted">too few points</span>;
  const lo = Math.min(...nums);
  const hi = Math.max(...nums);
  const w = 96;
  const h = 22;
  const x = (i: number) => (pts.length === 1 ? 0 : (i / (pts.length - 1)) * w);
  const y = (v: number) => (hi === lo ? h / 2 : h - 2 - ((v - lo) / (hi - lo)) * (h - 4));
  // Unknown points break the line instead of being drawn as zero.
  let d = "";
  let pen = false;
  pts.forEach((v, i) => {
    if (v === null) {
      pen = false;
      return;
    }
    d += `${pen ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)} `;
    pen = true;
  });
  return (
    <svg width={w} height={h} role="img" aria-label={`${row.label}, ${nums.length} periods, from ${formatRatio(nums[0], row.unit)} to ${formatRatio(nums[nums.length - 1], row.unit)}`} className="overflow-visible">
      <path d={d} fill="none" stroke="currentColor" strokeWidth="1.25" className="text-chart-line" />
    </svg>
  );
}

function Drill({ row, view }: { row: RatioRow; view: RatiosView }) {
  return (
    <div className="grid gap-x-8 gap-y-3 bg-surface-muted px-4 py-3 text-sm lg:grid-cols-2">
      <dl className="space-y-1.5">
        <div>
          <dt className="text-xs text-text-muted">Formula</dt>
          <dd>{row.formula}</dd>
        </div>
        <div>
          <dt className="text-xs text-text-muted">Period rule</dt>
          <dd>{row.period_rule}</dd>
        </div>
        <div>
          <dt className="text-xs text-text-muted">When it is not meaningful</dt>
          <dd>{row.exclusions}</dd>
        </div>
        {row.own_median_note ? (
          <div>
            <dt className="text-xs text-text-muted">Own median</dt>
            <dd>{row.own_median_note}</dd>
          </div>
        ) : null}
        {row.peer?.withheld_reason ? (
          <div>
            <dt className="text-xs text-text-muted">Peer median</dt>
            <dd>{row.peer.withheld_reason}</dd>
          </div>
        ) : null}
      </dl>
      <div className="space-y-3">
        <div>
          <p className="mb-1 text-xs text-text-muted">History</p>
          <ul className="grid grid-cols-2 gap-x-4 text-xs sm:grid-cols-3">
            {row.points.map((p) => (
              <li key={p.column} className="flex justify-between gap-2 border-b border-border-soft py-0.5">
                <span>{p.label}</span>
                <span className="num">{formatRatio(p.value, row.unit) ?? <Absent reason={p.reason} />}</span>
              </li>
            ))}
          </ul>
        </div>
        {row.inputs && row.inputs.length > 0 ? (
          <div>
            <p className="mb-1 text-xs text-text-muted">Inputs to the latest value</p>
            <ul className="space-y-0.5 text-xs">
              {row.inputs.map((i) => (
                <li key={`${i.key}-${i.column}`} className="flex flex-wrap justify-between gap-2">
                  <span>
                    {i.label} <span className="text-text-muted">({i.column})</span>
                    {i.derived ? <span className="text-unconfirmed"> · {i.derived}</span> : null}
                  </span>
                  <span className="num">
                    {formatRatio(i.value, "money") ?? "unknown"}
                    {i.filing_url ? <> <SourceLink href={i.filing_url}>filing</SourceLink></> : null}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {view.price_currency && view.statement_currency && view.price_currency !== view.statement_currency ? (
          <p className="text-xs text-unconfirmed">Price is in {view.price_currency} and statements in {view.statement_currency}; valuation ratios are not converted.</p>
        ) : null}
      </div>
    </div>
  );
}

function RatioLine({ row, view, open, onToggle }: { row: RatioRow; view: RatiosView; open: boolean; onToggle: () => void }) {
  const latest = formatRatio(row.latest?.value, row.unit);
  const own = formatRatio(row.own_median_5y, row.unit);
  const own10 = formatRatio(row.own_median_10y, row.unit);
  const peer = row.peer;
  const peerMedian = peer ? formatRatio(peer.median, row.unit) : null;
  const pct = peer ? percentileLabel(peer.percentile) : null;
  return (
    <>
      <tr className="border-t border-border-soft">
        <th scope="row" className="px-3 py-1.5 text-left font-normal">
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={open}
            className="inline-flex items-center gap-1 text-left hover:text-accent-ink focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none"
          >
            <ChevronRightIcon aria-hidden="true" className={cn("size-3.5 shrink-0 transition-transform", open && "rotate-90")} />
            {row.label}
          </button>
        </th>
        <td className="num px-2 py-1.5 text-right">{latest ?? <Absent reason={row.latest?.reason} />}</td>
        <td className="px-2 py-1.5">
          <Sparkline row={row} />
        </td>
        <td className="num px-2 py-1.5 text-right">{own ?? <Absent reason={row.own_median_note ?? "fewer than 3 periods"} />}</td>
        <td className="num hidden px-2 py-1.5 text-right md:table-cell">{own10 ?? <span className="text-text-muted">—</span>}</td>
        <td className="num px-2 py-1.5 text-right">{peer ? (peerMedian ?? <Absent reason={peer.withheld_reason ?? "peer median withheld"} />) : <span className="text-text-muted">—</span>}</td>
        <td className="px-2 py-1.5 text-right text-xs whitespace-nowrap">
          {peer && peerMedian !== null ? (
            <span className="inline-flex items-center gap-2">
              <RangeStrip value={row.latest?.value} low={peer.low} high={peer.high} median={peer.median} label={`${row.label} against ${peer.count} peers: ${pct ?? "position unknown"}`} />
              <span>{pct}</span>
            </span>
          ) : null}
        </td>
      </tr>
      {open ? (
        <tr>
          <td colSpan={7} className="p-0">
            <Drill row={row} view={view} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

export function RatiosTab({ ticker }: { ticker: string }) {
  const [scope, setScope] = useState<Scope>("none");
  const [groupId, setGroupId] = useState<number | null>(null);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());

  const groupsFetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/peer-groups", { params: { path: { ticker } } }), [ticker]);
  const { state: groups } = useApiResource(groupsFetcher);
  const savedGroups: PeerGroup[] = groups.kind === "ok" ? groups.data : [];

  const fetcher = useCallback(
    () =>
      getBrowserApiClient().GET("/companies/{ticker}/ratios", {
        params: { path: { ticker }, query: { peers: scope, ...(scope === "group" && groupId !== null ? { group_id: groupId } : {}) } },
      }),
    [ticker, scope, groupId],
  );
  const { state } = useApiResource(fetcher);

  const toggle = (set: ReadonlySet<string>, id: string): Set<string> => {
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  };

  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="px-[22px] py-4">
        <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
          <label className="text-sm">
            <span className="mb-1 block text-xs text-text-muted">Compare with</span>
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
                {savedGroups.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name} ({g.peers.length})
                  </option>
                ))}
              </select>
              {savedGroups.length === 0 ? <span className="mt-1 block text-xs text-text-muted">No saved groups yet. Create one on the Peers tab.</span> : null}
            </label>
          ) : null}
        </div>
      </Blueprint>

      <ResourceGate state={state} label="ratios">
        {(view: RatiosView) => (
          <Blueprint aria-labelledby="ratios-heading" className="space-y-4 px-[22px] py-5">
            <PanelHeading
              kicker={view.as_of ? `As of ${view.as_of}` : "Latest filed period"}
              title={<span id="ratios-heading">Ratios</span>}
            />
            {view.is_financial ? (
              <AlertMessage title="Financial company">Gross margin, current ratio, net debt and enterprise-value ratios do not describe a bank or insurer and are shown as n/m.</AlertMessage>
            ) : null}
            {view.warnings.map((w) => (
              <AlertMessage key={w} title="Note">{w}</AlertMessage>
            ))}
            {view.peer_excluded.length > 0 ? (
              <p className="text-sm text-text-muted">
                Left out of the peer figures: {view.peer_excluded.map((p) => `${p.ticker} (${p.reason})`).join("; ")}.
              </p>
            ) : null}
            <div className="overflow-x-auto border border-border">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="bg-surface-muted text-left">
                    <th scope="col" className="min-w-56 px-3 py-1.5 font-medium">Ratio</th>
                    <th scope="col" className="px-2 py-1.5 text-right font-medium">Latest</th>
                    <th scope="col" className="px-2 py-1.5 font-medium">History</th>
                    <th scope="col" className="px-2 py-1.5 text-right font-medium">Own median 5 yr</th>
                    <th scope="col" className="hidden px-2 py-1.5 text-right font-medium md:table-cell">10 yr</th>
                    <th scope="col" className="px-2 py-1.5 text-right font-medium">Peer median</th>
                    <th scope="col" className="px-2 py-1.5 text-right font-medium">Position among peers</th>
                  </tr>
                </thead>
                {view.groups.map((g) => (
                  <tbody key={g.id}>
                    <tr className="border-t border-border bg-surface">
                      <th colSpan={7} scope="colgroup" className="px-3 py-1 text-left">
                        <button
                          type="button"
                          aria-expanded={!collapsed.has(g.id)}
                          onClick={() => setCollapsed((s) => toggle(s, g.id))}
                          className="inline-flex items-center gap-1 text-sm font-semibold focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none"
                        >
                          <ChevronRightIcon aria-hidden="true" className={cn("size-3.5 transition-transform", !collapsed.has(g.id) && "rotate-90")} />
                          {g.label}
                        </button>
                      </th>
                    </tr>
                    {collapsed.has(g.id)
                      ? null
                      : g.ratios.map((r) => <RatioLine key={r.id} row={r} view={view} open={open.has(r.id)} onToggle={() => setOpen((s) => toggle(s, r.id))} />)}
                  </tbody>
                ))}
              </table>
            </div>
            <p className="text-xs text-text-muted">
              Peer figures need at least five comparable peers; with fewer, the median is withheld rather than shown. Method {view.method_version}.
            </p>
          </Blueprint>
        )}
      </ResourceGate>
    </div>
  );
}
