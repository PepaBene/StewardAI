"use client";

import { SrTable } from "@/components/charts/shared";
import { formatDay } from "@/lib/disclosure/model";
import {
  ACCESS_LABEL,
  analystsLabel,
  axisUnitKey,
  BREAK_LABEL,
  baseValue,
  figure,
  gateWords,
  LANE_META,
  latestFor,
  lineWarnings,
  num,
  SERIES,
  signed,
  statisticOf,
  STATUS_LABEL,
  unitLabel,
  type Choice,
  type CoverageGap,
  type ConsensusSource,
  type EstimateLine,
  type EstimatesView,
  type EventComparison,
  type RevisionGate,
  type RevisionPair,
  type SegmentBridge,
  type Series,
} from "@/lib/disclosure/estimates";
import { safeHref } from "@/lib/reference/identity";
import { cn } from "@/lib/utils";

const W = 760;
const PAD = 14;
const LINK = "underline underline-offset-2 hover:text-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus";

/** A base-unit figure (value × 10^scale) in words: "1,262.0 millions", "1.47". */
export function formatBase(v: string | null | undefined, unit: string | null, currency: string | null): string {
  const n = num(v);
  if (n == null) return "not stated";
  const cur = currency ? `${currency} ` : "";
  if (unit === "percent") return `${n.toFixed(1)}%`;
  if (unit === "per_share") return `${cur}${n.toFixed(2)}`;
  const abs = Math.abs(n);
  if (abs >= 1e9) return `${cur}${(n / 1e9).toFixed(2)} billion`;
  if (abs >= 1e6) return `${cur}${(n / 1e6).toLocaleString("en-GB", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} million`;
  return `${cur}${n.toLocaleString("en-GB", { maximumFractionDigits: 2 })}`;
}

const dayText = (iso: string | null | undefined) => formatDay(iso);

// ------------------------------------------------------------------------------ five lanes
function statsText(l: EstimateLine): string {
  if (l.series === "company_guidance") return `low ${figure(l.low, l.unit)} · high ${figure(l.high, l.unit)}`;
  const parts = [`mean ${figure(l.mean, l.unit)}`, `median ${figure(l.median, l.unit)}`];
  if (l.series === "published_analyst_consensus") parts.push(`high ${figure(l.high, l.unit)}`, `low ${figure(l.low, l.unit)}`);
  return parts.join(" · ");
}

function LaneMark({ series, l, x, key0 }: { series: Series; l: EstimateLine; x: (v: number) => number; key0: string }) {
  const at = (v: string | null) => {
    const b = baseValue(v, l.scale);
    return b == null ? null : x(b);
  };
  const mean = at(l.mean);
  const median = at(l.median);
  const lo = at(l.low);
  const hi = at(l.high);
  if (series === "published_analyst_consensus") {
    return (
      <g key={key0}>
        {lo != null && hi != null ? <rect x={lo} y={12} width={Math.max(1, hi - lo)} height={12} className="fill-chart-benchmark" opacity={0.45} /> : null}
        {mean != null ? <line x1={mean} x2={mean} y1={6} y2={30} strokeWidth={2} className="stroke-chart-line" /> : null}
        {median != null ? <path d={`M ${median - 4} 34 L ${median + 4} 34 L ${median} 27 Z`} className="fill-chart-line" /> : null}
      </g>
    );
  }
  if (series === "company_guidance" && lo != null && hi != null) {
    return (
      <g key={key0} className="stroke-chart-line" fill="none" strokeWidth={1.5}>
        <path d={`M ${lo + 5} 8 H ${lo} V 28 H ${lo + 5}`} />
        <path d={`M ${hi - 5} 8 H ${hi} V 28 H ${hi - 5}`} />
        <line x1={lo} x2={hi} y1={18} y2={18} strokeWidth={1} className="stroke-chart-benchmark" />
      </g>
    );
  }
  const centre = mean ?? median;
  if (centre == null) return null;
  if (series === "event_calendar_estimate") return <rect key={key0} x={centre - 6} y={12} width={12} height={12} strokeWidth={1.5} className="fill-surface-raised stroke-chart-line" />;
  if (series === "owner_scenario")
    return (
      <g key={key0} className="stroke-chart-line" fill="none" strokeWidth={1.5} strokeDasharray="3 2">
        <rect x={centre - 7} y={11} width={14} height={14} />
        <line x1={centre} x2={centre} y1={4} y2={32} />
      </g>
    );
  return <circle key={key0} cx={centre} cy={18} r={4.5} className="fill-chart-line" />;
}

