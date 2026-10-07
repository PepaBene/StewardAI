"use client";

import { Sankey } from "@/components/charts";
import { StateBadge } from "@/components/disclosure/panels";
import { formatDay } from "@/lib/disclosure/model";
import {
  formatPercent,
  formatShares,
  lagLabel,
  laneBar,
  LANE_CAVEAT,
  MOVE_LABEL,
  rankFlowGraph,
  stakeDeltas,
  stakesAt,
  type HolderBook,
  type Lane,
  type OwnershipView,
  type StakeChainView,
  type StakeEvent,
} from "@/lib/disclosure/ownership";
import { cn } from "@/lib/utils";

/**
 * One bar per ownership system, each against the same stated share count. The filled part is what
 * that system reports; the hatched remainder is what it does not account for, drawn as an open gap
 * because an unknown is not an owner. The bars are never stacked into one total: the systems overlap.
 */
function LaneBar({ lane, denominator }: { lane: Lane; denominator: string | null }) {
  const bar = laneBar(lane);
  if (!bar) {
    return <p className="text-sm text-text-muted">{denominator ? "No figure to draw." : "No share count is known for the company at this date, so no bar is drawn."}</p>;
  }
  return (
    <div>
      <div className="relative h-5 w-full border border-border bg-surface-raised" role="img" aria-label={`${formatPercent(lane.percent_of_denominator)} of shares reported by this lane`}>
        <div className="absolute inset-y-0 left-0 bg-accent" style={{ width: `${bar.fill}%` }} />
        <div
          className="absolute inset-y-0 right-0"
          style={{ left: `${bar.fill}%`, backgroundImage: "repeating-linear-gradient(135deg, var(--chart-grid) 0 3px, transparent 3px 7px)" }}
        />
        {bar.over ? <div className="absolute inset-y-0 right-0 w-1 bg-warning-signal" /> : null}
      </div>
      <div className="mt-1 flex flex-wrap justify-between gap-x-4 text-xs text-text-muted">
        <span><span className="num font-medium text-text">{formatPercent(lane.percent_of_denominator)}</span> reported ({formatShares(lane.total_shares)} shares)</span>
        <span>{lane.remainder_percent != null ? <><span className="num">{formatPercent(lane.remainder_percent)}</span> not accounted for here</> : "No remainder: see notes"}</span>
      </div>
    </div>
  );
}

