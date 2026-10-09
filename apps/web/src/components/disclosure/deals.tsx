"use client";

import { useState } from "react";

import { WaterfallChart } from "@/components/charts";
import { AXIS_TEXT, CATEGORICAL_FILL } from "@/components/charts/shared";
import { SELECT_CLASS } from "@/components/reference/form-bits";
import { formatDay } from "@/lib/disclosure/model";
import {
  ACTIVE,
  bridgeParts,
  CONFIDENCE_LABEL,
  describeChange,
  FLAG_LABEL,
  formatTerm,
  impactValue,
  impactWaterfalls,
  money,
  num,
  ORIGIN_LABEL,
  percent,
  STATE_LABEL,
  timelineLayout,
  TYPE_LABEL,
  type Deal,
  type DealFilter,
  type DealTransition,
  type Impact,
  type PortfolioDeals,
  type TermVersion,
} from "@/lib/disclosure/deals";
import { cn } from "@/lib/utils";

const TH = "py-1.5 pr-3 text-left font-medium";
const TD = "py-1.5 pr-3 align-top";
const TABLE = "w-full border-collapse text-sm";
const HEAD_ROW = "border-b border-border text-left";
const BODY_ROW = "border-b border-border-soft";
const STALE_AFTER_DAYS = 120;

type Kind = "announced" | "management_projection" | "owner_scenario" | "unknown";

// ------------------------------------------------------------------------- encodings and legend
/** Defines the hatch once for the page. Marks elsewhere refer to it as url(#deal-hatch). */
export function DealDefs() {
  return (
    <svg width={0} height={0} aria-hidden="true" focusable="false" className="absolute">
      <defs>
        <pattern id="deal-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="6" height="6" className="fill-surface" />
          <line x1="0" y1="0" x2="0" y2="6" strokeWidth="2" className="stroke-chart-line" />
        </pattern>
      </defs>
    </svg>
  );
}

function Swatch({ kind }: { kind: Kind }) {
  return (
    <svg width={16} height={12} aria-hidden="true" className="shrink-0">
      {kind === "announced" ? <rect x={1} y={1} width={14} height={10} className="fill-chart-line" /> : null}
      {kind === "management_projection" ? <rect x={1} y={1} width={14} height={10} fill="url(#deal-hatch)" strokeWidth={1} className="stroke-chart-line" /> : null}
      {kind === "owner_scenario" ? <rect x={1} y={1} width={14} height={10} strokeWidth={1.25} strokeDasharray="3 2" className="fill-surface stroke-chart-line" /> : null}
      {kind === "unknown" ? <rect x={1} y={1} width={14} height={10} strokeWidth={1} className="fill-surface stroke-text-muted" /> : null}
    </svg>
  );
}

export function EncodingLegend() {
  const items: { kind: Kind; label: string }[] = [
    { kind: "announced", label: "Announced terms (solid)" },
    { kind: "management_projection", label: "Management projection (hatched)" },
    { kind: "owner_scenario", label: "Your scenario (dashed outline)" },
    { kind: "unknown", label: "Not stated (open outline)" },
  ];
  return (
    <ul aria-label="How to read the marks" className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-text-muted">
      {items.map((i) => (
        <li key={i.kind} className="inline-flex items-center gap-1.5">
          <Swatch kind={i.kind} />
          {i.label}
        </li>
      ))}
    </ul>
  );
}

const originKind = (o: Impact["origin"]): Kind => o;

