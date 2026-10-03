"use client";

import { useCallback, useMemo } from "react";

import { DivergingBar, Legend } from "@/components/charts";
import { ChartFrame, ProvenanceFooter } from "@/components/data-state";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatCompact, formatSigned } from "@/lib/charts/format";
import { DATA_STATE_META } from "@/lib/data-state/states";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { compact, date, signedInt, weightPct } from "@/lib/xray/format";
import {
  breadthRows,
  buildRankFlow,
  CHANNEL_NOTE,
  nportState,
  ownershipProvenance,
  quarterLabel,
  stakePercentNote,
  stakeViews,
} from "@/lib/xray/ownership";
import { toNum } from "@/lib/xray/parse";
import type { ControlStakesOut, HoldingFundPositions, HoldingOwnership } from "@/lib/xray/types";
import { edgarFilingUrl } from "@/lib/reference/ownership";
import { formatOutstandingPct } from "@/lib/reference/shares-outstanding";

import { JobTrigger } from "./confirm-action";
import { DataTable } from "./facts";
import { SafeLink } from "./safe-link";
import { Panel, ResourceShell } from "./resource-shell";
import { RankFlowChart } from "./rank-flow";

const POSITION_LIMIT = 50;

export function OwnershipSection({ ticker }: { ticker: string }) {
  return (
    <div className="flex flex-col gap-5">
      <Panel
        id="own-channels"
        kicker="Four separate channels"
        title="Ownership is not one number"
        description={CHANNEL_NOTE}
      >
        <div className="flex flex-wrap gap-4">
          <JobTrigger job="ownership-13f" />
          <JobTrigger job="nport-holdings" />
          <JobTrigger job="control-stakes" />
        </div>
      </Panel>
      <InstitutionalBlock key={`13f-${ticker}`} ticker={ticker} />
      <FundPositionsBlock key={`nport-${ticker}`} ticker={ticker} />
      <ControlStakesBlock key={`13dg-${ticker}`} ticker={ticker} />
    </div>
  );
}

// ---- 13F ---------------------------------------------------------------------------------------

function InstitutionalBlock({ ticker }: { ticker: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/ownership", { params: { path: { ticker } } }),
    [ticker],
  );
  const resource = useApiResource(fetcher);
  return (
    <ResourceShell state={resource.state} label="13F ownership">
      {(own, lastGood) => <InstitutionalBody own={own} lastGood={lastGood} />}
    </ResourceShell>
  );
}

