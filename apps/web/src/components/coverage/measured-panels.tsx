"use client";

import { useState } from "react";

import { DataStateBadge, Segmented, StatePlaceholder } from "@/components/data-state";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import type { components } from "@/lib/api/schema";
import { priceHoldingState } from "@/lib/coverage/logic";
import { toDataState } from "@/lib/data-state/states";
import { formatDate, formatDateTime } from "@/lib/format";

type Schemas = components["schemas"];

const pct = (f: number | null) => (f === null ? "unknown" : `${(f * 100).toFixed(1)}%`);
const money = (v: number) => v.toLocaleString("en-GB", { maximumFractionDigits: 0 });
const day = (v: string | null) => (v ? formatDate(v.slice(0, 10)) : "Unknown");

const TH = "px-2 py-1.5 font-medium";
const HEAD = "border-b border-border text-left text-[11px] tracking-[0.08em] text-text-muted uppercase";

/**
 * Value-weighted held-price coverage (GET /portfolio/price-coverage): each holding's state, its
 * share of held value and why. A holding without a usable close is the unknown bucket, never a
 * zero price.
 */
export function PriceCoveragePanel({ data }: { data: Schemas["PriceCoverage"] }) {
  const cur = data.base_currency ? ` ${data.base_currency}` : "";
  return (
    <Blueprint as="section" aria-labelledby="price-heading" id="price-coverage" className="flex scroll-mt-24 flex-col gap-3 px-5 py-5">
      <PanelHeading kicker="Price coverage" title={<span id="price-heading">Held market value with a usable close</span>} />
      {data.holdings_total === 0 ? (
        <StatePlaceholder state="not_covered" reason="No held positions in the latest snapshot, so no price coverage is measured." />
      ) : (
        <>
          <p className="max-w-prose text-sm text-text">
            {pct(data.usable_share)} of held market value ({money(data.usable_value_base)} of {money(data.denominator_base)}
            {cur}) has a usable latest close; the unknown bucket is {pct(data.unknown_share)} ({money(data.unknown_value_base)}
            {cur}). Snapshot {day(data.snapshot_date)}, judged on {day(data.as_of)}; a close older than {data.max_stale_sessions} sessions is stale.
          </p>
          <div className="relative max-h-[26rem] overflow-auto border border-border-soft">
            <table className="w-full min-w-[52rem] text-xs">
              <caption className="sr-only">Price coverage per held position, largest value first</caption>
              <thead className="sticky top-0 bg-surface-muted">
                <tr className={HEAD}>
                  {["Holding", "State", "Share of held value", "Last close", "Sessions old", "Why"].map((c) => (
                    <th key={c} scope="col" className={TH}>
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.holdings.map((h) => (
                  <tr key={h.ticker} className="border-b border-border-soft align-top last:border-0">
                    <th scope="row" className="num px-2 py-1.5 text-left font-medium">
                      {h.ticker}
                      {h.name ? <span className="block font-normal text-text-muted">{h.name}</span> : null}
                    </th>
                    <td className="px-2 py-1.5">
                      <DataStateBadge state={priceHoldingState(h.state)} reason={h.reason} />
                    </td>
                    <td className="num px-2 py-1.5">{pct(h.share_of_held_value)}</td>
                    <td className="num px-2 py-1.5">
                      {h.last_close !== null && h.last_close_date ? (
                        <>
                          {h.last_close.toLocaleString("en-GB", { maximumFractionDigits: 4 })} {h.currency ?? "currency not recorded"}
                          <span className="block text-text-muted">{day(h.last_close_date)}{h.adjustment_basis ? ` · ${h.adjustment_basis}` : ""}</span>
                        </>
                      ) : (
                        "No stored close"
                      )}
                    </td>
                    <td className="num px-2 py-1.5">{h.stale_sessions ?? "Unknown"}</td>
                    <td className="px-2 py-1.5 text-text-muted">{h.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Blueprint>
  );
}

type ReachGroup = "exchange" | "country" | "asset_class" | "cells";

const REACH_GROUPS: readonly { id: ReachGroup; label: string }[] = [
  { id: "exchange", label: "By exchange" },
  { id: "country", label: "By country" },
  { id: "asset_class", label: "By asset class" },
  { id: "cells", label: "Exchange x country x class" },
];

/** Rows of a reach grouping, in the response's own order. */
export function reachRows(data: Schemas["DataReach"], group: ReachGroup): Schemas["ReachRow"][] {
  return group === "exchange" ? data.by_exchange : group === "country" ? data.by_country : group === "asset_class" ? data.by_asset_class : data.cells;
}

function reachLabel(r: Schemas["ReachRow"], group: ReachGroup): string {
  const unknown = "Unknown";
  if (group === "exchange") return r.exchange_mic ?? `${unknown} exchange`;
  if (group === "country") return r.country ?? `${unknown} country`;
  if (group === "asset_class") return r.asset_class ?? `${unknown} class`;
  return [r.exchange_mic ?? "?", r.country ?? "?", r.asset_class ?? "?"].join(" / ");
}

/**
 * Stored reach by exchange, country, asset class and metric (GET /data/reach). Counts are of the
 * app's own universe (held, watchlist, tested symbols) and are never a share of a global universe.
 */
export function ReachPanel({ data }: { data: Schemas["DataReach"] }) {
  const [group, setGroup] = useState<ReachGroup>("exchange");
  const rows = reachRows(data, group);
  return (
    <Blueprint as="section" aria-labelledby="reach-dash-heading" id="reach-dashboard" className="flex scroll-mt-24 flex-col gap-3 px-5 py-5">
      <PanelHeading kicker="Global reach" title={<span id="reach-dash-heading">Stored coverage by exchange, country, asset class and metric</span>} />
      <p className="max-w-prose text-sm text-text">
        {data.tested_symbols} tested symbols ({data.held_tickers} held positions, {data.watchlist_tickers} watchlist tickers), as of {formatDateTime(data.as_of)}. {data.universe_note}
      </p>
      {data.tested_symbols === 0 ? (
        <StatePlaceholder state="not_covered" reason="No symbol has a stored price, fundamentals, statement fact or ledger row yet." />
      ) : (
        <>
          <Segmented label="Reach grouping" options={REACH_GROUPS} value={group} onChange={setGroup} />
          <div className="relative max-h-[26rem] overflow-auto border border-border-soft">
            <table className="w-full min-w-[56rem] text-xs">
              <caption className="sr-only">Stored coverage {REACH_GROUPS.find((g) => g.id === group)?.label.toLowerCase()} and metric</caption>
              <thead className="sticky top-0 bg-surface-muted">
                <tr className={HEAD}>
                  {["Group", "Metric", "Tested symbols", "Valid records", "History", "Records in history", "Freshest known at", "States"].map((c) => (
                    <th key={c} scope="col" className={TH}>
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={`${reachLabel(r, group)}|${r.metric}`} className="border-b border-border-soft align-top last:border-0">
                    <th scope="row" className="num px-2 py-1.5 text-left font-medium">
                      {reachLabel(r, group)}
                    </th>
                    <td className="num px-2 py-1.5 break-all">{r.metric}</td>
                    <td className="num px-2 py-1.5">{r.tested_symbols}</td>
                    <td className="num px-2 py-1.5">{r.valid_records}</td>
                    <td className="num px-2 py-1.5">{r.history_first && r.history_last ? `${day(r.history_first)} to ${day(r.history_last)}` : "Unknown"}</td>
                    <td className="num px-2 py-1.5">{r.history_count}</td>
                    <td className="num px-2 py-1.5">{r.freshest_known_at ? formatDateTime(r.freshest_known_at) : "Unknown"}</td>
                    <td className="px-2 py-1.5">
                      {Object.keys(r.coverage_states).length === 0 ? (
                        <span className="text-text-muted">No state column</span>
                      ) : (
                        <ul className="m-0 flex list-none flex-wrap gap-1 p-0">
                          {Object.entries(r.coverage_states).map(([s, n]) => (
                            <li key={s} className="inline-flex items-center gap-1">
                              <DataStateBadge state={toDataState(s)} />
                              <span className="num">{n}</span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {rows.length === 0 ? <p className="text-xs text-text-muted">No rows in this grouping.</p> : null}
        </>
      )}
    </Blueprint>
  );
}

const DIMENSIONS: Record<string, string> = { source: "Source", as_of: "As-of", known_at: "Known at", method: "Method / vintage", access_state: "Access state" };

/** Provenance completeness per displayed fact table (GET /data/provenance-completeness). */
export function ProvenancePanel({ data }: { data: Schemas["ProvenanceCompleteness"] }) {
  return (
    <Blueprint as="section" aria-labelledby="prov-heading" id="provenance" className="flex scroll-mt-24 flex-col gap-3 px-5 py-5">
      <PanelHeading kicker="Provenance" title={<span id="prov-heading">Rows carrying source, as-of, known-at, method and access state</span>} />
      <p className="max-w-prose text-sm text-text">
        {data.tables_meeting_target} of {data.tables_total} tables meet the {pct(data.target)} target.
        {data.empty_tables.length ? ` Empty tables are not judged: ${data.empty_tables.join(", ")}.` : ""} A dimension with no stored column counts as not carried.
      </p>
      <div className="relative overflow-x-auto border border-border-soft">
        <table className="w-full min-w-[46rem] text-xs">
          <caption className="sr-only">Provenance completeness per fact table: rows carrying each dimension, out of all rows</caption>
          <thead className="bg-surface-muted">
            <tr className={HEAD}>
              <th scope="col" className={TH}>
                Table
              </th>
              <th scope="col" className={TH}>
                Rows
              </th>
              {Object.values(DIMENSIONS).map((c) => (
                <th key={c} scope="col" className={TH}>
                  {c}
                </th>
              ))}
              <th scope="col" className={TH}>
                Complete rows
              </th>
            </tr>
          </thead>
          <tbody>
            {data.tables.map((t) => (
              <tr key={t.table} className="border-b border-border-soft align-top last:border-0">
                <th scope="row" className="num px-2 py-1.5 text-left font-medium">
                  {t.table}
                </th>
                <td className="num px-2 py-1.5">{t.rows}</td>
                {t.dimensions.map((d) => (
                  <td key={d.dimension} className="num px-2 py-1.5">
                    {d.column === null ? (
                      "No column"
                    ) : t.rows === 0 ? (
                      "Empty table"
                    ) : (
                      <>
                        {pct(d.share)}
                        <span className="block text-text-muted">
                          {d.present} of {t.rows}
                          {d.missing > 0 ? ` · ${d.missing} missing` : ""}
                        </span>
                      </>
                    )}
                  </td>
                ))}
                <td className="num px-2 py-1.5">
                  {t.rows === 0 ? "Empty table" : t.complete_share === null ? `${t.complete_rows} (not all dimensions have a column)` : `${pct(t.complete_share)} (${t.complete_rows} of ${t.rows})`}
                  <span className="block text-text-muted">{t.meets_target ? "Meets target" : t.rows === 0 ? "Not judged" : "Below target"}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Blueprint>
  );
}

/** Identity review (GET /portfolio/identity-review): reviewed share and the unresolved exceptions. */
export function IdentityReviewPanel({ data }: { data: Schemas["IdentityReview"] }) {
  return (
    <Blueprint as="section" aria-labelledby="identity-heading" id="identity-review" className="flex scroll-mt-24 flex-col gap-3 px-5 py-5">
      <PanelHeading kicker="Identity" title={<span id="identity-heading">Held positions with a reviewed issuer/security/listing path</span>} />
      {data.held_positions === 0 ? (
        <StatePlaceholder state="not_covered" reason="No held positions in the latest snapshot, so no identity share is measured." />
      ) : (
        <>
          <p className="max-w-prose text-sm text-text">
            {data.reviewed} of {data.held_positions} held positions ({pct(data.reviewed_share)}) have a confirmed path: {data.owner_reviewed} confirmed by you, {data.auto_confirmed} by the matcher&apos;s evidence rules.{" "}
            {data.unresolved_count === 0 ? "No unresolved exception." : `${data.unresolved_count} unresolved ${data.unresolved_count === 1 ? "exception is" : "exceptions are"} listed below.`}
          </p>
          {data.unresolved.length > 0 ? (
            <div className="relative overflow-x-auto border border-border-soft">
              <table className="w-full min-w-[40rem] text-xs">
                <caption className="sr-only">Held positions without a reviewed identity path</caption>
                <thead className="bg-surface-muted">
                  <tr className={HEAD}>
                    {["Holding", "Link status", "Match score", "Last decision", "Identity error"].map((c) => (
                      <th key={c} scope="col" className={TH}>
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.unresolved.map((u) => (
                    <tr key={u.ticker} className="border-b border-border-soft align-top last:border-0">
                      <th scope="row" className="num px-2 py-1.5 text-left font-medium">
                        {u.ticker}
                        {u.name ? <span className="block font-normal text-text-muted">{u.name}</span> : null}
                      </th>
                      <td className="px-2 py-1.5">{u.link_status.replace(/_/g, " ")}</td>
                      <td className="num px-2 py-1.5">{u.score === null ? "No score" : u.score.toFixed(2)}</td>
                      <td className="num px-2 py-1.5">{u.last_decision ? `${u.last_decision}${u.last_decision_at ? `, ${formatDateTime(u.last_decision_at)}` : ""}` : "No decision recorded"}</td>
                      <td className="px-2 py-1.5">{u.identity_error ?? "None recorded"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </>
      )}
    </Blueprint>
  );
}