// ----------------------------------------------------------------------------- filters and list
export function DealFilters({ value, onChange, types, currencies = [] }: { value: DealFilter; onChange: (f: DealFilter) => void; types: readonly string[]; currencies?: readonly string[] }) {
  return (
    <div className="flex flex-wrap items-end gap-3 text-sm">
      <label className="flex flex-col gap-1">
        <span className="font-medium">Type</span>
        <select className={SELECT_CLASS} value={value.type} onChange={(e) => onChange({ ...value, type: e.target.value })}>
          <option value="">All types</option>
          {types.map((t) => (
            <option key={t} value={t}>{TYPE_LABEL[t] ?? t}</option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1">
        <span className="font-medium">State</span>
        <select className={SELECT_CLASS} value={value.state} onChange={(e) => onChange({ ...value, state: e.target.value })}>
          <option value="">All states</option>
          {Object.entries(STATE_LABEL).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
          <option value="unknown">State not resolved</option>
        </select>
      </label>
      <label className="flex flex-col gap-1">
        <span className="font-medium">Currency</span>
        <select className={SELECT_CLASS} value={value.currency ?? ""} onChange={(e) => onChange({ ...value, currency: e.target.value })}>
          <option value="">All currencies</option>
          {currencies.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1">
        <span className="font-medium">Stated size at least</span>
        <input
          type="number"
          min={0}
          inputMode="decimal"
          className={cn(SELECT_CLASS, "num w-40")}
          value={value.minSize ?? ""}
          onChange={(e) => onChange({ ...value, minSize: e.target.value === "" ? null : Math.max(0, Number(e.target.value)) })}
          aria-describedby="deal-size-note"
        />
        <span id="deal-size-note" className="text-xs text-text-muted">In the deal’s own currency. Deals with no stated size are hidden while this is set.</span>
      </label>
    </div>
  );
}

export function staleDays(deal: Deal, asOf: string): number | null {
  if (!deal.state || !ACTIVE.has(deal.state)) return null;
  const last = Math.max(...deal.transitions.map((t) => Date.parse(t.known_at)), ...deal.term_versions.map((v) => Date.parse(v.known_at)));
  const days = Math.floor((Date.parse(asOf) - last) / 86_400_000);
  return days > STALE_AFTER_DAYS ? days : null;
}

export function DealTable({ deals, selected, onSelect, asOf }: { deals: readonly Deal[]; selected: number | null; onSelect: (id: number) => void; asOf: string }) {
  if (deals.length === 0) return null;
  return (
    <div className="overflow-x-auto">
      <table className={cn(TABLE, "min-w-[640px]")}>
        <caption className="sr-only">Deals known at this date: choose one to see its timeline and terms</caption>
        <thead>
          <tr className={HEAD_ROW}>
            <th scope="col" className={TH}>Deal</th>
            <th scope="col" className={TH}>Type</th>
            <th scope="col" className={TH}>State</th>
            <th scope="col" className={cn(TH, "text-right")}>Stated size</th>
            <th scope="col" className={TH}>Support</th>
          </tr>
        </thead>
        <tbody>
          {deals.map((d) => {
            const stale = staleDays(d, asOf);
            return (
              <tr key={d.id} className={cn(BODY_ROW, selected === d.id && "bg-surface-muted")}>
                <th scope="row" className={cn(TD, "font-medium")}>
                  <button type="button" onClick={() => onSelect(d.id)} aria-pressed={selected === d.id} className="text-left underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus">
                    {d.title}
                  </button>
                </th>
                <td className={TD}>{d.type_label}</td>
                <td className={TD}>
                  {d.state ? STATE_LABEL[d.state] : <span className="text-text-muted">Not resolved</span>}
                  {stale ? <span className="block text-xs text-warning-signal">No update for {stale} days: check the filings.</span> : null}
                </td>
                <td className={cn(TD, "num text-right")}>{d.size ? <>{money(d.size, d.currency)}{d.bridge.total_is_lower_bound && d.bridge.unstated.length > 0 && d.deal_type !== "buyback" ? <span className="text-text-muted"> or more</span> : null}</> : <span className="text-text-muted">not stated</span>}</td>
                <td className={cn(TD, "text-text-muted")}>{CONFIDENCE_LABEL[d.confidence]}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ----------------------------------------------------------------------------------- timeline
const W = 860;
const RAIL = 100;
const TRACK = 200;
const H = 262;

function StateMark({ state, x, active }: { state: string; x: number; active: boolean }) {
  return (
    <g transform={`translate(${x} ${RAIL})`}>
      <rect x={-12} y={-12} width={24} height={24} strokeWidth={2} className={active ? "fill-none stroke-focus" : "fill-none stroke-transparent"} />
      {state === "terminated" ? (
        <>
          <rect x={-8} y={-8} width={16} height={16} strokeWidth={1.5} className="fill-surface stroke-loss" />
          <path d="M -5 -5 L 5 5 M 5 -5 L -5 5" strokeWidth={1.75} className="stroke-loss" />
        </>
      ) : state === "contested" ? (
        <>
          <path d="M 0 -10 L 10 0 L 0 10 L -10 0 Z" strokeWidth={2} className="fill-surface stroke-warning-signal" />
          <path d="M 0 -4 L 0 2 M 0 5 L 0 5.5" strokeWidth={1.75} className="stroke-warning-signal" />
        </>
      ) : state === "closed" ? (
        <>
          <rect x={-8} y={-8} width={16} height={16} className="fill-accent" />
          <rect x={-3} y={-3} width={6} height={6} className="fill-surface" />
        </>
      ) : state === "rumored" ? (
        <rect x={-7} y={-7} width={14} height={14} strokeWidth={1.5} strokeDasharray="3 2" className="fill-surface stroke-text-muted" />
      ) : (
        <rect x={-7} y={-7} width={14} height={14} className="fill-accent" />
      )}
    </g>
  );
}

type Pick = { kind: "state"; id: number } | { kind: "version"; id: number };

/**
 * The deal's life on one rail. Squares sit where each state TOOK EFFECT; the tick below the rail sits
 * where it BECAME KNOWN, so a state learned later is visibly later. Terminated (crossed square) and
 * contested (diamond) differ in shape, not just colour. The second track holds the term versions at
 * the date each was known, saying what changed. Everything is also in the detail tables below.
 */
export function DealTimeline({ deal }: { deal: Deal }) {
  const layout = timelineLayout(deal, W);
  const last = deal.transitions.at(-1);
  const [picked, setPicked] = useState<Pick | null>(null);
  const active: Pick | null = picked ?? (last ? { kind: "state", id: last.id } : null);
  if (layout.marks.length === 0) return <p className="text-sm text-text-muted">No state change is known for this deal at this date, so there is no timeline yet.</p>;
  const tr = active?.kind === "state" ? deal.transitions.find((t) => t.id === active.id) : undefined;
  const ver = active?.kind === "version" ? deal.term_versions.find((v) => v.id === active.id) : undefined;
  const anchor = (x: number) => (x > W - 190 ? "end" : x < 150 ? "start" : "middle");
  return (
    <figure className="space-y-3">
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} role="group" aria-label={`Lifecycle of ${deal.title}`} className="block h-auto w-full min-w-[640px]">
          {layout.ticks.map((t) => (
            <g key={t.label}>
              <line x1={t.x} x2={t.x} y1={30} y2={H - 22} strokeWidth={1} className="stroke-border-soft" />
              <text x={t.x} y={H - 8} textAnchor="middle" className={AXIS_TEXT}>{t.label}</text>
            </g>
          ))}
          <text x={4} y={16} className="fill-text-muted text-[11px]">Where the deal stood (square = took effect, tick = became known)</text>
          <line x1={20} x2={W - 20} y1={RAIL} y2={RAIL} strokeWidth={1} className="stroke-border" />
          {layout.marks.map((m, i) => {
            const t = deal.transitions.find((x) => x.id === m.id)!;
            const on = active?.kind === "state" && active.id === m.id;
            const up = i % 2 === 0 ? RAIL - 24 : RAIL - 44;
            return (
              <g key={m.id} tabIndex={0} role="button" aria-pressed={on} aria-label={`${STATE_LABEL[m.state]}, took effect ${formatDay(m.effective)}, known ${formatDay(m.known)}`}
                onFocus={() => setPicked({ kind: "state", id: m.id })} onMouseEnter={() => setPicked({ kind: "state", id: m.id })} onClick={() => setPicked({ kind: "state", id: m.id })} className="cursor-pointer outline-none">
                {m.xKnown !== m.x ? <line x1={m.x} x2={m.xKnown} y1={RAIL + 16} y2={RAIL + 16} strokeWidth={1} strokeDasharray="2 2" className="stroke-text-muted" /> : null}
                <path d={`M ${m.xKnown} ${RAIL + 12} l 5 9 l -10 0 Z`} className="fill-text-muted" />
                <line x1={m.x} x2={m.x} y1={up + 4} y2={RAIL - 12} strokeWidth={1} className="stroke-border" />
                <StateMark state={m.state} x={m.x} active={on} />
                <text x={m.x} y={up} textAnchor={anchor(m.x)} className="fill-text text-[11px] font-medium">{STATE_LABEL[m.state]}</text>
                <text x={m.x} y={RAIL + 38} textAnchor={anchor(m.x)} className={AXIS_TEXT}>
                  {m.known === m.effective || formatDay(m.known) === formatDay(m.effective) ? formatDay(m.effective) : `known ${formatDay(m.known)}`}
                </text>
                {t.rumor_source ? <text x={m.x} y={RAIL + 52} textAnchor={anchor(m.x)} className="fill-text-muted text-[10px]">rumor, not confirmed</text> : null}
              </g>
            );
          })}
          <text x={4} y={TRACK - 40} className="fill-text-muted text-[11px]">What the terms said (placed where each version became known)</text>
          <line x1={20} x2={W - 20} y1={TRACK} y2={TRACK} strokeWidth={1} className="stroke-border" />
          {layout.versions.map((v, i) => {
            const on = active?.kind === "version" && active.id === v.id;
            const proj = v.origin === "management_projection";
            const y = i % 2 === 0 ? TRACK - 12 : TRACK + 24;
            return (
              <g key={v.id} tabIndex={0} role="button" aria-pressed={on} aria-label={`Terms version ${v.versionNo}, ${ORIGIN_LABEL[v.origin]}, known ${formatDay(v.known)}: ${v.summary}`}
                onFocus={() => setPicked({ kind: "version", id: v.id })} onMouseEnter={() => setPicked({ kind: "version", id: v.id })} onClick={() => setPicked({ kind: "version", id: v.id })} className="cursor-pointer outline-none">
                <rect x={v.x - 6} y={TRACK - 10} width={12} height={20} strokeWidth={on ? 2 : 1} fill={proj ? "url(#deal-hatch)" : undefined} className={cn(proj ? "" : "fill-chart-line", on ? "stroke-focus" : "stroke-chart-line")} />
                <text x={v.x} y={y + (i % 2 === 0 ? 0 : 0)} textAnchor={anchor(v.x)} className="fill-text text-[10px]">
                  {`${proj ? "Projection" : "Terms"} v${v.versionNo}: ${v.summary.length > 46 ? `${v.summary.slice(0, 45)}…` : v.summary}`}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <figcaption aria-live="polite" className="border border-border bg-surface-muted px-3 py-2 text-sm">
        {tr ? <StateDetail t={tr} /> : ver ? <VersionDetail v={ver} /> : <span className="text-text-muted">Focus or hover a mark to see its evidence.</span>}
      </figcaption>
    </figure>
  );
}

function StateDetail({ t }: { t: DealTransition }) {
  return (
    <div className="space-y-1">
      <p>
        <strong>{STATE_LABEL[t.state]}</strong>: took effect <span className="num">{formatDay(t.effective_at)}</span>, became known <span className="num">{formatDay(t.known_at)}</span>.
        {t.rumor_source ? <> Source of the rumor: {t.rumor_source}.</> : null}
      </p>
      {t.quote ? <blockquote className="border-l-2 border-border pl-3 text-text-muted">“{t.quote}”</blockquote> : <p className="text-text-muted">No passage is stored for this change.</p>}
      {t.source_url ? <a href={t.source_url} target="_blank" rel="noreferrer noopener" className="underline underline-offset-2">Open the filing</a> : null}
    </div>
  );
}

function VersionDetail({ v }: { v: TermVersion }) {
  const un = v.terms.find((t) => t.name === "unaffected_price");
  return (
    <div className="space-y-1">
      <p>
        <strong>{ORIGIN_LABEL[v.origin]}, version {v.version_no}</strong>, known <span className="num">{formatDay(v.known_at)}</span>.
      </p>
      <ul className="list-disc pl-5">
        {v.changes.length === 0 ? <li>No change in terms.</li> : v.changes.map((c) => <li key={c.field}>{describeChange(c, v.currency)}</li>)}
      </ul>
      {un?.state === "stated" ? (
        <p>Unaffected price {formatTerm("unaffected_price", un.value, v.currency)} dated <span className="num">{v.unaffected_date ? formatDay(v.unaffected_date) : "no date given"}</span>{v.unaffected_source ? ` (${v.unaffected_source})` : ""}.</p>
      ) : <p className="text-text-muted">No unaffected price is stated in this version.</p>}
      {v.quote ? <blockquote className="border-l-2 border-border pl-3 text-text-muted">“{v.quote}”</blockquote> : null}
      {v.source_url ? <a href={v.source_url} target="_blank" rel="noreferrer noopener" className="underline underline-offset-2">Open the filing</a> : null}
    </div>
  );
}

// ------------------------------------------------------------------------------- detail tables
export function TransitionTable({ deal }: { deal: Deal }) {
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <table className={cn(TABLE, "min-w-[720px]")}>
          <caption className="sr-only">State changes with the date each took effect, the date it became known, and its evidence</caption>
          <thead>
            <tr className={HEAD_ROW}>
              <th scope="col" className={TH}>State</th>
              <th scope="col" className={TH}>Took effect</th>
              <th scope="col" className={TH}>Became known</th>
              <th scope="col" className={TH}>Evidence</th>
            </tr>
          </thead>
          <tbody>
            {deal.transitions.map((t) => (
              <tr key={t.id} className={BODY_ROW}>
                <th scope="row" className={cn(TD, "font-medium")}>{STATE_LABEL[t.state]}</th>
                <td className={cn(TD, "num whitespace-nowrap")}>{formatDay(t.effective_at)}</td>
                <td className={cn(TD, "num whitespace-nowrap")}>{formatDay(t.known_at)}</td>
                <td className={TD}>
                  {t.quote ? <span className="text-text-muted">“{t.quote}” </span> : null}
                  {t.rumor_source ? <span className="block text-xs">Rumor source: {t.rumor_source}</span> : null}
                  {t.source_url ? <a href={t.source_url} target="_blank" rel="noreferrer noopener" className="underline underline-offset-2">Filing</a> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {deal.rejected_transitions.length > 0 ? (
        <ul role="note" className="space-y-0.5 border border-dashed border-border bg-surface-muted px-3 py-2 text-sm">
          {deal.rejected_transitions.map((r) => (
            <li key={r.id}>A recorded change to “{STATE_LABEL[r.state] ?? r.state}” was not applied: {r.reason}.</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function VersionTable({ deal }: { deal: Deal }) {
  if (deal.term_versions.length === 0) return <p className="text-sm text-text-muted">No terms are stated for this deal at this date.</p>;
  return (
    <div className="overflow-x-auto">
      <table className={cn(TABLE, "min-w-[760px]")}>
        <caption className="sr-only">Term versions: what each said, what changed, and the unaffected price with its date</caption>
        <thead>
          <tr className={HEAD_ROW}>
            <th scope="col" className={TH}>Version</th>
            <th scope="col" className={TH}>Kind</th>
            <th scope="col" className={TH}>Known</th>
            <th scope="col" className={TH}>What changed</th>
            <th scope="col" className={TH}>Unaffected price</th>
          </tr>
        </thead>
        <tbody>
          {deal.term_versions.map((v) => {
            const un = v.terms.find((t) => t.name === "unaffected_price");
            return (
              <tr key={v.id} className={BODY_ROW}>
                <th scope="row" className={cn(TD, "num font-medium")}>v{v.version_no}</th>
                <td className={TD}><span className="inline-flex items-center gap-1.5"><Swatch kind={originKind(v.origin)} />{ORIGIN_LABEL[v.origin]}</span></td>
                <td className={cn(TD, "num whitespace-nowrap")}>{formatDay(v.known_at)}</td>
                <td className={TD}>{v.changes.length === 0 ? <span className="text-text-muted">First version</span> : <ul className="space-y-0.5">{v.changes.map((c) => <li key={c.field}>{describeChange(c, v.currency)}</li>)}</ul>}{v.problems.map((p) => <p key={p} className="text-xs text-warning-signal">{p}</p>)}</td>
                <td className={cn(TD, "num")}>
                  {un?.state === "stated" ? <>{formatTerm("unaffected_price", un.value, v.currency)}, {v.unaffected_date ? formatDay(v.unaffected_date) : <span className="text-warning-signal">no date</span>}</> : <span className="text-text-muted">not stated</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// -------------------------------------------------------------------------------------- bridge
const BRIDGE_FILL: Record<string, string> = { cash: CATEGORICAL_FILL[0], stock: CATEGORICAL_FILL[2], debt: CATEGORICAL_FILL[4], earnout: CATEGORICAL_FILL[6] };

/** Cash, stock, debt and earnout as stated. A part not stated is an open outlined segment labelled
 * "not stated", with its own width: it is not a zero and it is not a share of the total. */
export function ConsiderationBridge({ deal }: { deal: Deal }) {
  const b = deal.bridge;
  const parts = bridgeParts(b, 720);
  const synergy = deal.impacts.find((i) => i.kind === "synergies" && i.value != null);
  const statedAny = parts.some((p) => p.state === "stated");
  const total = num(b.known_total) ?? 0;
  return (
    <figure className="space-y-3">
      <div className="overflow-x-auto">
        <svg viewBox="0 0 740 118" role="img" aria-label={`Consideration for ${deal.title}: ${statedAny ? `at least ${money(total, b.currency)} stated` : "no amount stated"}`} className="block h-auto w-full min-w-[520px] max-w-3xl">
          {parts.map((p) => (
            <g key={p.kind} transform={`translate(${p.x + 10} 8)`}>
              {p.state === "stated" ? (
                <rect width={Math.max(2, p.w - 2)} height={30} className={BRIDGE_FILL[p.kind]} />
              ) : (
                <rect width={Math.max(2, p.w - 2)} height={30} strokeWidth={1} className="fill-surface stroke-text-muted" />
              )}
              <text x={2} y={50} className="fill-text text-[11px] font-medium">{p.label}</text>
              <text x={2} y={64} className={p.state === "stated" ? "fill-text num text-[11px]" : "fill-text-muted text-[11px]"}>{p.state === "stated" ? money(p.amount, b.currency) : "not stated"}</text>
            </g>
          ))}
          {synergy ? (
            <g transform="translate(10 84)">
              <rect width={Math.max(24, Math.min(720, total > 0 ? (Number(synergy.value) / total) * 720 : 120))} height={14} strokeWidth={1} fill="url(#deal-hatch)" className="stroke-chart-line" />
              <text x={0} y={30} className="fill-text-muted text-[11px]">Management projection, not part of the price: expected synergies {impactValue(synergy)}</text>
            </g>
          ) : null}
        </svg>
      </div>
      <p className="text-sm">
        {statedAny ? <>Stated so far: <span className="num font-medium">{money(total, b.currency)}</span>{b.total_is_lower_bound ? ` or more, because ${b.unstated.join(" and ")} ${b.unstated.length === 1 ? "is" : "are"} not stated` : ""}.</> : "No consideration is stated in the documents known at this date."}
        {b.cash_pct != null || b.stock_pct != null ? <> Reported mix: {b.cash_pct != null ? `${percent(b.cash_pct, 0)} cash` : ""}{b.cash_pct != null && b.stock_pct != null ? ", " : ""}{b.stock_pct != null ? `${percent(b.stock_pct, 0)} stock` : ""}.</> : null}
      </p>
      <table className={cn(TABLE, "max-w-md")}>
        <caption className="sr-only">Consideration by part, amounts in {b.currency ?? "an unstated currency"}</caption>
        <thead><tr className={HEAD_ROW}><th scope="col" className={TH}>Part</th><th scope="col" className={TH}>Status</th><th scope="col" className={cn(TH, "text-right")}>Amount</th></tr></thead>
        <tbody>
          {b.segments.map((s) => (
            <tr key={s.kind} className={BODY_ROW}>
              <th scope="row" className={cn(TD, "font-medium")}>{s.kind === "earnout" ? "Earnout" : s.kind[0].toUpperCase() + s.kind.slice(1)}</th>
              <td className={TD}>{s.state === "stated" ? "Stated" : <span className="text-text-muted">Not stated</span>}</td>
              <td className={cn(TD, "num text-right")}>{s.state === "stated" ? money(s.amount, b.currency) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}

// ---------------------------------------------------------------------------- impacts and waterfalls
export function ImpactWaterfalls({ deal }: { deal: Deal }) {
  const { shown, notDrawn } = impactWaterfalls(deal);
  return (
    <div className="space-y-4">
      {shown.length === 0 ? <p className="text-sm text-text-muted">No impact can be drawn: each needs inputs the documents do not give (see below).</p> : null}
      <div className="flex flex-col gap-6">
        {shown.map((w) => (
          <figure key={w.id} className="max-w-xl space-y-1">
            <figcaption className="flex items-center gap-2 text-sm font-medium"><Swatch kind={originKind(w.origin)} />{w.title} <span className="font-normal text-text-muted">· {ORIGIN_LABEL[w.origin]}</span></figcaption>
            <WaterfallChart steps={w.steps} label={`${w.title}, ${ORIGIN_LABEL[w.origin]}`} formatValue={w.format} />
            <p className="text-xs text-text-muted">{w.note}</p>
          </figure>
        ))}
      </div>
      {notDrawn.length > 0 ? (
        <ul className="space-y-0.5 text-sm text-text-muted">
          {notDrawn.map((n) => (
            <li key={n.title} className="flex items-start gap-1.5"><Swatch kind="unknown" /><span><span className="font-medium text-text">{n.title}</span> is not drawn: {n.reason}.</span></li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function ImpactTable({ impacts }: { impacts: readonly Impact[] }) {
  if (impacts.length === 0) return <p className="text-sm text-text-muted">No impact is defined for this type of deal.</p>;
  return (
    <div className="overflow-x-auto">
      <table className={cn(TABLE, "min-w-[760px]")}>
        <caption className="sr-only">Impacts with their value, kind, inputs and formula, or the reason there is no value</caption>
        <thead>
          <tr className={HEAD_ROW}>
            <th scope="col" className={TH}>Impact</th>
            <th scope="col" className={cn(TH, "text-right")}>Value</th>
            <th scope="col" className={TH}>Kind</th>
            <th scope="col" className={TH}>Inputs and formula</th>
          </tr>
        </thead>
        <tbody>
          {impacts.map((i, n) => (
            <tr key={`${i.kind}-${i.origin}-${n}`} className={BODY_ROW}>
              <th scope="row" className={cn(TD, "font-medium")}>{i.label}</th>
              <td className={cn(TD, "num text-right whitespace-nowrap")}>{i.value == null ? <span className="text-text-muted">Not computable</span> : impactValue(i)}</td>
              <td className={TD}><span className="inline-flex items-center gap-1.5"><Swatch kind={i.value == null ? "unknown" : originKind(i.origin)} />{ORIGIN_LABEL[i.origin]}</span></td>
              <td className={TD}>
                {i.reason ? <p>{i.reason[0].toUpperCase() + i.reason.slice(1)}.</p> : null}
                {i.flags.map((f) => <p key={f} className="text-warning-signal">{FLAG_LABEL[f] ?? f}</p>)}
                <p className="text-xs text-text-muted">
                  {Object.entries(i.inputs).map(([k, v]) => `${k.replaceAll("_", " ")} ${v}`).join("; ") || "no inputs available"}
                </p>
                <p className="text-xs text-text-muted">
                  {i.formula} · formula {i.formula_version} · from term version {i.term_version_ids.length ? i.term_version_ids.join(", ") : "none"}
                </p>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// -------------------------------------------------------------------- participants, missing, docs
export function DealFacts({ deal }: { deal: Deal }) {
  const c = deal.closing;
  return (
    <div className="space-y-4 text-sm">
      <p>
        <strong>{deal.type_label}.</strong>{" "}
        {deal.participants.map((p) => `${p.name} (${p.role}${p.ticker ? `, ${p.ticker}` : ""})`).join("; ")}. Support: {CONFIDENCE_LABEL[deal.confidence]}.
      </p>
      <div>
        <h4 className="font-medium">Missing fields</h4>
        {deal.missing_fields.length === 0 ? (
          <p className="text-text-muted">Every field this type of deal needs is stated.</p>
        ) : (
          <ul className="list-disc pl-5">
            {deal.missing_fields.map((m) => <li key={m.field}>{m.label} is not stated. It will appear when a filing gives it; it is never filled from a headline.</li>)}
          </ul>
        )}
      </div>
      {c ? (
        <div>
          <h4 className="font-medium">Closing and successor</h4>
          <dl className="grid max-w-xl grid-cols-[auto_1fr] gap-x-4 gap-y-0.5">
            <dt className="text-text-muted">Legal closing</dt><dd className="num">{c.legal_closing_date ? formatDay(c.legal_closing_date) : "not recorded"}</dd>
            <dt className="text-text-muted">First reporting period that includes it</dt><dd className="num">{c.first_reporting_period_end ? `period ending ${formatDay(c.first_reporting_period_end)}` : "not recorded yet"}</dd>
            <dt className="text-text-muted">Broker posted the corporate action</dt><dd className="num">{c.broker_posting_date ? formatDay(c.broker_posting_date) : "not recorded"}</dd>
            <dt className="text-text-muted">Successor</dt><dd>{c.successor_name ?? "not recorded"}</dd>
          </dl>
          <p className="mt-1 text-xs text-text-muted">These are three different facts; none is worked out from another.</p>
          {c.problems.map((p) => <p key={p} className="text-warning-signal">{p[0].toUpperCase() + p.slice(1)}.</p>)}
        </div>
      ) : null}
      <div>
        <h4 className="font-medium">Documents</h4>
        {deal.documents.length === 0 ? <p className="text-text-muted">No document is linked at this date.</p> : (
          <ul className="list-disc pl-5">
            {deal.documents.map((d) => (
              <li key={d.document_version_id}>
                {d.role[0].toUpperCase() + d.role.slice(1)}, known <span className="num">{formatDay(d.known_at)}</span>{" "}
                {d.source_url ? <a href={d.source_url} target="_blank" rel="noreferrer noopener" className="underline underline-offset-2">open</a> : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------- portfolio
export function PortfolioDealsPanel({ data, names }: { data: PortfolioDeals; names?: Record<string, string> }) {
  const label = (t: string) => names?.[t] ?? t;
  // unknown stays unknown: a missing figure is not read as zero
  const covered = num(data.covered_weight);
  const denom = num(data.denominator);
  return (
    <div className="space-y-3 text-sm">
      <p>
        Weights are shares of <strong className="num">{percent(data.denominator, 0)}</strong> of portfolio value across <span className="num">{data.holdings}</span> holdings. <span className="num">{percent(data.covered_weight)}</span> sits in {denom != null && denom > 0 && covered === 0 ? "no holding" : "holdings"} with an active deal known at this date.
      </p>
      {data.rows.length === 0 ? <p className="text-text-muted">No held company has an active deal known at this date.</p> : (
        <div className="overflow-x-auto">
          <table className={cn(TABLE, "min-w-[560px]")}>
            <caption className="sr-only">Holdings with an active deal, with portfolio weight</caption>
            <thead><tr className={HEAD_ROW}><th scope="col" className={TH}>Holding</th><th scope="col" className={TH}>Deal</th><th scope="col" className={TH}>State</th><th scope="col" className={cn(TH, "text-right")}>Weight</th></tr></thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={`${r.ticker}-${r.deal_id}`} className={BODY_ROW}>
                  <th scope="row" className={cn(TD, "font-medium")}>{label(r.ticker)}</th>
                  <td className={TD}>{r.title}</td>
                  <td className={TD}>{r.state ? STATE_LABEL[r.state] : "Not resolved"}</td>
                  <td className={cn(TD, "num text-right")}>{percent(r.weight)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {Object.keys(data.by_state).length > 0 ? (
        <p className="text-text-muted">By state: {Object.entries(data.by_state).map(([k, v]) => `${STATE_LABEL[k] ?? k} ${percent(v)}`).join("; ")}.</p>
      ) : null}
      <p className="flex items-start gap-1.5 text-text-muted"><Swatch kind="unknown" /><span>No deal known for: {data.none_known.length ? data.none_known.map(label).join(", ") : "none"}. That is unknown, not zero: it does not prove nothing is pending.</span></p>
      {data.unknown_state.length > 0 ? <p className="text-warning-signal">State not resolved for: {data.unknown_state.map(label).join(", ")}.</p> : null}
      {data.notes.map((n) => <p key={n} className="text-text-muted">{n}</p>)}
    </div>
  );
}