function InstitutionalBody({ own, lastGood }: { own: HoldingOwnership; lastGood: boolean }) {
  const base = useMemo(() => ownershipProvenance(own), [own]);
  const provenance = lastGood ? { ...base, state: "unavailable" as const, reason: "The refresh failed; this is the last value that loaded." } : base;
  const rows = useMemo(() => breadthRows(own.periods), [own.periods]);
  const flow = useMemo(() => buildRankFlow(own), [own]);
  const latest = rows[rows.length - 1];
  const hasPeriods = rows.length > 0;
  const so = own.shares_outstanding;

  const breadthTakeaway = latest
    ? `${latest.holders} managers held ${compact(latest.shares)} shares at the ${latest.quarter} quarter end${
        latest.holdersChange === null ? "" : `, ${signedInt(latest.holdersChange)} managers against the previous loaded quarter`
      }. The latest filing of the quarter was filed ${latest.lagDays === null ? "at an unknown lag" : `${latest.lagDays} days`} after quarter end (late amendments included).`
    : undefined;

  return (
    <>
      <ChartFrame
        id="own-breadth"
        kicker="Form 13F"
        title="Holder breadth and share changes by quarter"
        takeaway={breadthTakeaway}
        units={`Change against the previous loaded quarter, centred on zero. ${
          so ? `Percent of outstanding uses ${so.denominator_label} (${compact(toNum(so.shares))} shares as of ${date(so.as_of)}).` : "No shares-outstanding denominator, so no percent of outstanding."
        }${own.basis_label ? ` ${own.basis_label}.` : ""}`}
        provenance={provenance}
        hasValue={hasPeriods && own.applicable}
        table={{
          caption: "13F holders by quarter",
          columns: ["Quarter end", "Latest filing (lag)", "Holders", "Holders change", "New / exited", "Shares", "Shares change", "% of outstanding"],
          rows: rows
            .slice()
            .reverse()
            .map((r) => [
              `${r.quarter} · ${date(r.period)}`,
              `${date(r.filed)}${r.lagDays === null ? "" : ` (+${r.lagDays} d)`}`,
              String(r.holders),
              signedInt(r.holdersChange),
              `${r.newHolders ?? "—"} / ${r.exitedHolders ?? "—"}`,
              compact(r.shares),
              r.sharesChange === null ? "—" : `${r.sharesChange > 0 ? "+" : ""}${formatCompact(r.sharesChange)}`,
              formatOutstandingPct(r.pctOfOutstanding),
            ]),
        }}
      >
        {hasPeriods ? (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div>
              <h3 className="mb-2 text-sm font-semibold">Change in number of holders</h3>
              <DivergingBar
                label="Change in number of 13F holders by quarter"
                colorMode="direction"
                rows={rows.map((r) => ({ label: r.quarter, value: r.holdersChange }))}
                formatValue={(v) => formatSigned(v, 0)}
              />
            </div>
            <div>
              <h3 className="mb-2 text-sm font-semibold">Change in shares held</h3>
              <DivergingBar
                label="Change in 13F shares held by quarter"
                colorMode="direction"
                rows={rows.map((r) => ({ label: r.quarter, value: r.sharesChange }))}
                formatValue={(v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${formatCompact(Math.abs(v))}`}
              />
            </div>
          </div>
        ) : null}
        {rows.some((r) => r.holdersChange === null) ? (
          <p className="text-xs text-text-muted">
            A row marked “—” has no earlier loaded quarter to compare with: the change is unknown, not zero.
          </p>
        ) : null}
        {rows.some((r) => (r.quartersSincePrevious ?? 1) > 1) ? (
          <p role="status" className="text-xs text-text font-medium">
            A quarter is missing between some loaded quarters, so those changes span more than one quarter.
          </p>
        ) : null}
        {rows.some((r) => r.changeSpanUnknown) ? (
          <p role="status" className="text-xs text-text">
            <span aria-hidden="true" className="text-warning-signal">▲ </span>
            The oldest row&apos;s change was computed against an earlier quarter that is not in this list, so how many quarters it spans is unknown.
          </p>
        ) : null}
        <Legend
          items={[
            { label: "Increase", swatch: "square", className: "bg-diverging-over" },
            { label: "Decrease", swatch: "square", className: "bg-diverging-under" },
          ]}
        />
      </ChartFrame>

      <ChartFrame
        id="own-rank"
        kicker="Form 13F"
        title="Top-holder rank flow"
        takeaway={
          flow
            ? `${flow.rows[0].name} is the largest reported holder at the ${flow.latestPeriod ? quarterLabel(flow.latestPeriod) : "latest"} quarter end with ${compact(flow.rows[0].shares)} shares${
                flow.rows[0].pctOfOutstanding !== null && flow.rows[0].pctOfOutstanding !== undefined ? ` (${weightPct(flow.rows[0].pctOfOutstanding)} of shares outstanding)` : ""
              }.`
            : undefined
        }
        units="Rank by shares held among the latest quarter's top holders. Managers that left that list are not in the data, so a rank is relative to this list."
        provenance={provenance}
        hasValue={flow !== null && own.applicable}
      >
        {flow ? <RankFlowChart flow={flow} label="Rank of top 13F holders, previous quarter to latest quarter" /> : null}
        <p className="text-xs text-text-muted">
          13F shows quarter-end positions filed up to 45 days later; it omits shorts, most non-US holdings and managers below the threshold. Each manager&apos;s filing:{" "}
          {flow
            ? flow.rows
                .slice(0, 5)
                .map((r) => ({ r, url: edgarFilingUrl(r.cik, r.accession) }))
                .filter((x) => x.url)
                .map((x, i) => (
                  <span key={x.r.cik}>
                    {i > 0 ? ", " : ""}
                    <SafeLink href={x.url} className="text-accent-ink hover:underline">
                      {x.r.name}
                    </SafeLink>
                  </span>
                ))
            : null}
        </p>
      </ChartFrame>
    </>
  );
}

// ---- N-PORT --------------------------------------------------------------------------------------

function FundPositionsBlock({ ticker }: { ticker: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/fund-positions", { params: { path: { ticker }, query: { limit: POSITION_LIMIT } } }),
    [ticker],
  );
  const resource = useApiResource(fetcher);
  return (
    <ResourceShell state={resource.state} label="N-PORT fund positions">
      {(data, lastGood) => <FundPositionsBody data={data} lastGood={lastGood} />}
    </ResourceShell>
  );
}

function FundPositionsBody({ data, lastGood }: { data: HoldingFundPositions; lastGood: boolean }) {
  const st = nportState(data.coverage);
  const provenance = {
    source: "SEC N-PORT filings (fund positions)",
    asOf: data.coverage.latest_report_period_end,
    method: "Each fund's reported position at its own report date; public about 60 days after. Scope: " + data.coverage.scope,
    state: lastGood ? ("unavailable" as const) : st.state,
    reason: lastGood ? "The refresh failed; this is the last value that loaded." : st.reason,
    coverage: data.coverage.funds_loaded > 0 ? { covered: data.coverage.funds_holding, total: data.coverage.funds_loaded, unit: "loaded funds hold it" } : null,
  };
  const showable = DATA_STATE_META[provenance.state].showsValue || lastGood;
  return (
    <Panel id="own-nport" kicker="SEC N-PORT" title="Fund positions in this holding" description={data.notice}>
      <>
        {showable || data.positions.length > 0 ? (
          data.positions.length === 0 ? (
            <p className="text-sm text-text-muted">No tracked fund reports a position. This is not evidence that no fund holds it.</p>
          ) : (
            <>
            {data.positions.length >= POSITION_LIMIT ? (
              <p className="text-xs text-text-muted">Showing the {POSITION_LIMIT} requested rows; more funds may report a position (the API does not say).</p>
            ) : null}
            <DataTable
              caption="N-PORT fund positions with report date and filing lag. Not additive."
              minWidth="60rem"
              columns={[
                { label: "Fund (series)" },
                { label: "Report date", align: "right" },
                { label: "Filed (lag)", align: "right" },
                { label: "Public after", align: "right" },
                { label: "Position", align: "right" },
                { label: "Value (USD)", align: "right" },
                { label: "% of fund", align: "right" },
                { label: "Mapped by" },
              ]}
              rows={data.positions.map((p) => [
                <span key="f">
                  <span className="font-medium">{p.fund_name ?? p.series_id}</span>
                  <span className="block text-[11px] text-text-muted">{p.registrant_name ?? p.series_id}</span>
                </span>,
                <span key="r">
                  {date(p.report_period_end)}
                  <span className={p.stale ? "block text-[11px] text-text font-medium" : "block text-[11px] text-text-muted"}>
                    {p.age_days} d old{p.stale ? " · stale" : ""}
                  </span>
                </span>,
                `${date(p.filed)} (+${p.lag_days} d)`,
                date(p.public_after),
                `${compact(toNum(p.balance))} ${p.units ?? ""}${p.is_derivative ? " (derivative)" : ""}`,
                compact(toNum(p.value_usd)),
                toNum(p.pct_of_fund) === null ? "—" : `${(toNum(p.pct_of_fund) as number).toFixed(2)}%`,
                p.map_basis ?? "—",
              ])}
            />
            </>
          )
        ) : null}
      <ProvenanceFooter provenance={provenance} />
      </>
    </Panel>
  );
}

// ---- 13D/G ------------------------------------------------------------------------------------

function ControlStakesBlock({ ticker }: { ticker: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/control-stakes", { params: { path: { ticker } } }),
    [ticker],
  );
  const resource = useApiResource(fetcher);
  return (
    <ResourceShell state={resource.state} label="Schedule 13D/G stakes">
      {(data) => <ControlStakesBody data={data} />}
    </ResourceShell>
  );
}

function ControlStakesBody({ data }: { data: ControlStakesOut }) {
  const views = stakeViews(data.stakes);
  const newest = views[0]?.stake.filed ?? null;
  return (
    <Panel
      id="own-control"
      kicker="Schedule 13D/G"
      title="Beneficial-ownership and control stakes"
      description={`A separate channel: percent of one share class as the filer reports it. ${data.not_additive_with?.length ? `Never added to ${data.not_additive_with.join(", ")}.` : "Never added to 13F or fund figures."}`}
    >
      {views.length === 0 ? (
        <p className="text-sm text-text-muted">No 13D/G filing is stored for this holding. That means none has been loaded, not that no one holds a large stake.</p>
      ) : (
        <>
          <p className="text-xs text-text-muted">
            Latest filing of each amendment chain; newest filed {date(newest)}. Percent is in percent points of the stated class.
          </p>
          <DataTable
            caption="Schedule 13D/G stakes: latest filing of each chain"
            minWidth="62rem"
            columns={[
              { label: "Reporting person(s)" },
              { label: "Filing" },
              { label: "Filed / event", align: "right" },
              { label: "Class" },
              { label: "Shares", align: "right" },
              { label: "% of class (largest single reporting person)", align: "right" },
              { label: "Cross-check" },
              { label: "Purpose and changes" },
            ]}
            rows={views.map((v) => [
              <span key="p">
                {v.stake.holders.length === 0 ? "Not parsed" : v.stake.holders.map((h) => h.person_name).join(", ")}
                {stakePercentNote(v) ? <span className="block text-[11px] text-text-muted">{stakePercentNote(v)}</span> : null}
                {v.isGroup && v.stake.holders.some((h) => toNum(h.percent_of_class) !== null) ? (
                  <span className="block text-[11px] text-text-muted">
                    {v.stake.holders.map((h) => `${h.person_name} ${toNum(h.percent_of_class) === null ? "—" : `${toNum(h.percent_of_class)}%`}`).join(" · ")}
                  </span>
                ) : null}
              </span>,
              <SafeLink key="f" href={v.stake.source_url} className="text-accent-ink hover:underline">
                {v.stake.form}
                {v.stake.is_amendment ? ` amendment ${v.stake.amendment_no ?? ""}` : " initial"}
              </SafeLink>,
              <span key="d">
                {date(v.stake.filed)}
                <span className="block text-[11px] text-text-muted">event {date(v.stake.event_date)}</span>
              </span>,
              v.stake.class_title ?? "class not stated",
              compact(v.shares),
              v.percent === null ? "—" : `${v.percent.toFixed(2)}%`,
              <span key="c" className="text-[11px]">
                {v.checkLabel}
                {v.stake.reconciliation.class_status === "class_unreconciled" ? <span className="block text-text font-medium">share class not reconciled to the issuer</span> : null}
              </span>,
              <span key="i" className="text-[11px] text-text-muted">
                {v.stake.intent_text ?? "No purpose text parsed"}
                {v.stake.changes.length > 0 ? (
                  <span className="block text-text">
                    {v.stake.changes.map((c) => `${c.change_type.replace(/_/g, " ")}${c.detail ? `: ${c.detail}` : ""}`).join("; ")}
                  </span>
                ) : null}
              </span>,
            ])}
          />
          {data.superseded.length > 0 ? (
            <details className="border border-border-soft text-xs">
              <summary className="cursor-pointer px-3 py-1.5 font-medium text-accent-ink select-none hover:underline">
                {data.superseded.length} earlier filings of these chains
              </summary>
              <ul className="space-y-1 border-t border-border-soft px-3 py-2 text-text-muted">
                {data.superseded.map((s) => (
                  <li key={s.accession}>
                    {date(s.filed)} · {s.form} · {toNum(s.stake_percent) === null ? "—" : `${toNum(s.stake_percent)}%`} of class
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </>
      )}
      <Limitations items={data.limitations} />
    </Panel>
  );
}

export function Limitations({ items }: { items: readonly string[] }) {
  if (items.length === 0) return null;
  return (
    <details className="border border-border-soft text-xs">
      <summary className="cursor-pointer px-3 py-1.5 font-medium text-accent-ink select-none hover:underline">Limitations</summary>
      <ul className="list-disc space-y-1 border-t border-border-soft py-2 pr-3 pl-8 text-text-muted">
        {items.map((l, i) => (
          <li key={i}>{l}</li>
        ))}
      </ul>
    </details>
  );
}