/**
 * Five lanes on one shared axis for one metric and fiscal period. Each lane has its own mark and its
 * own label; nothing is overlaid, averaged or merged. A lane with nothing in it says why, in words.
 */
export function FiveLanes({ view, choice }: { view: EstimatesView; choice: Choice }) {
  const rows = SERIES.map((s) => {
    const lane = view.lanes[s];
    const items = s === "named_individual_estimate" ? lane.filter((l) => l.metric === choice.metric && l.fiscal_period === choice.period && !l.segment) : [latestFor(lane, choice.metric, choice.period)].filter((l): l is EstimateLine => l != null);
    return { s, items };
  });
  const drawable = rows.flatMap((r) => r.items);
  const ref = rows.find((r) => r.s === "published_analyst_consensus")?.items[0] ?? drawable[0];
  const sameAxis = ref ? drawable.filter((l) => axisUnitKey(l) === axisUnitKey(ref)) : [];
  const apart = drawable.filter((l) => !sameAxis.includes(l));
  const nums = sameAxis.flatMap((l) => [l.mean, l.median, l.high, l.low].map((v) => baseValue(v, l.scale)).filter((n): n is number => n != null));
  if (!ref || nums.length === 0) return <p className="text-sm text-text-muted">Nothing is known for {choice.label} at this date.</p>;
  const lo = Math.min(...nums);
  const hi = Math.max(...nums);
  const span = hi - lo || Math.abs(hi) * 0.02 || 1;
  const min = lo - span * 0.08;
  const max = hi + span * 0.08;
  const x = (v: number) => PAD + ((v - min) / (max - min)) * (W - 2 * PAD);
  const ticks = [min + (max - min) * 0.1, (min + max) / 2, max - (max - min) * 0.1];
  const gaps = new Map(view.coverage.map((c) => [c.kind, c]));
  const scale = 10 ** ref.scale;
  return (
    <figure className="space-y-3">
      <ol className="space-y-3">
        {rows.map(({ s, items }) => {
          const inAxis = items.filter((l) => sameAxis.includes(l));
          return (
            <li key={s} className="border-t border-border pt-2">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                <h4 className="text-sm font-medium">{LANE_META[s].label}</h4>
                <span className="text-xs text-text-faint">{LANE_META[s].mark}</span>
              </div>
              {items.length === 0 ? (
                <p className="mt-1 text-sm text-text-muted">{gaps.get(s === "named_individual_estimate" ? "named_estimates" : s)?.detail ?? "Nothing known in this lane at this date."}</p>
              ) : (
                <>
                  <svg viewBox={`0 0 ${W} 40`} className="mt-1 block h-10 w-full" role="img" aria-label={`${LANE_META[s].label}, ${choice.label}`}>
                    <line x1={PAD} x2={W - PAD} y1={18} y2={18} className="stroke-border" strokeWidth={0.5} />
                    {inAxis.map((l) => (
                      <LaneMark key={l.id} key0={String(l.id)} series={s} l={l} x={(v) => x(v)} />
                    ))}
                    {s === "named_individual_estimate"
                      ? inAxis.map((l) => {
                          const v = baseValue(l.mean ?? l.median, l.scale);
                          return v == null ? null : (
                            <text key={`n${l.id}`} x={x(v)} y={38} textAnchor="middle" className="fill-text-faint font-mono" fontSize={9}>
                              {l.broker}
                            </text>
                          );
                        })
                      : null}
                  </svg>
                  <ul className="space-y-0.5 text-xs text-text-muted">
                    {items.map((l) => (
                      <li key={l.id}>
                        <span className="num">{statsText(l)}</span> · {unitLabel(l)} · {l.basis} · {l.series === "named_individual_estimate" ? `${l.broker}, ${l.analyst}` : analystsLabel(l.analyst_count)} · known {dayText(l.known_at)}
                        {l.document_url ? (
                          <>
                            {" "}
                            · <a className={LINK} href={safeHref(l.document_url) ?? undefined} target="_blank" rel="noopener noreferrer">source file</a>
                          </>
                        ) : null}
                        {lineWarnings(l).length ? <span className="block text-text-faint">{lineWarnings(l).join("; ")}.</span> : null}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </li>
          );
        })}
      </ol>
      <svg viewBox={`0 0 ${W} 22`} className="block h-6 w-full" aria-hidden="true">
        <line x1={PAD} x2={W - PAD} y1={4} y2={4} className="stroke-border" strokeWidth={0.75} />
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={4} y2={9} className="stroke-border" strokeWidth={0.75} />
            <text x={x(t)} y={19} textAnchor="middle" className="fill-text-faint font-mono num" fontSize={10}>
              {(t / scale).toLocaleString("en-GB", { maximumFractionDigits: 1 })}
            </text>
          </g>
        ))}
      </svg>
      <figcaption className="text-xs text-text-muted">
        Shared axis: {unitLabel(ref)}, {choice.label}. Lanes are separate series: guidance is a management target, a scenario is yours, and none is averaged into another.
        {apart.length ? ` Not drawn, because their currency, unit or scale differs: ${apart.map((l) => `${LANE_META[l.series as Series].label} (${unitLabel(l)})`).join(", ")}.` : ""}
      </figcaption>
      <SrTable
        caption={`All lanes for ${choice.label}`}
        columns={["Lane", "Mean", "Median", "High", "Low", "Analysts", "Basis", "Unit", "Known"]}
        rows={drawable.map((l) => [LANE_META[l.series as Series].label, figure(l.mean, l.unit), figure(l.median, l.unit), figure(l.high, l.unit), figure(l.low, l.unit), analystsLabel(l.analyst_count), l.basis, unitLabel(l), dayText(l.known_at)])}
      />
    </figure>
  );
}

// ------------------------------------------------------------------------ range by period
/** Published consensus per fiscal period: range bar, mean tick, median notch, panel size printed. Each row has its own scale. */
export function PeriodRanges({ lines, metric }: { lines: readonly EstimateLine[]; metric: string }) {
  const latest = new Map<string, EstimateLine>();
  for (const l of lines) {
    if (l.metric.toLowerCase() !== metric.toLowerCase() || l.segment) continue;
    const prev = latest.get(l.fiscal_period);
    if (!prev || Date.parse(l.known_at) > Date.parse(prev.known_at)) latest.set(l.fiscal_period, l);
  }
  const rows = [...latest.values()].sort((a, b) => (a.period_end ?? "").localeCompare(b.period_end ?? ""));
  if (rows.length === 0) return <p className="text-sm text-text-muted">No published consensus for {metric} at this date.</p>;
  return (
    <figure className="space-y-2">
      <ul className="space-y-2">
        {rows.map((l) => {
          const vals = [l.low, l.high, l.mean, l.median].map(num).filter((n): n is number => n != null);
          const a = Math.min(...vals);
          const b = Math.max(...vals);
          const sp = b - a || Math.abs(b) * 0.01 || 1;
          const x = (v: number) => PAD + ((v - (a - sp * 0.1)) / (sp * 1.2)) * (W - 2 * PAD);
          const mean = num(l.mean);
          const median = num(l.median);
          const lo = num(l.low);
          const hi = num(l.high);
          return (
            <li key={l.id} className="grid gap-x-4 gap-y-0.5 sm:grid-cols-[9rem_1fr]">
              <div className="text-sm">
                <span className="font-medium">{l.fiscal_period}</span>
                <span className="block text-xs text-text-faint">{analystsLabel(l.analyst_count ?? l.panel_analyst_count)} · {l.basis}</span>
              </div>
              <div>
                <svg viewBox={`0 0 ${W} 28`} className="block h-7 w-full" role="img" aria-label={`${l.fiscal_period} range`}>
                  {lo != null && hi != null ? <rect x={x(lo)} y={8} width={Math.max(1, x(hi) - x(lo))} height={10} className="fill-chart-benchmark" opacity={0.45} /> : null}
                  {mean != null ? <line x1={x(mean)} x2={x(mean)} y1={3} y2={23} strokeWidth={2} className="stroke-chart-line" /> : null}
                  {median != null ? <path d={`M ${x(median) - 4} 27 L ${x(median) + 4} 27 L ${x(median)} 20 Z`} className="fill-chart-line" /> : null}
                </svg>
                <p className="num text-xs text-text-muted">
                  low {figure(l.low, l.unit)} · mean {figure(l.mean, l.unit)} · median {figure(l.median, l.unit)} · high {figure(l.high, l.unit)} ({unitLabel(l)}) · known {dayText(l.known_at)}
                </p>
              </div>
            </li>
          );
        })}
      </ul>
      <p className="text-xs text-text-muted">Each row has its own scale, so bar lengths are not comparable between periods. The bar is low to high, the line is the mean, the notch is the median.</p>
      <SrTable caption={`${metric} consensus by fiscal period`} columns={["Period", "Low", "Mean", "Median", "High", "Analysts", "Known"]} rows={rows.map((l) => [l.fiscal_period, figure(l.low, l.unit), figure(l.mean, l.unit), figure(l.median, l.unit), figure(l.high, l.unit), analystsLabel(l.analyst_count), dayText(l.known_at)])} />
    </figure>
  );
}

// --------------------------------------------------------------------------- revisions
type Chain = { key: string; title: string; nodes: { id: number; known_at: string; value: number | null }[]; links: RevisionPair[]; line: EstimateLine | undefined };

function chains(pairs: readonly RevisionPair[], byId: Map<number, EstimateLine>): Chain[] {
  const groups = new Map<string, RevisionPair[]>();
  for (const p of pairs) {
    const k = `${p.series}¦${p.metric}¦${p.segment ?? ""}¦${p.fiscal_period}`;
    groups.set(k, [...(groups.get(k) ?? []), p]);
  }
  return [...groups.entries()].map(([key, ps]) => {
    const sorted = [...ps].sort((a, b) => Date.parse(a.later_known_at) - Date.parse(b.later_known_at));
    const ids = [sorted[0].earlier_id, ...sorted.map((p) => p.later_id)];
    const nodes = ids.map((id) => {
      const l = byId.get(id);
      const s = l ? statisticOf(l) : null;
      return { id, known_at: l?.known_at ?? "", value: s ? s.value : null };
    });
    const first = sorted[0];
    return { key, title: `${first.metric}${first.segment ? `, ${first.segment}` : ""}, ${first.fiscal_period}`, nodes, links: sorted, line: byId.get(first.later_id) };
  });
}

/**
 * Revision slopegraph: one column per snapshot, a line only between comparable neighbours. A pair that
 * breaks (basis, panel, currency, period, unit, restatement) shows a break marker with its reason and no slope.
 */
export function RevisionSlopes({ pairs, lines }: { pairs: readonly RevisionPair[]; lines: readonly EstimateLine[] }) {
  const byId = new Map(lines.map((l) => [l.id, l]));
  const cs = chains(pairs.filter((p) => p.series !== "owner_scenario"), byId);
  if (cs.length === 0) return <p className="text-sm text-text-muted">Fewer than two snapshots of the same estimate are known, so there is no revision to draw. Days between files are never filled in.</p>;
  return (
    <div className="space-y-5">
      {cs.map((c) => {
        const vals = c.nodes.map((n) => n.value).filter((v): v is number => v != null);
        const a = Math.min(...vals);
        const b = Math.max(...vals);
        const sp = b - a || Math.abs(b) * 0.01 || 1;
        const H = 96;
        const y = (v: number) => 14 + (1 - (v - a) / sp) * (H - 34);
        const step = (W - 2 * 40) / Math.max(1, c.nodes.length - 1);
        const px = (i: number) => 40 + i * step;
        return (
          <figure key={c.key} className="space-y-1">
            <figcaption className="text-sm font-medium">
              {c.title} <span className="text-xs font-normal text-text-faint">· {LANE_META[(c.links[0].series as Series)].label}{c.line ? ` · ${unitLabel(c.line)} · ${c.line.basis}` : ""}</span>
            </figcaption>
            <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full" role="img" aria-label={`Revisions of ${c.title}`}>
              {c.links.map((p, i) => {
                const n0 = c.nodes[i];
                const n1 = c.nodes[i + 1];
                if (p.comparable && n0.value != null && n1.value != null)
                  return (
                    <g key={p.later_id}>
                      <line x1={px(i)} y1={y(n0.value)} x2={px(i + 1)} y2={y(n1.value)} strokeWidth={1.5} className="stroke-chart-line" />
                      <text x={(px(i) + px(i + 1)) / 2} y={Math.min(y(n0.value), y(n1.value)) - 6} textAnchor="middle" className="fill-text-muted font-mono num" fontSize={10}>
                        {signed(num(p.change), 1)}
                      </text>
                    </g>
                  );
                const mx = (px(i) + px(i + 1)) / 2;
                return (
                  <g key={p.later_id} className="stroke-warning-signal" strokeWidth={1.5} fill="none">
                    <line x1={mx - 6} y1={H / 2 - 12} x2={mx - 2} y2={H / 2 + 6} />
                    <line x1={mx + 2} y1={H / 2 - 12} x2={mx + 6} y2={H / 2 + 6} />
                  </g>
                );
              })}
              {c.nodes.map((n, i) =>
                n.value == null ? null : (
                  <g key={n.id}>
                    <rect x={px(i) - 3.5} y={y(n.value) - 3.5} width={7} height={7} className="fill-chart-line" />
                    <text x={px(i)} y={H - 4} textAnchor="middle" className="fill-text-faint font-mono num" fontSize={10}>
                      {formatDay(n.known_at)}
                    </text>
                  </g>
                ),
              )}
            </svg>
            <ul className="space-y-0.5 text-xs text-text-muted">
              {[...c.links].reverse().map((p) => (
                <li key={p.later_id}>
                  {dayText(p.earlier_known_at)} to {dayText(p.later_known_at)} ({p.elapsed_days} days):{" "}
                  {p.comparable ? (
                    p.change == null ? (
                      p.note
                    ) : (
                      <span className="num">
                        {p.statistic} {signed(num(p.change), 2)} ({p.change_pct == null ? (p.pct_note ?? "percent undefined") : `${signed(num(p.change_pct), 1)}%`}), {p.direction}; {p.analyst_count_change == null ? (p.analyst_count_note ?? "analyst count not stated") : `analysts ${signed(p.analyst_count_change, 0)}`}
                      </span>
                    )
                  ) : (
                    <strong className="font-medium text-text">Break, no revision: {p.break_reasons.map((r) => BREAK_LABEL[r] ?? r).join("; ")}.</strong>
                  )}
                </li>
              ))}
            </ul>
          </figure>
        );
      })}
      <p className="text-xs text-text-muted">Columns are snapshots in order, not equal spans of time. A broken mark means levels on either side were measured differently and are not compared.</p>
    </div>
  );
}

// ----------------------------------------------------------------------------- surprise
/** Pre-release estimate to surprise to reported actual, with the formula and the snapshot date that was used. */
export function SurpriseBridge({ events, lines }: { events: readonly EventComparison[]; lines: readonly EstimateLine[] }) {
  if (events.length === 0) return <p className="text-sm text-text-muted">No consensus snapshot is known for a reported period at this date.</p>;
  const byId = new Map(lines.map((l) => [l.id, l]));
  return (
    <div className="space-y-5">
      {events.map((e) => {
        const est = num(e.estimate);
        const act = num(e.actual);
        const link = e.estimate_value_id != null ? byId.get(e.estimate_value_id)?.document_url : null;
        return (
          <figure key={`${e.metric}${e.period_end}`} className="space-y-2">
            <figcaption className="text-sm font-medium">
              {e.metric}, {e.fiscal_period ?? e.period_end} <span className="text-xs font-normal text-text-faint">· {STATUS_LABEL[e.status] ?? e.status}</span>
            </figcaption>
            {e.status === "scored" && est != null && act != null ? (
              <>
                <ol className="grid grid-cols-1 gap-y-1 border-y border-border py-2 text-sm sm:grid-cols-3">
                  <li>
                    <span className="block text-xs text-text-faint">Estimate known {dayText(e.estimate_known_at)} ({e.statistic})</span>
                    <span className="num">{formatBase(e.estimate, e.unit, e.currency)}</span>
                  </li>
                  <li>
                    <span className="block text-xs text-text-faint">Surprise</span>
                    <span className="num font-medium">
                      {formatBase(e.surprise, e.unit, e.currency)} {e.surprise_pct != null ? `(${signed(num(e.surprise_pct), 1)}%)` : `(${e.pct_note ?? "percent undefined"})`}
                    </span>
                  </li>
                  <li>
                    <span className="block text-xs text-text-faint">Reported {dayText(e.release_at)}</span>
                    <span className="num">{formatBase(e.actual, e.unit, e.currency)}</span>
                  </li>
                </ol>
                <svg viewBox={`0 0 ${W} 30`} className="block h-8 w-full" role="img" aria-label={`Estimate ${formatBase(e.estimate, e.unit, e.currency)}, actual ${formatBase(e.actual, e.unit, e.currency)}`}>
                  {(() => {
                    const lo = Math.min(est, act);
                    const hi = Math.max(est, act);
                    const pad = (hi - lo || Math.abs(hi) * 0.01 || 1) * 0.6;
                    const x = (v: number) => PAD + ((v - (lo - pad)) / (hi - lo + 2 * pad)) * (W - 2 * PAD);
                    return (
                      <g>
                        <line x1={x(est)} x2={x(act)} y1={15} y2={15} strokeWidth={2} className="stroke-chart-line" />
                        <rect x={x(est) - 6} y={9} width={12} height={12} strokeWidth={1.5} className="fill-surface-raised stroke-chart-line" />
                        <rect x={x(act) - 5} y={10} width={10} height={10} className="fill-chart-line" />
                      </g>
                    );
                  })()}
                </svg>
              </>
            ) : (
              <p className="text-sm text-text-muted">{e.status === "no_prior_estimate" && e.later_files_only ? "Only a file published after the release exists, so it is not used to score this surprise." : (STATUS_LABEL[e.status] ?? e.status)}.</p>
            )}
            <p className="text-xs text-text-muted">
              <span className="font-medium">Formula {e.formula_version}.</span> {e.formula}
              {link ? (
                <>
                  {" "}
                  <a className={LINK} href={safeHref(link) ?? undefined} target="_blank" rel="noopener noreferrer">IR file used</a>
                </>
              ) : null}
            </p>
            {e.warnings.length ? <ul className="list-disc space-y-0.5 pl-5 text-xs text-text-muted">{e.warnings.map((w) => <li key={w}>{w}</li>)}</ul> : null}
          </figure>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------- segments
/** Segment actual against estimate in aligned rows on one axis. A missing estimate is left blank, never zero. */
export function SegmentMultiples({ bridge }: { bridge: SegmentBridge | undefined }) {
  if (!bridge) return <p className="text-sm text-text-muted">No segment estimates are known at this date.</p>;
  const ordered = [...bridge.lines.filter((l) => !l.non_additive), ...bridge.lines.filter((l) => l.non_additive)];
  const nums = bridge.lines.filter((l) => !l.non_additive).flatMap((l) => [num(l.estimate), num(l.actual)]).filter((n): n is number => n != null);
  const hi = nums.length ? Math.max(...nums) : 1;
  const x = (v: number) => PAD + (v / (hi * 1.08)) * (W - 2 * PAD);
  return (
    <figure className="space-y-2">
      <p className="text-xs text-text-muted">
        {bridge.metric}, {bridge.fiscal_period ?? bridge.period_end}. Every row shares one axis starting at zero. Hollow square is the estimate known before the release, filled square is the reported actual.
      </p>
      {ordered.length === 0 ? <p className="text-sm text-text-muted">{bridge.notes.join(" ")}</p> : null}
      <ul className="space-y-2">
        {ordered.map((l) => {
          const e = num(l.estimate);
          const a = num(l.actual);
          return (
            <li key={l.segment} className="grid gap-x-4 sm:grid-cols-[9rem_1fr]">
              <div className="text-sm">
                <span className="font-medium">{l.segment}</span>
                <span className="block text-xs text-text-faint">{STATUS_LABEL[l.status] ?? l.status}</span>
              </div>
              <div>
                {l.non_additive ? null : (
                  <svg viewBox={`0 0 ${W} 24`} className="block h-6 w-full" role="img" aria-label={`${l.segment} estimate and actual`}>
                    <line x1={PAD} x2={W - PAD} y1={12} y2={12} className="stroke-border" strokeWidth={0.5} strokeDasharray={e == null ? "3 3" : undefined} />
                    {e != null && a != null ? <line x1={x(e)} x2={x(a)} y1={12} y2={12} strokeWidth={2} className="stroke-chart-line" /> : null}
                    {e != null ? <rect x={x(e) - 5} y={7} width={10} height={10} strokeWidth={1.5} className="fill-surface-raised stroke-chart-line" /> : null}
                    {a != null ? <rect x={x(a) - 4} y={8} width={8} height={8} className="fill-chart-line" /> : null}
                  </svg>
                )}
                <p className="num text-xs text-text-muted">
                  estimate {l.estimate == null ? "not published" : formatBase(l.estimate, "amount", null)} · actual {l.actual == null ? "not loaded" : formatBase(l.actual, "amount", null)}
                  {l.delta != null ? ` · difference ${formatBase(l.delta, "amount", null)}${l.delta_pct != null ? ` (${signed(num(l.delta_pct), 1)}%)` : ""}` : ""}
                </p>
                {l.flags.length ? <p className="text-xs text-text-faint">{l.flags.join("; ")}.</p> : null}
              </div>
            </li>
          );
        })}
      </ul>
      <ul className="space-y-0.5 text-xs text-text-muted">
        {bridge.notes.map((n) => <li key={n}>{n}</li>)}
        {bridge.additive_estimate_total != null ? <li className="num">Additive segments with both an estimate and an actual: estimate {formatBase(bridge.additive_estimate_total, "amount", null)}, actual {formatBase(bridge.additive_actual_total, "amount", null)}.</li> : null}
        {bridge.inputs.length ? <li>Inputs cited: {bridge.inputs.join(", ")}.</li> : null}
      </ul>
      <SrTable caption={`${bridge.metric} by segment`} columns={["Segment", "Estimate", "Actual", "Difference", "Status"]} rows={bridge.lines.map((l) => [l.segment, l.estimate == null ? "not published" : formatBase(l.estimate, "amount", null), l.actual == null ? "not loaded" : formatBase(l.actual, "amount", null), l.delta == null ? "none" : formatBase(l.delta, "amount", null), STATUS_LABEL[l.status] ?? l.status])} />
    </figure>
  );
}

// ------------------------------------------------------------------ coverage and sources
export function CoverageList({ gaps, sources }: { gaps: readonly CoverageGap[]; sources: readonly ConsensusSource[] }) {
  return (
    <div className="space-y-3">
      <ul className="space-y-1 text-sm">
        {gaps.length === 0 ? <li className="text-text-muted">Every lane holds something at this date.</li> : null}
        {gaps.map((g) => (
          <li key={g.kind + g.detail}>
            <span className="font-medium">{g.kind === "named_estimates" ? "Named analyst estimates" : (LANE_META[g.kind as Series]?.label ?? g.kind)}:</span> <span className="text-text-muted">{g.detail}</span>
          </li>
        ))}
      </ul>
      <h4 className="text-sm font-medium">Where the files come from</h4>
      {sources.length === 0 ? <p className="text-sm text-text-muted">No consensus file source is registered for this company. That is a coverage gap, not a licensing block.</p> : null}
      <ul className="space-y-1 text-sm text-text-muted">
        {sources.map((s) => (
          <li key={s.source_key}>
            <a className={LINK} href={safeHref(s.url) ?? undefined} target="_blank" rel="noopener noreferrer">{s.publisher}</a> · {s.format.toUpperCase()} · {s.origin === "third_party_brokers" ? "values come from third-party brokers" : s.origin === "issuer_compiled" ? "compiled by the issuer" : "origin not stated"} · {ACCESS_LABEL[s.access_state] ?? s.access_state}
            {s.cadence ? ` · ${s.cadence}` : ""}
            {s.selected_metrics_only ? " · only selected metrics" : ""}
            {s.selected_brokers_only ? " · only selected brokers" : ""}
            {s.gap_reason ? ` · ${s.gap_reason}` : ""}
            {s.tested_on ? ` · tested ${dayText(s.tested_on)}` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function GateNote({ gate }: { gate: RevisionGate }) {
  return (
    <p role="note" className={cn("border px-3 py-2 text-sm", gate.met ? "border-border bg-surface-muted" : "border-dashed border-border bg-surface-muted")}>
      <strong>{gateWords(gate)}</strong> Alerts and factor-like screens built on revisions stay off until at least {gate.needed} reviewed snapshot pairs are {Number(gate.bar) * 100}% correct or better.
    </p>
  );
}

export function BasisWarnings({ lines }: { lines: readonly EstimateLine[] }) {
  const warned = lines.map((l) => ({ l, w: lineWarnings(l) })).filter((x) => x.w.length > 0);
  if (warned.length === 0) return <p className="text-sm text-text-muted">No basis or panel warnings for the lines shown.</p>;
  const seen = new Set<string>();
  return (
    <ul className="space-y-1 text-sm text-text-muted">
      {warned.map(({ l, w }) => {
        const k = `${l.metric}|${l.fiscal_period}|${l.series}|${w.join()}|${l.snapshot_id}`;
        if (seen.has(k)) return null;
        seen.add(k);
        return (
          <li key={k}>
            <span className="font-medium text-text">{l.metric}{l.segment ? `, ${l.segment}` : ""}, {l.fiscal_period}</span> ({LANE_META[l.series as Series].label}, known {dayText(l.known_at)}): {w.join("; ")}.
          </li>
        );
      })}
    </ul>
  );
}