function EntriesTable({ lane }: { lane: Lane }) {
  if (lane.entries.length === 0) return null;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[600px] border-collapse text-sm">
        <caption className="sr-only">{lane.title}: holders, with the date each figure is measured on and when it was filed</caption>
        <thead>
          <tr className="border-b border-border text-left">
            <th scope="col" className="py-1.5 pr-3 font-medium">Holder</th>
            <th scope="col" className="py-1.5 pr-3 text-right font-medium">Shares</th>
            <th scope="col" className="py-1.5 pr-3 text-right font-medium">Reported %</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">Measured on</th>
            <th scope="col" className="py-1.5 font-medium">Filed</th>
          </tr>
        </thead>
        <tbody>
          {lane.entries.map((e) => (
            <tr key={`${e.holder}-${e.known_at}`} className="border-b border-border-soft align-top">
              <th scope="row" className="py-1.5 pr-3 text-left font-medium">
                {e.holder}
                {e.amended ? <span className="block text-xs font-normal text-text-muted">amended</span> : null}
              </th>
              <td className={cn("num py-1.5 pr-3 text-right", e.shares == null && "text-text-muted")}>{formatShares(e.shares)}</td>
              <td className="num py-1.5 pr-3 text-right">{formatPercent(e.reported_percent)}</td>
              <td className="num py-1.5 pr-3 whitespace-nowrap">{formatDay(e.report_date)}</td>
              <td className="py-1.5 whitespace-nowrap">
                <span className="num">{formatDay(e.filed)}</span>
                <span className="block text-xs text-text-muted">{lagLabel(e.lag_days)}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function OwnershipLanes({ view }: { view: OwnershipView }) {
  return (
    <div className="space-y-6">
      <p className="text-sm">
        {view.denominator ? (
          <>
            Shares outstanding: <strong className="num">{formatShares(view.denominator)}</strong>, as of <span className="num">{formatDay(view.denominator_as_of)}</span>. Every bar below is measured against this number.
          </>
        ) : (
          "No share count was known for this company at the chosen date, so shares are listed but not turned into percentages."
        )}
      </p>
      {view.lanes.map((lane) => (
        <section key={lane.key} aria-labelledby={`lane-${lane.key}`} className="space-y-2 border-t border-border-soft pt-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 id={`lane-${lane.key}`} className="text-base font-semibold">{lane.title}</h3>
            {lane.status === "not_covered" ? <StateBadge state="not_covered" /> : lane.status === "no_data" ? <span className="text-xs text-text-muted">Nothing known yet</span> : null}
          </div>
          <p className="max-w-[72ch] text-sm text-text-muted">{lane.basis}</p>
          {lane.status === "covered" ? (
            <>
              <LaneBar lane={lane} denominator={view.denominator} />
              <EntriesTable lane={lane} />
            </>
          ) : null}
          <ul className="space-y-0.5 text-xs text-text-muted">
            {lane.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
            {lane.status === "covered" ? <li>{LANE_CAVEAT[lane.key]}</li> : null}
          </ul>
        </section>
      ))}
      {view.warnings.length ? (
        <ul role="note" className="space-y-0.5 border border-dashed border-border bg-surface-muted px-3 py-2 text-sm">
          {view.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

// ------------------------------------------------------------------------------- common quarter
const CONFIDENTIAL_LABEL: Record<string, string> = {
  none_indicated: "No confidential treatment indicated",
  requested: "Confidential treatment requested: some holdings may be left out",
  unknown: "Confidential treatment not stated",
};

/**
 * Which quarter the manager lane shows and why, and what each manager’s own report says about its
 * size. A manager who has not filed that quarter is named, never carried forward from an older one.
 */
export function QuarterNote({ view }: { view: OwnershipView }) {
  const q = view.quarter;
  if (q.period == null) return <p className="text-sm text-text-muted">No manager report is known at this date.</p>;
  return (
    <div className="space-y-3 text-sm">
      <p className="max-w-[72ch]">
        The manager lane shows the quarter ending <strong className="num">{formatDay(q.period)}</strong>: <span className="num">{q.managers_filed}</span> managers ({(Number(q.share_filed) * 100).toFixed(0)}% of those we follow) had filed it.
        {q.fell_back ? " No quarter reached 80%, so the best-covered one is shown and the lane is incomplete." : ""}
      </p>
      {q.not_yet_filed.length ? (
        <p className="max-w-[72ch]">
          <strong>Not in this quarter:</strong> {q.not_yet_filed.join(", ")}. {q.not_yet_filed.length === 1 ? "Its" : "Their"} older holdings are not used to fill the gap.
        </p>
      ) : null}
      {q.reports.length ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] border-collapse">
            <caption className="sr-only">What each manager’s own report says about its size, against the lines kept for this company</caption>
            <thead>
              <tr className="border-b border-border text-left">
                <th scope="col" className="py-1.5 pr-3 font-medium">Manager</th>
                <th scope="col" className="py-1.5 pr-3 text-right font-medium">Lines for this company</th>
                <th scope="col" className="py-1.5 pr-3 text-right font-medium">Lines in the whole report</th>
                <th scope="col" className="py-1.5 pr-3 text-right font-medium">Reported total value</th>
                <th scope="col" className="py-1.5 font-medium">Confidential treatment</th>
              </tr>
            </thead>
            <tbody>
              {q.reports.map((r) => (
                <tr key={`${r.manager}-${r.report_date}`} className="border-b border-border-soft align-top">
                  <th scope="row" className="py-1.5 pr-3 text-left font-medium">{r.manager}</th>
                  <td className="num py-1.5 pr-3 text-right">{r.lines_stored_for_ticker}</td>
                  <td className="num py-1.5 pr-3 text-right">{r.lines_reported == null ? "Not stated" : r.lines_reported.toLocaleString("en-GB")}</td>
                  <td className="num py-1.5 pr-3 text-right">{r.total_value_reported_usd == null ? "Not stated" : `$${Number(r.total_value_reported_usd).toLocaleString("en-GB")}`}</td>
                  <td className="py-1.5">{CONFIDENTIAL_LABEL[r.confidential_treatment] ?? r.confidential_treatment}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <p className="max-w-[72ch] text-xs text-text-muted">“Not stated” means the report gave no figure. It is not zero.</p>
    </div>
  );
}

// ------------------------------------------------------------------------------------ filing chain
const SCHEDULE_NAME = { "13D": "Schedule 13D", "13G": "Schedule 13G" } as const;

/** One filer’s 13D/G filings about a company, oldest first, with what changed at each step. */
export function StakeChain({ chain }: { chain: StakeChainView }) {
  if (chain.steps.length === 0) return <p className="text-sm text-text-muted">This filer has no 13D or 13G filing about the company known at this date.</p>;
  return (
    <ol className="space-y-4">
      {chain.steps.map((s) => (
        <li key={s.accession} className="space-y-1.5 border border-border bg-surface-raised p-3 text-sm">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="font-medium">
              {SCHEDULE_NAME[s.schedule]}
              {s.is_amendment ? `, amendment${s.amendment_no ? ` ${s.amendment_no}` : ""}` : ", first filing"}
              <span className="num font-normal text-text-muted"> · filed {formatDay(s.filed)}</span>
            </p>
            <p className="num text-base font-semibold">{s.stake_percent == null ? "No percent reported" : `${Number(s.stake_percent).toFixed(2)}%`}</p>
          </div>
          <p className="text-xs text-text-muted">
            {s.class_title ?? "Class not stated"}
            {s.event_date ? <> · event on <span className="num">{formatDay(s.event_date)}</span></> : null}
            {!s.linked_to_previous && s.prior_accession == null && chain.steps[0] !== s ? " · not linked to the filing before it" : ""}
          </p>
          <p className="text-xs">
            {s.reported_delta_points != null ? <>Moved <span className="num">{Number(s.reported_delta_points) >= 0 ? "+" : "−"}{Math.abs(Number(s.reported_delta_points)).toFixed(2)}</span> points from the filing before.</> : null}
            {s.delta_note ? <span className="text-text-muted"> {s.delta_note}</span> : null}
          </p>
          {s.purpose ? (
            <blockquote className="max-w-[72ch] border-l-2 border-border pl-3">
              {s.purpose_changed === true ? <span className="mb-0.5 block text-xs font-medium">The stated purpose changed from the filing before.</span> : null}
              {s.purpose}
            </blockquote>
          ) : null}
          {s.holders.length ? (
            <details>
              <summary className="cursor-pointer text-xs text-accent-ink">{s.holders.length} reporting {s.holders.length === 1 ? "person" : "people"}</summary>
              <ul className="mt-1 space-y-0.5 text-xs">
                {s.holders.map((h) => (
                  <li key={h.name}>
                    {h.name}{h.member_of_group ? " (group member)" : ""}: <span className="num">{h.percent == null ? "no percent" : `${Number(h.percent).toFixed(2)}%`}</span>
                  </li>
                ))}
                <li className="text-text-muted">Group members report overlapping shares, so these are not added.</li>
              </ul>
            </details>
          ) : null}
          {s.changes.length ? (
            <ul className="space-y-0.5 text-xs">
              {s.changes.map((c) => (
                <li key={`${c.change_type}-${c.person}-${c.old}-${c.new}`}>
                  {c.change_type.replaceAll("_", " ")}{c.person ? ` (${c.person})` : ""}: {c.old ?? "none"} → {c.new ?? "none"}{c.detail ? `. ${c.detail}` : ""}
                </li>
              ))}
            </ul>
          ) : null}
          {s.exhibits.length ? (
            <ul className="space-y-0.5 text-xs text-text-muted">
              {s.exhibits.map((x) => (
                <li key={x.label}>
                  {x.url ? <a href={x.url} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">{x.label}</a> : x.label}
                  {x.description ? `: ${x.description}` : ""}
                </li>
              ))}
            </ul>
          ) : null}
          <p className="text-xs">
            <a href={s.source_url} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">Open the filing</a>
          </p>
        </li>
      ))}
    </ol>
  );
}

// ---------------------------------------------------------------------------------- rank flow
export function RankFlow({ prev, cur, prevLabel, curLabel }: { prev: HolderBook; cur: HolderBook; prevLabel: string; curLabel: string }) {
  const g = rankFlowGraph(prev, cur, 6);
  if (g.moves.length === 0) return <p className="text-sm text-text-muted">Two quarters of manager reports are needed to show movement.</p>;
  return (
    <div className="space-y-3">
      <div className="flex justify-between text-xs text-text-muted" aria-hidden="true">
        <span>{prevLabel}</span>
        <span>{curLabel}</span>
      </div>
      <Sankey nodes={g.nodes} links={g.links} formatValue={(v) => formatShares(String(v))} label={`Top holders, ${prevLabel} to ${curLabel}`} height={300} className="w-full" />
      <table className="w-full max-w-2xl border-collapse text-sm">
        <caption className="sr-only">Change in reported holdings by manager, {prevLabel} to {curLabel}</caption>
        <thead>
          <tr className="border-b border-border text-left">
            <th scope="col" className="py-1.5 pr-3 font-medium">Manager</th>
            <th scope="col" className="py-1.5 pr-3 text-right font-medium">{prevLabel}</th>
            <th scope="col" className="py-1.5 pr-3 text-right font-medium">{curLabel}</th>
            <th scope="col" className="py-1.5 font-medium">What was reported</th>
          </tr>
        </thead>
        <tbody>
          {g.moves.map((m) => (
            <tr key={m.manager} className="border-b border-border-soft">
              <th scope="row" className="py-1.5 pr-3 text-left font-medium">{m.manager}</th>
              <td className="num py-1.5 pr-3 text-right">{m.prev == null ? "—" : formatShares(String(m.prev))}</td>
              <td className="num py-1.5 pr-3 text-right">{m.cur == null ? "—" : formatShares(String(m.cur))}</td>
              <td className="py-1.5">{MOVE_LABEL[m.kind]}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-text-muted">This lists what managers reported each quarter. It does not say why a position changed.</p>
    </div>
  );
}

// ------------------------------------------------------------------------------ stake timeline
const W = 720;
const H = 190;
const PAD = { l: 44, r: 16, t: 14, b: 26 };

/**
 * One holder's control filings over time: the reported percent after each filing, as a step, with a
 * mark per filing. Filings after the cutoff are hollow and the step stops at the last known one.
 * A filing that reports no percentage has no mark on the line (a gap, not a drop to zero).
 */
export function StakeTimeline({ events, cutoffMs }: { events: readonly StakeEvent[]; cutoffMs: number }) {
  if (events.length === 0) return <p className="text-sm text-text-muted">No control filings are on record for this company.</p>;
  const times = events.map((e) => Date.parse(e.known_at));
  const lo = Math.min(...times);
  const hi = Math.max(...times, cutoffMs);
  const span = Math.max(hi - lo, 1);
  const top = Math.max(10, ...events.map((e) => e.percent ?? 0)) * 1.15;
  const x = (t: number) => PAD.l + ((t - lo) / span) * (W - PAD.l - PAD.r);
  const y = (p: number) => PAD.t + (1 - p / top) * (H - PAD.t - PAD.b);
  const known = stakesAt(events, cutoffMs);
  const knownWithPct = known.filter((e) => e.percent != null);
  const steps = knownWithPct.map((e, i) => `${i === 0 ? "M" : "L"} ${x(Date.parse(e.known_at))} ${i === 0 ? y(e.percent!) : y(knownWithPct[i - 1].percent!)} L ${x(Date.parse(e.known_at))} ${y(e.percent!)}`).join(" ");
  const tail = knownWithPct.length ? `L ${x(Math.min(cutoffMs, hi))} ${y(knownWithPct[knownWithPct.length - 1].percent!)}` : "";
  const deltas = stakeDeltas(known);
  const yearTicks = [...new Set(events.map((e) => new Date(e.known_at).getUTCFullYear()))];
  return (
    <figure className="space-y-2">
      <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full max-w-3xl" role="img" aria-label={`Reported stake of ${events[0].holder} over time`}>
        {[5, 10].filter((p) => p < top).map((p) => (
          <g key={p}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(p)} y2={y(p)} className="stroke-chart-grid" strokeDasharray={p === 5 ? undefined : "2 4"} />
            <text x={PAD.l - 6} y={y(p)} textAnchor="end" dominantBaseline="middle" className="num fill-text-muted text-[11px]">{p}%</text>
          </g>
        ))}
        {yearTicks.map((yr) => (
          <text key={yr} x={x(Date.UTC(yr, 0, 1) < lo ? lo : Date.UTC(yr, 0, 1))} y={H - 8} className="num fill-text-muted text-[11px]">{yr}</text>
        ))}
        <path d={`${steps} ${tail}`} fill="none" className="stroke-chart-line" strokeWidth={2} />
        <line x1={x(Math.min(cutoffMs, hi))} x2={x(Math.min(cutoffMs, hi))} y1={PAD.t} y2={H - PAD.b} className="stroke-text" strokeWidth={1.5} />
        {events.map((e) => {
          const t = Date.parse(e.known_at);
          const isKnown = t <= cutoffMs;
          if (e.percent == null) return null;
          return (
            <g key={`${e.form}-${e.known_at}`}>
              <title>{`${e.form}, ${e.percent}% reported, known ${formatDay(e.known_at)}${isKnown ? "" : " (after the chosen date)"}`}</title>
              <rect x={x(t) - 5} y={y(e.percent) - 5} width={10} height={10} strokeWidth={2} className={cn("stroke-chart-line", isKnown ? "fill-chart-line" : "fill-surface-raised")} />
            </g>
          );
        })}
      </svg>
      <table className="w-full max-w-2xl border-collapse text-sm">
        <caption className="sr-only">Control filings by {events[0].holder}, with the reported percent and the change from the filing before</caption>
        <thead>
          <tr className="border-b border-border text-left">
            <th scope="col" className="py-1.5 pr-3 font-medium">Filing</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">Known from</th>
            <th scope="col" className="py-1.5 pr-3 text-right font-medium">Reported %</th>
            <th scope="col" className="py-1.5 text-right font-medium">Change</th>
          </tr>
        </thead>
        <tbody>
          {known.map((e, i) => {
            const d = i === 0 ? null : deltas[i - 1].delta;
            return (
              <tr key={`${e.form}-${e.known_at}`} className="border-b border-border-soft">
                <th scope="row" className="py-1.5 pr-3 text-left font-medium">{e.form}{e.amendment ? ` no. ${e.amendment}` : ""}</th>
                <td className="num py-1.5 pr-3 whitespace-nowrap">{formatDay(e.known_at)}</td>
                <td className="num py-1.5 pr-3 text-right">{formatPercent(e.percent)}</td>
                <td className="num py-1.5 text-right">{d == null ? "—" : `${d >= 0 ? "+" : ""}${d.toFixed(1)} pts`}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="text-xs text-text-muted">
        Reported stake only. A change is shown when both filings give a percentage for the same class of shares. Hollow marks were filed after the chosen date.
      </p>
    </figure>
  );
}
