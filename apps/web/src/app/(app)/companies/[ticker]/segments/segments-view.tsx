"use client";

import { useCallback, useState, type FormEvent } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Absent, ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { toNumber, type SegmentAnalysis, type SegmentBreakdown } from "@/lib/company/company";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

const COMPACT = new Intl.NumberFormat("en-GB", { notation: "compact", maximumFractionDigits: 1 });
const ROLE_NOTE: Record<string, string> = { elimination: "elimination", unallocated: "not allocated", subtotal: "subtotal", operating: "" };
const CONTEXTS = [
  ["country", "Country"],
  ["commodity", "Commodity"],
  ["patent_tech", "Patent technology (CPC)"],
  ["trade_hs", "Trade product (HS code)"],
] as const;

function amount(v: string | number | null | undefined): string | null {
  const n = toNumber(v);
  return n === null ? null : COMPACT.format(n);
}

function percent(v: string | number | null | undefined): string | null {
  const n = toNumber(v);
  return n === null ? null : `${(n * 100).toFixed(1)}%`;
}

function BreakdownTable({ b, currency }: { b: SegmentBreakdown; currency: string | null }) {
  return (
    <Blueprint aria-labelledby={`bd-${b.kind}`} className="space-y-3 px-[22px] py-5">
      <PanelHeading kicker={`Revenue${currency ? `, ${currency}` : ""}${b.latest_period ? ` · latest ${b.latest_period}` : ""}`} title={<span id={`bd-${b.kind}`}>{b.label}</span>} />
      {b.notes.map((n) => (
        <p key={n} className="text-sm text-text-muted">
          {n}
        </p>
      ))}
      <div className="overflow-x-auto border border-border">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="bg-surface-muted text-xs">
              <th scope="col" className="sticky left-0 min-w-48 bg-surface-muted px-3 py-1.5 text-left font-medium">Member</th>
              {b.periods.map((p) => (
                <th key={p} scope="col" className="px-2 py-1.5 text-right font-medium">
                  {p}
                </th>
              ))}
              <th scope="col" className="px-2 py-1.5 text-right font-medium">Share</th>
              <th scope="col" className="px-2 py-1.5 text-right font-medium">Change</th>
            </tr>
          </thead>
          <tbody>
            {b.members.map((m) => (
              <tr key={m.key} className={cn("border-t border-border-soft align-top", m.role !== "operating" && "text-text-muted")}>
                <th scope="row" className="sticky left-0 bg-surface px-3 py-1 text-left font-normal">
                  {m.label}
                  {ROLE_NOTE[m.role] ? <span className="ml-2 text-xs">({ROLE_NOTE[m.role]})</span> : null}
                  {m.definition_note ? <span className="block text-xs text-unconfirmed">{m.definition_note}</span> : null}
                </th>
                {b.periods.map((p) => {
                  const v = amount(m.values[p]);
                  return (
                    <td key={p} className="num px-2 py-1 text-right">
                      {v ?? <Absent reason="missing: not reported that period" />}
                    </td>
                  );
                })}
                <td className="num px-2 py-1 text-right">{percent(m.latest_share) ?? ""}</td>
                <td className="num px-2 py-1 text-right">{percent(m.change) ?? ""}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            {[
              ["Operating revenue", b.operating_total],
              ["Eliminations", b.eliminations],
              ["Not allocated", b.unallocated],
              ["After eliminations", b.after_eliminations],
              ["Reported total revenue", b.reported_total],
            ].map(([label, v]) => (
              <tr key={label as string} className="border-t border-border-soft">
                <th scope="row" className="sticky left-0 bg-surface px-3 py-1 text-left font-medium">
                  {label}
                </th>
                <td colSpan={b.periods.length + 2} className="num px-2 py-1 text-right">
                  {amount(v as string | null) ?? <Absent reason="not reported" />}
                </td>
              </tr>
            ))}
          </tfoot>
        </table>
      </div>
      {b.difference_note ? <AlertMessage title={`Difference from reported revenue: ${amount(b.difference) ?? "unknown"}`}>{b.difference_note}</AlertMessage> : null}
    </Blueprint>
  );
}

function MapForm({ ticker, analysis, onSaved }: { ticker: string; analysis: SegmentAnalysis; onSaved: () => void }) {
  const members = analysis.breakdowns.flatMap((b) => b.members.filter((m) => m.role === "operating").map((m) => ({ breakdown: b.kind, label: m.label })));
  const [pick, setPick] = useState("");
  const [context, setContext] = useState<(typeof CONTEXTS)[number][0]>("country");
  const [value, setValue] = useState("");
  const [share, setShare] = useState("100");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const chosen = members[Number(pick)];
    const pct = Number(share);
    if (!chosen || !value.trim() || !Number.isFinite(pct) || pct < 0 || pct > 100) {
      setError("Choose a member, say what it maps to, and give a share between 0 and 100.");
      return;
    }
    setBusy(true);
    setError(null);
    const { error: err } = await getBrowserApiClient().PUT("/companies/{ticker}/segment-map", {
      params: { path: { ticker } },
      body: {
        breakdown: chosen.breakdown as "geographic" | "segment" | "product",
        member_label: chosen.label,
        context,
        value: value.trim(),
        share: String(pct / 100),
        note: note.trim() || null,
      },
    });
    setBusy(false);
    if (err) {
      setError("The mapping could not be saved. Check the values and try again.");
      return;
    }
    setValue("");
    setNote("");
    onSaved();
  }

  if (members.length === 0) return <p className="text-sm text-text-muted">No reported members to map yet.</p>;
  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
      <label className="min-w-56 text-sm">
        <span className="mb-1 block text-xs text-text-muted">Reported member</span>
        <select value={pick} onChange={(e) => setPick(e.target.value)} className="w-full border border-border bg-surface px-2 py-1.5">
          <option value="">Choose…</option>
          {members.map((m, i) => (
            <option key={`${m.breakdown}-${m.label}`} value={i}>
              {m.label} ({m.breakdown})
            </option>
          ))}
        </select>
      </label>
      <label className="text-sm">
        <span className="mb-1 block text-xs text-text-muted">Maps to</span>
        <select value={context} onChange={(e) => setContext(e.target.value as typeof context)} className="border border-border bg-surface px-2 py-1.5">
          {CONTEXTS.map(([v, t]) => (
            <option key={v} value={v}>
              {t}
            </option>
          ))}
        </select>
      </label>
      <label className="text-sm">
        <span className="mb-1 block text-xs text-text-muted">Value (for example US, or copper)</span>
        <input value={value} onChange={(e) => setValue(e.target.value)} className="border border-border bg-surface px-2 py-1.5" />
      </label>
      <label className="w-24 text-sm">
        <span className="mb-1 block text-xs text-text-muted">Share, %</span>
        <input value={share} onChange={(e) => setShare(e.target.value)} inputMode="decimal" className="num w-full border border-border bg-surface px-2 py-1.5" />
      </label>
      <label className="min-w-48 flex-1 text-sm">
        <span className="mb-1 block text-xs text-text-muted">Why you think so (optional)</span>
        <input value={note} onChange={(e) => setNote(e.target.value)} className="w-full border border-border bg-surface px-2 py-1.5" />
      </label>
      <button type="submit" disabled={busy} className="border border-border px-3 py-1.5 text-sm hover:border-accent disabled:opacity-50">
        Save mapping
      </button>
      {error ? (
        <p role="alert" className="w-full text-sm text-loss">
          {error}
        </p>
      ) : null}
    </form>
  );
}

export function SegmentsView({ ticker }: { ticker: string }) {
  const [basis, setBasis] = useState<"annual" | "quarter">("annual");
  const fetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/segments", { params: { path: { ticker }, query: { basis } } }), [ticker, basis]);
  const { state, reload } = useApiResource(fetcher);

  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="px-[22px] py-4">
        <fieldset>
          <legend className="mb-1 text-xs text-text-muted">Period</legend>
          <div className="inline-flex border border-border">
            {(
              [
                ["annual", "Annual"],
                ["quarter", "Quarterly"],
              ] as const
            ).map(([v, t]) => (
              <label key={v} className={cn("cursor-pointer border-r border-border px-2.5 py-1 text-sm last:border-r-0 has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-focus/30", basis === v ? "bg-surface-muted font-semibold" : "text-text-muted hover:text-text")}>
                <input type="radio" name="segment-basis" checked={basis === v} onChange={() => setBasis(v)} className="sr-only" />
                {t}
              </label>
            ))}
          </div>
        </fieldset>
      </Blueprint>

      <ResourceGate state={state} label="the segment breakdowns">
        {(a: SegmentAnalysis) => (
          <>
            {a.source_note ? <p className="text-sm text-text-muted">{a.source_note}</p> : null}
            {a.notes.map((n) => (
              <AlertMessage key={n} title="Note">
                {n}
              </AlertMessage>
            ))}
            {a.breakdowns.length === 0 ? (
              <Blueprint className="px-[22px] py-5">
                <p className="text-sm text-text-muted">No segment, geography or product revenue is stored for this company in this period basis.</p>
              </Blueprint>
            ) : (
              a.breakdowns.map((b) => <BreakdownTable key={b.kind} b={b} currency={a.currency} />)
            )}

            <Blueprint aria-labelledby="seg-map" className="space-y-4 px-[22px] py-5">
              <PanelHeading kicker="Your estimate, not a reported figure" title={<span id="seg-map">Map segments to countries, commodities, patents or trade</span>} />
              <p className="text-sm text-text-muted">
                A mapping says how much of a reported member you assign to something else, for example that a segment is 60% US. Exposure below is built only from your mappings; what you have
                not mapped stays visible as unmapped.
              </p>
              <MapForm ticker={ticker} analysis={a} onSaved={reload} />
              {a.mappings.length > 0 ? (
                <ul className="space-y-1 text-sm">
                  {a.mappings.map((m) => (
                    <li key={m.id} className="flex flex-wrap gap-x-2">
                      <span className="font-medium">{m.member_label}</span>
                      <span className="text-text-muted">
                        → {CONTEXTS.find(([v]) => v === m.context)?.[1] ?? m.context}: {m.value}, {((toNumber(m.share) ?? 0) * 100).toFixed(0)}%
                      </span>
                      {m.note ? <span className="text-xs text-text-muted">({m.note})</span> : null}
                    </li>
                  ))}
                </ul>
              ) : null}
              {a.exposures.map((x) => (
                <div key={`${x.breakdown}-${x.context}`} className="space-y-2">
                  <p className="text-sm font-medium">
                    {x.context_label}, from {x.breakdown} revenue{x.period ? `, ${x.period}` : ""}
                  </p>
                  <p className="text-xs text-text-muted">{x.label}</p>
                  <div className="overflow-x-auto border border-border">
                    <table className="w-full border-collapse text-sm">
                      <tbody>
                        {x.lines.map((l) => (
                          <tr key={l.value} className="border-t border-border-soft first:border-t-0">
                            <th scope="row" className="px-3 py-1 text-left font-normal">
                              {l.value}
                            </th>
                            <td className="num px-2 py-1 text-right">{amount(l.revenue)}</td>
                            <td className="num px-2 py-1 text-right">{percent(l.share) ?? <Absent reason="share not computable" />}</td>
                          </tr>
                        ))}
                        <tr className="border-t border-border-soft text-text-muted">
                          <th scope="row" className="px-3 py-1 text-left font-normal">Unmapped</th>
                          <td className="num px-2 py-1 text-right">{amount(x.unmapped)}</td>
                          <td />
                        </tr>
                        <tr className="border-t border-border-soft text-text-muted">
                          <th scope="row" className="px-3 py-1 text-left font-normal">Not allocated by the company</th>
                          <td className="num px-2 py-1 text-right">{amount(x.unallocated)}</td>
                          <td />
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  {x.warnings.map((w) => (
                    <p key={w} className="text-xs text-unconfirmed">
                      {w}
                    </p>
                  ))}
                </div>
              ))}
            </Blueprint>
            <p className="text-xs text-text-muted">Method {a.method_version}.</p>
          </>
        )}
      </ResourceGate>
    </div>
  );
}
