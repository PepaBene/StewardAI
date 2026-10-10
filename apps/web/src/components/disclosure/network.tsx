"use client";

import Link from "next/link";
import { useState } from "react";

import { Sankey } from "@/components/charts";
import { Field, SaveNote, SELECT_CLASS, TEXTAREA_CLASS, type SaveState } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { callApi, DecisionForm } from "@/app/(app)/relationships/decision-form";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDay } from "@/lib/disclosure/model";
import {
  concentrationParts,
  DENOMINATOR_LABEL,
  formatShare,
  fraction,
  graphLayout,
  liveSankey,
  periodLabel,
  portfolioSankey,
  SHAPE_LABEL,
  STATE_LABEL,
  type Claim,
  type Concentration,
  type OwnerWeight,
  type PortfolioDependencies,
  type PortfolioHolding,
  type Scenario,
  type SideConflict,
} from "@/lib/disclosure/dependencies";
import { DECISION_LABEL, DECISION_RESULT, decisionBody, decisionsFor, toStatus, type GraphDecision } from "@/lib/relationships/relationships";
import { safeHref } from "@/lib/reference/identity";
import { cn } from "@/lib/utils";

const SCREEN_LABEL: Record<string, string> = {
  negated: "Reads as a denial, not a relationship",
  hypothetical: "Reads as a risk statement, not a fact",
  historical: "May describe a past relationship",
  third_party: "May describe other companies",
};

// --------------------------------------------------------------------------------- the graph
const W = 760;
const BOX_W = 190;
const BOX_H = 40;
const ROW = 64;

/**
 * The company in the middle, suppliers to its left and customers to its right. An arrow follows the
 * money: supplier to company is a purchase, company to customer is a sale. Line weight is the
 * reported share; a dashed line is a claim nobody has confirmed; a dotted line reports no share.
 */
export function DependencyGraph({ ticker, claims }: { ticker: string; claims: readonly Claim[] }) {
  const { nodes, edges } = graphLayout(ticker, claims);
  if (edges.length === 0) return <p className="text-sm text-text-muted">No customer or supplier claims are known for this company at this date.</p>;
  const supplierSide = nodes.filter((n) => n.kind === "supplier").length;
  const customerSide = nodes.filter((n) => n.kind === "customer" || n.kind === "award").length;
  const H = Math.max(supplierSide, customerSide, 2) * ROW + 24;
  const pos = (id: string) => {
    const n = nodes.find((x) => x.id === id)!;
    return { x: n.x * W, y: 12 + n.y * (H - 24) };
  };
  return (
    <figure className="space-y-2">
      <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full max-w-4xl" role="img" aria-label={`Customers and suppliers of ${ticker}, with the share each reports`}>
        <defs>
          <marker id="dep-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" className="fill-chart-line" />
          </marker>
        </defs>
        {edges.map((e) => {
          const a = pos(e.from);
          const b = pos(e.to);
          const x1 = a.x + (a.x < b.x ? BOX_W / 2 : -BOX_W / 2);
          const x2 = b.x + (a.x < b.x ? -BOX_W / 2 : BOX_W / 2);
          const mid = (x1 + x2) / 2;
          return (
            <g key={e.id} opacity={e.status === "confirmed" ? 1 : 0.7}>
              <title>{`${e.label} — ${STATE_LABEL[e.status] ?? e.status}`}</title>
              <path
                d={`M ${x1} ${a.y} C ${mid} ${a.y}, ${mid} ${b.y}, ${x2} ${b.y}`}
                fill="none"
                strokeWidth={e.share == null ? 1.5 : 2 + e.share * 16}
                strokeDasharray={e.status !== "confirmed" ? "7 5" : e.share == null ? "1 5" : undefined}
                strokeLinecap={e.share == null && e.status === "confirmed" ? "round" : "butt"}
                markerEnd="url(#dep-arrow)"
                className="stroke-chart-line"
              />
            </g>
          );
        })}
        {nodes.map((n) => {
          const p = pos(n.id);
          const edge = edges.find((e) => (e.from === n.id || e.to === n.id) && n.kind !== "issuer");
          return (
            <g key={n.id}>
              <rect x={p.x - BOX_W / 2} y={p.y - BOX_H / 2} width={BOX_W} height={BOX_H} strokeWidth={n.kind === "issuer" ? 2.5 : 1.5} className={cn(n.kind === "issuer" ? "fill-accent stroke-accent" : "fill-surface-raised stroke-border")} />
              <text x={p.x} y={edge ? p.y - 3 : p.y} textAnchor="middle" dominantBaseline="middle" className={cn("text-[12px] font-medium", n.kind === "issuer" ? "fill-accent-contrast" : "fill-text")}>
                {n.label.length > 28 ? `${n.label.slice(0, 27)}…` : n.label}
              </text>
              {edge ? (
                <text x={p.x} y={p.y + 12} textAnchor="middle" className="fill-text-muted text-[10.5px]">
                  {edge.status !== "confirmed" ? `${STATE_LABEL[edge.status]}: ` : ""}
                  {edge.label.length > 30 ? `${edge.label.slice(0, 29)}…` : edge.label}
                </text>
              ) : null}
            </g>
          );
        })}
        <text x={W * 0.14} y={H - 2} textAnchor="middle" className="fill-text-muted text-[11px]">Sells to the company</text>
        <text x={W * 0.86} y={H - 2} textAnchor="middle" className="fill-text-muted text-[11px]">Buys from the company</text>
      </svg>
      <figcaption className="max-w-[72ch] text-xs text-text-muted">
        Heavier line, larger reported share. Dashed: suggested or disputed, not confirmed. Dotted: confirmed, but no share is reported. Rejected claims are not drawn.
      </figcaption>
      <ClaimsTable claims={claims.filter((c) => c.status !== "rejected")} />
    </figure>
  );
}

const ENTITY_LABEL: Record<string, string> = {
  matched: "Linked to a company by identifier",
  candidate: "Possible company match, not confirmed",
  ambiguous: "More than one company fits",
  none: "No company matched",
};

/** Only an identifier match is a link. A name match is a lead, and says so. */
function EntityNote({ claim }: { claim: Claim }) {
  const e = claim.entity;
  if (!e || e.status === "anonymous") return null;
  return (
    <span className="mt-0.5 block max-w-[40ch] text-xs font-normal text-text-muted">
      {ENTITY_LABEL[e.status] ?? e.status}
      {e.needs_review ? ". Needs a person to check." : "."} {e.note}
    </span>
  );
}

function AwardNotes({ claim }: { claim: Claim }) {
  if (claim.awards.length === 0) return null;
  return (
    <ul className="mt-1 space-y-1 text-xs font-normal text-text-muted">
      {claim.awards.map((a) => (
        <li key={`${a.notice_number}-${a.lot_id ?? ""}`}>
          <span className="num">{a.notice_number}</span>
          {a.lot_id ? <>, lot {a.lot_id}</> : null}: {a.status}
          {a.value ? <>, <span className="num">{a.currency ?? "unknown currency"} {Number(a.value).toLocaleString("en-GB")}</span></> : ", no value published"}
          {a.cpv_codes.length ? <>, CPV <span className="num">{a.cpv_codes.join(", ")}</span></> : null}
          {a.start_date ? <>, from {formatDay(a.start_date)}</> : null}
        </li>
      ))}
    </ul>
  );
}

export function ClaimsTable({ claims }: { claims: readonly Claim[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] border-collapse text-sm">
        <caption className="sr-only">Every relationship claim known at this date, with its review state, share, basis and source</caption>
        <thead>
          <tr className="border-b border-border text-left">
            <th scope="col" className="py-1.5 pr-3 font-medium">Counterparty</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">Relationship</th>
            <th scope="col" className="py-1.5 pr-3 text-right font-medium">Reported share</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">Period</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">Review state</th>
            <th scope="col" className="py-1.5 font-medium">Known from</th>
          </tr>
        </thead>
        <tbody>
          {claims.map((c) => (
            <tr key={c.id} className="border-b border-border-soft align-top">
              <th scope="row" className="py-1.5 pr-3 text-left font-medium">
                {c.counterparty ?? SHAPE_LABEL[c.shape]}
                {c.counterparty_count ? <span className="block text-xs font-normal text-text-muted">{c.counterparty_count} customers together</span> : null}
                <EntityNote claim={c} />
                <AwardNotes claim={c} />
              </th>
              <td className="py-1.5 pr-3">{c.relationship === "procurement_award" ? "Contract award" : c.relationship === "customer" ? "Customer" : "Supplier"}</td>
              <td className="num py-1.5 pr-3 text-right">
                {c.relationship === "procurement_award" ? "Not a revenue share" : formatShare(c.share)}
                {c.denominator && c.share ? <span className="block text-xs text-text-muted">{DENOMINATOR_LABEL[c.denominator]}</span> : null}
              </td>
              <td className="num py-1.5 pr-3 whitespace-nowrap">{c.period_end ? formatDay(c.period_end) : "—"}</td>
              <td className="py-1.5 pr-3">
                {STATE_LABEL[c.status] ?? c.status}
                {c.stale ? <span className="block text-xs text-text-muted">Source is over 18 months old</span> : null}
              </td>
              <td className="num py-1.5 whitespace-nowrap">{formatDay(c.known_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ----------------------------------------------------------------------------- concentration
const HATCH = "repeating-linear-gradient(135deg, var(--chart-grid) 0 3px, transparent 3px 7px)";

/**
 * Ranked bars, one per confirmed counterparty, each against the same whole. The last row is the part
 * no confirmed claim accounts for, drawn as an open hatched gap because an unknown is not a customer.
 * Each basis and period is its own group and none is added to another.
 */
export function ConcentrationBars({ groups }: { groups: readonly Concentration[] }) {
  const shown = groups.filter((g) => g.entries.length > 0 || g.groups.length > 0 || g.suggested_not_counted > 0);
  if (shown.length === 0) return <p className="text-sm text-text-muted">No customer shares are reported for this company at this date.</p>;
  return (
    <div className="space-y-8">
      {shown.map((g) => {
        const parts = concentrationParts(g);
        return (
          <section key={`${g.denominator}-${g.period}`} aria-label={`${periodLabel(g.period)}, shares ${DENOMINATOR_LABEL[g.denominator] ?? g.denominator}`} className="space-y-2">
            <h3 className="text-base font-semibold">
              {periodLabel(g.period)} <span className="font-normal text-text-muted">· shares {DENOMINATOR_LABEL[g.denominator] ?? g.denominator}</span>
            </h3>
            {g.entries.length === 0 ? <p className="text-sm text-text-muted">Nothing is confirmed for this basis yet.</p> : null}
            <ol className="space-y-1.5">
              {parts.map((p) => (
                <li key={p.key} className="grid grid-cols-[minmax(8rem,14rem)_1fr_3.5rem] items-center gap-3 text-sm">
                  <span className={cn("truncate", p.kind === "remainder" && "text-text-muted")}>{p.label}</span>
                  <span className="relative block h-4 border border-border bg-surface-raised" role="img" aria-label={`${p.label}: ${formatShare(p.share)}`}>
                    <span className="absolute inset-y-0 left-0" style={p.kind === "remainder" ? { width: `${p.share * 100}%`, backgroundImage: HATCH } : { width: `${Math.min(100, p.share * 100)}%`, background: "var(--chart-line)" }} />
                  </span>
                  <span className="num text-right font-medium">{formatShare(p.share)}</span>
                </li>
              ))}
            </ol>
            {g.groups.map((gr) => (
              <p key={gr.id} className="text-sm">
                <strong>{gr.label}</strong>: {gr.count} customers together reported at <span className="num">{formatShare(gr.share)}</span>. This is a floor, and is not added to the bars above.
              </p>
            ))}
            <ul className="space-y-0.5 text-xs text-text-muted">
              {g.suggested_not_counted > 0 ? <li>{g.suggested_not_counted} suggested {g.suggested_not_counted === 1 ? "claim is" : "claims are"} waiting for review and not counted.</li> : null}
              {g.exceeds_whole ? <li>The single-party shares add to more than the whole, so the claims disagree and need review.</li> : null}
              {g.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

// ----------------------------------------------------------------------------------- scenario
export const SHOCKS = [0.1, 0.2, 0.3, 0.5] as const;

export function ScenarioView({ scenarios, shock, onShock, names }: { scenarios: readonly Scenario[]; shock: number; onShock: (s: number) => void; names: Record<string, string> }) {
  const name = (k: string) => names[k] ?? k;
  return (
    <div className="space-y-4">
      <div role="group" aria-label="Size of the revenue shock" className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">If a customer’s revenue falls by</span>
        {SHOCKS.map((s) => (
          <button
            key={s}
            type="button"
            aria-pressed={s === shock}
            onClick={() => onShock(s)}
            className={cn("num h-8 border px-3 focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none", s === shock ? "border-accent bg-accent text-accent-contrast" : "border-border bg-surface-raised hover:bg-surface-muted")}
          >
            {Math.round(s * 100)}%
          </button>
        ))}
      </div>
      {scenarios.length === 0 ? <p className="text-sm text-text-muted">No confirmed customer with a reported share links to another company, so there is nothing to project.</p> : null}
      {scenarios.map((s) => (
        <section key={s.origin} aria-label={`If ${name(s.origin)} loses ${formatShare(s.shock)} of its revenue`} className="space-y-2 border-t border-border-soft pt-3">
          <h3 className="text-base font-semibold">If {name(s.origin)} loses {formatShare(s.shock)} of its revenue</h3>
          {s.impacts.length === 0 ? <p className="text-sm text-text-muted">No confirmed supplier link with a reported share.</p> : null}
          <ul className="space-y-2">
            {s.impacts.map((i) => {
              // an unknown bound is unknown, never 0%: no bar is drawn and the text says so
              const lo = fraction(i.lower);
              const up = fraction(i.upper);
              const known = lo != null && up != null;
              return (
                <li key={`${i.entity}-${i.hop}`} className="grid grid-cols-[minmax(8rem,14rem)_1fr_9rem] items-center gap-3 text-sm">
                  <span>
                    {name(i.entity)}
                    <span className="block text-xs text-text-muted">{i.hop === 1 ? "Directly" : "Second step"}, {formatShare(i.coverage)} of its revenue is covered by confirmed claims</span>
                  </span>
                  <span className="relative block h-4 border border-border bg-surface-raised" role="img" aria-label={known ? `Revenue falls between ${formatShare(lo, 1)} and ${formatShare(up, 1)}` : "The bounds on this revenue effect are unknown"}>
                    {known ? <span className="absolute inset-y-0 left-0" style={{ width: `${Math.min(100, up * 100)}%`, backgroundImage: HATCH }} /> : null}
                    {known ? <span className="absolute inset-y-0 left-0" style={{ width: `${Math.min(100, lo * 100)}%`, background: "var(--chart-line)" }} /> : null}
                  </span>
                  <span className="num text-right">{known ? `${formatShare(lo, 1)} to ${formatShare(up, 1)}` : "bounds unknown"}</span>
                </li>
              );
            })}
          </ul>
          {s.non_quantified.length ? (
            <div className="text-sm">
              <p className="font-medium">Linked, but not quantified</p>
              <ul className="list-disc space-y-0.5 pl-5 text-text-muted">
                {s.non_quantified.map((n) => (
                  <li key={n.edge_id}>{name(n.supplier)} and {name(n.customer)}: confirmed relationship, no reported share.</li>
                ))}
              </ul>
            </div>
          ) : null}
          <ul className="space-y-0.5 text-xs text-text-muted">
            {s.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
            <li>Solid is the effect through disclosed shares. The hatched part is what the upper bound adds if every undisclosed customer fell as far. This is arithmetic on reported shares, not a forecast.</li>
          </ul>
        </section>
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------------- suggestions
export function SuggestedEdges({ ticker, claims, editable = false, onChanged }: { ticker: string; claims: readonly Claim[]; editable?: boolean; onChanged?: () => void }) {
  const open = claims.filter((c) => c.status === "suggested" || c.status === "disputed");
  if (open.length === 0) return <p className="text-sm text-text-muted">Nothing is waiting for review at this date.</p>;
  return (
    <ul className="space-y-4">
      {open.map((c) => (
        <li key={c.id} className="space-y-1.5 border border-border bg-surface-raised p-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="font-medium">
              {c.counterparty ?? SHAPE_LABEL[c.shape]} <span className="font-normal text-text-muted">· {c.relationship === "supplier" ? "supplier" : "customer"}{c.share ? `, ${formatShare(c.share)} ${DENOMINATOR_LABEL[c.denominator ?? "other"]}` : ""}</span>
            </p>
            <span className="border border-dashed border-border px-2 py-0.5 text-xs">{STATE_LABEL[c.status]}</span>
          </div>
          <blockquote className="max-w-[72ch] border-l-2 border-border pl-3 text-sm">“{c.passage}”</blockquote>
          {c.screen.length ? (
            <ul className="space-y-0.5 text-xs text-text-muted">
              {c.screen.map((s) => (
                <li key={s}>Check before confirming: {SCREEN_LABEL[s] ?? s}.</li>
              ))}
            </ul>
          ) : null}
          <p className="text-xs text-text-muted">
            Known from <span className="num">{formatDay(c.known_at)}</span>.{" "}
            {c.source_url ? (
              <a href={safeHref(c.source_url) ?? undefined} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">Open the source</a>
            ) : null}
          </p>
          <EntityNote claim={c} />
          {editable ? (
            <DecisionForm
              summary="Decide on this claim"
              options={decisionsFor(toStatus(c.status)).map((v) => ({ value: v, label: DECISION_LABEL[v] }))}
              onSubmit={async (value, reason) => {
                const decision = value as GraphDecision;
                const r = await callApi(
                  () =>
                    getBrowserApiClient().POST("/holdings/{ticker}/relationship-disclosures/{disclosure_id}/decision", {
                      params: { path: { ticker, disclosure_id: c.id } },
                      body: decisionBody(decision, reason),
                    }),
                  `Saved: the claim is now ${DECISION_RESULT[decision]}.`,
                );
                if (r.ok) onChanged?.();
                return r;
              }}
            />
          ) : null}
        </li>
      ))}
      <li className="text-sm">
        A suggestion counts for nothing until a person confirms it. <Link href="/relationships" className="text-accent-ink hover:underline">See every suggestion in Relationships</Link>.
      </li>
    </ul>
  );
}

// ------------------------------------------------------------------------ conflicts, owner weights
export function ConflictList({ conflicts, claims, inbound }: { conflicts: readonly SideConflict[]; claims: readonly Claim[]; inbound: number }) {
  const name = (id: number) => claims.find((c) => c.id === id)?.counterparty ?? `claim ${id}`;
  return (
    <div className="space-y-2 text-sm">
      {conflicts.length === 0 ? (
        <p className="text-text-muted">No two reporters disagree about this company at this date.</p>
      ) : (
        <ul className="space-y-2">
          {conflicts.map((c) => (
            <li key={`${c.kind}-${c.claim_ids.join("-")}`} className="max-w-[72ch] border border-border bg-surface-raised p-3">
              <p className="font-medium">{c.kind === "opposite_direction" ? "The two sides describe opposite directions" : "The two sides report different shares"}</p>
              <p className="mt-0.5">{c.note}</p>
              <p className="mt-1 text-xs text-text-muted">Involves {c.claim_ids.map(name).join(", ")}. Both claims are kept; neither overwrites the other.</p>
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-text-muted">{inbound === 0 ? "No other company has filed a claim about this one." : `${inbound} ${inbound === 1 ? "claim was" : "claims were"} made about this company by other reporters; they feed the scenario but not the concentration bars.`}</p>
    </div>
  );
}

const OWNER_DENOMINATORS = [
  { value: "total_revenue", label: "Share of total revenue" },
  { value: "segment_revenue", label: "Share of segment revenue" },
  { value: "purchases", label: "Share of purchases" },
  { value: "cost_of_sales", label: "Share of cost of sales" },
  { value: "receivables", label: "Share of receivables" },
  { value: "other", label: "Other basis" },
] as const;

/** A weight you assigned yourself. It is labelled as yours everywhere and never joins reported figures. */
export function OwnerWeights({ ticker, weights, editable, onChanged }: { ticker: string; weights: readonly OwnerWeight[]; editable: boolean; onChanged: () => void }) {
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [percent, setPercent] = useState("");
  const [basis, setBasis] = useState<(typeof OWNER_DENOMINATORS)[number]["value"]>("total_revenue");
  const [note, setNote] = useState("");
  const [state, setState] = useState<SaveState>({ kind: "idle" });
  const share = Number((Number(percent) / 100).toPrecision(12)); // 14.1 % is 0.141, not 0.14100000000000001
  const today = new Date();
  const localDay = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  const problem = !key.trim() ? "Enter the counterparty’s ticker or id." : !Number.isFinite(share) || percent.trim() === "" || share < 0 || share > 1 ? "Enter a percentage from 0 to 100." : note.trim().length < 10 ? "Say where this estimate comes from (at least 10 characters)." : undefined;
  const [touched, setTouched] = useState(false);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (problem) return;
    setState({ kind: "saving" });
    const r = await callApi(
      () =>
        getBrowserApiClient().POST("/disclosures/holdings/{ticker}/dependency-weights", {
          params: { path: { ticker } },
          body: { counterparty_key: key.trim().toUpperCase(), counterparty_name: name.trim() || null, share: String(share), denominator: basis, as_of: localDay, note: note.trim() },
        }),
      "Saved as your own estimate.",
    );
    if (r.ok) {
      setState({ kind: "ok", text: r.text });
      setKey("");
      setName("");
      setPercent("");
      setNote("");
      setTouched(false);
      onChanged();
    } else setState({ kind: "error", text: r.text });
  }

  async function retire(id: number) {
    const r = await callApi(() => getBrowserApiClient().DELETE("/disclosures/holdings/{ticker}/dependency-weights/{weight_id}", { params: { path: { ticker, weight_id: id } } }), "Retired. Earlier dates still show it.");
    setState(r.ok ? { kind: "ok", text: r.text } : { kind: "error", text: r.text });
    if (r.ok) onChanged();
  }

  return (
    <div className="space-y-3 text-sm">
      {weights.length === 0 ? (
        <p className="text-text-muted">You have not assigned any weights for this holding.</p>
      ) : (
        <table className="w-full max-w-3xl border-collapse">
          <caption className="sr-only">Weights assigned by the owner, not reported by any company</caption>
          <thead>
            <tr className="border-b border-border text-left">
              <th scope="col" className="py-1.5 pr-3 font-medium">Counterparty</th>
              <th scope="col" className="py-1.5 pr-3 text-right font-medium">Your estimate</th>
              <th scope="col" className="py-1.5 pr-3 font-medium">Why</th>
              <th scope="col" className="py-1.5 font-medium"><span className="sr-only">Action</span></th>
            </tr>
          </thead>
          <tbody>
            {weights.map((w) => (
              <tr key={w.id} className="border-b border-border-soft align-top">
                <th scope="row" className="py-1.5 pr-3 text-left font-medium">{w.counterparty ?? w.counterparty_key}<span className="block text-xs font-normal text-text-muted">as of {formatDay(w.as_of)}</span></th>
                <td className="num py-1.5 pr-3 text-right">{formatShare(w.share)}<span className="block text-xs text-text-muted">{DENOMINATOR_LABEL[w.denominator] ?? w.denominator}</span></td>
                <td className="py-1.5 pr-3 text-text-muted">{w.note}</td>
                <td className="py-1.5 text-right">{editable ? <Button type="button" size="sm" variant="outline" onClick={() => void retire(w.id)}>Retire</Button> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="max-w-[72ch] text-xs text-text-muted">These are your estimates, not company filings. They can widen a scenario and are labelled that way there; they are never added to the reported concentration.</p>
      {editable ? (
        <form onSubmit={add} noValidate className="grid max-w-3xl gap-3 border border-border bg-surface-raised p-3 sm:grid-cols-2">
          <Field label="Counterparty id" hint="Its ticker, as the scenario knows it.">
            <Input value={key} onChange={(e) => setKey(e.target.value)} autoCapitalize="characters" spellCheck={false} />
          </Field>
          <Field label="Name" hint="Optional.">
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Share, in percent">
            <Input value={percent} onChange={(e) => setPercent(e.target.value)} inputMode="decimal" className="num" />
          </Field>
          <Field label="Of what">
            <select value={basis} onChange={(e) => setBasis(e.target.value as typeof basis)} className={SELECT_CLASS}>
              {OWNER_DENOMINATORS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </Field>
          <div className="sm:col-span-2">
            <Field label="Where this comes from" error={touched ? problem : undefined}>
              <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} className={TEXTAREA_CLASS} />
            </Field>
          </div>
          <div className="flex items-center gap-3 sm:col-span-2">
            <Button type="submit" size="sm" disabled={state.kind === "saving"}>Save my estimate</Button>
            <SaveNote state={state} />
          </div>
        </form>
      ) : null}
    </div>
  );
}

// ----------------------------------------------------------------------------------- portfolio
/** The whole portfolio, from the backend: each flow is the holding's weight times its confirmed share. */
export function LivePortfolio({ data }: { data: PortfolioDependencies }) {
  const g = liveSankey(data);
  const covered = Number(data.covered_weight);
  return (
    <div className="space-y-3">
      <p className="text-sm">
        <strong className="num">{(covered * 100).toFixed(1)}%</strong> of portfolio weight sits in holdings with a confirmed customer share, across <span className="num">{data.holdings}</span> {data.holdings === 1 ? "holding" : "holdings"}.
      </p>
      {g.links.length === 0 ? (
        <p className="text-sm text-text-muted">No holding has a confirmed customer share to draw.</p>
      ) : (
        <Sankey nodes={g.nodes} links={g.links} formatValue={(v) => `${(v * 100).toFixed(1)}% of portfolio`} label="Portfolio revenue exposure by confirmed customer" height={320} className="w-full" />
      )}
      {Object.keys(data.by_counterparty).length ? (
        <table className="w-full max-w-2xl border-collapse text-sm">
          <caption className="sr-only">Portfolio weight exposed to each named customer, all holdings together</caption>
          <thead>
            <tr className="border-b border-border text-left">
              <th scope="col" className="py-1.5 pr-3 font-medium">Customer</th>
              <th scope="col" className="py-1.5 text-right font-medium">Portfolio weight exposed</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(data.by_counterparty)
              .sort((a, b) => Number(b[1]) - Number(a[1]))
              .map(([who, v]) => (
                <tr key={who} className="border-b border-border-soft">
                  <th scope="row" className="py-1.5 pr-3 text-left font-medium">{who}</th>
                  <td className="num py-1.5 text-right">{(Number(v) * 100).toFixed(1)}%</td>
                </tr>
              ))}
          </tbody>
        </table>
      ) : null}
      {data.uncovered.length ? (
        <p className="max-w-[72ch] text-sm">
          <strong>Unknown, not zero:</strong> {data.uncovered.join(", ")} {data.uncovered.length === 1 ? "has" : "have"} no confirmed customer share, so {data.uncovered.length === 1 ? "its" : "their"} exposure is not drawn.
        </p>
      ) : null}
      <ul className="max-w-[72ch] space-y-0.5 text-xs text-text-muted">
        {data.notes.map((n) => (
          <li key={n}>{n}</li>
        ))}
        <li>Each holding uses its latest confirmed period only. The same customer name in two holdings is one node.</li>
      </ul>
    </div>
  );
}

export function PortfolioExposure({ holdings }: { holdings: readonly PortfolioHolding[] }) {
  const g = portfolioSankey(holdings);
  if (g.links.length === 0) return <p className="text-sm text-text-muted">No holding has a confirmed customer share to draw.</p>;
  return (
    <div className="space-y-3">
      <Sankey nodes={g.nodes} links={g.links} formatValue={(v) => `${(v * 100).toFixed(1)}% of portfolio`} label="Portfolio revenue exposure by confirmed customer" height={320} className="w-full" />
      <table className="w-full max-w-2xl border-collapse text-sm">
        <caption className="sr-only">Portfolio weight exposed to each confirmed customer</caption>
        <thead>
          <tr className="border-b border-border text-left">
            <th scope="col" className="py-1.5 pr-3 font-medium">Holding</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">Customer</th>
            <th scope="col" className="py-1.5 text-right font-medium">Portfolio weight exposed</th>
          </tr>
        </thead>
        <tbody>
          {g.rows.map((r) => (
            <tr key={`${r.holding}-${r.counterparty}-${r.value}`} className="border-b border-border-soft">
              <th scope="row" className="py-1.5 pr-3 text-left font-medium">{r.holding}</th>
              <td className="py-1.5 pr-3">{r.counterparty}</td>
              <td className="num py-1.5 text-right">{(r.value * 100).toFixed(1)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="max-w-[72ch] text-xs text-text-muted">
        Only confirmed shares of total revenue are drawn. “Not disclosed” is each holding’s weight that no confirmed claim covers. The same customer name in two holdings is one node; two unnamed customers are never merged.
      </p>
    </div>
  );
}
