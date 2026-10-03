"use client";

import { useCallback, useMemo } from "react";

import { Legend } from "@/components/charts";
import { ChartFrame, DataStateBadge, ProvenanceFooter } from "@/components/data-state";
import { getBrowserApiClient } from "@/lib/api/client";
import type { DataProvenance } from "@/lib/data-state/provenance";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { compact, date, num, signedInt } from "@/lib/xray/format";
import {
  classMeta,
  classTotals,
  countsTowardNetText,
  exclusionText,
  INSIDER_GROUP_LABEL,
  insiderProvenance,
  netValueNote,
  netValueText,
  PDMR_STATUS_LABEL,
  pdmrHoldingState,
  type InsiderGroup,
} from "@/lib/xray/insider";
import { toNum } from "@/lib/xray/parse";
import type { HoldingPdmrOut, InsiderActivityOut } from "@/lib/xray/types";

import { JobTrigger } from "./confirm-action";
import { DataTable } from "./facts";
import { SafeLink } from "./safe-link";
import { Limitations } from "./ownership-section";
import { Panel, ResourceShell } from "./resource-shell";

const ROW_LIMIT = 200;

export function InsiderSection({ ticker }: { ticker: string }) {
  return (
    <div className="flex flex-col gap-5">
      <InsiderActivityBlock ticker={ticker} />
      <PdmrBlock ticker={ticker} />
    </div>
  );
}

function InsiderActivityBlock({ ticker }: { ticker: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/insider-activity", { params: { path: { ticker }, query: { limit: ROW_LIMIT } } }),
    [ticker],
  );
  const resource = useApiResource(fetcher);
  return (
    <ResourceShell state={resource.state} label="Insider activity">
      {(data, lastGood) => <InsiderBody data={data} lastGood={lastGood} />}
    </ResourceShell>
  );
}

const GROUP_TONE: Record<InsiderGroup, string> = {
  discretionary: "bg-accent",
  compensation: "bg-chart-benchmark",
  non_market: "bg-categorical-6",
  other: "bg-categorical-8",
};

function InsiderBody({ data, lastGood }: { data: InsiderActivityOut; lastGood: boolean }) {
  const nd = data.net_discretionary;
  const { totals, superseded } = useMemo(() => classTotals(data.transactions), [data.transactions]);
  const hasRows = data.transactions.length > 0;
  const net = toNum(nd.net_shares);
  const bought = toNum(nd.bought_shares);
  const sold = toNum(nd.sold_shares);

  const provenance = insiderProvenance(data, { lastGood, rowLimit: ROW_LIMIT });

  return (
    <>
      <ChartFrame
        id="ins-net"
        kicker="Forms 3/4/5"
        title="Net discretionary insider activity"
        takeaway={
          hasRows
            ? `${nd.buy_rows} open-market buys (${compact(bought)} shares) and ${nd.sell_rows} sales (${compact(sold)} shares) from ${date(nd.since)} to ${date(nd.until)}: net ${signedInt(net)} shares. Grants, option exercises, gifts and tax withholding are not in this figure.`
            : undefined
        }
        units={`Shares, ${date(nd.since)} to ${date(nd.until)}. Net value ${netValueText(nd, compact(toNum(nd.net_value)))}. Newest stored filing ${date(provenance.knownAt)}.`}
        provenance={provenance}
        hasValue={hasRows}
        table={{
          caption: "Insider activity by economic class",
          columns: ["Economic class", "Counts toward net", "Rows", "Shares"],
          rows: totals.map((t) => [
            t.label,
            `${countsTowardNetText(t)}${exclusionText(t) ? ` (excluded: ${exclusionText(t)})` : ""}`,
            String(t.rows),
            t.shares === null ? "no share count" : `${compact(t.shares)}${t.derivativeLegRows > 0 ? ` (${t.derivativeLegRows} derivative legs not added)` : ""}`,
          ]),
        }}
      >
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Figure label="Bought (open market)" value={compact(bought)} note={`${nd.buy_rows} rows`} />
          <Figure label="Sold (open market)" value={compact(sold)} note={`${nd.sell_rows} rows`} />
          <Figure label="Net shares" value={signedInt(net)} note={netValueNote(nd)} />
        </div>
        <ul aria-label="Insider activity by economic class" className="m-0 flex list-none flex-col gap-1.5 p-0">
          {totals.map((t) => {
            const max = Math.max(...totals.map((x) => Math.abs(x.shares ?? 0)), 1);
            const share = t.shares === null ? 0 : Math.abs(t.shares) / max;
            return (
              <li key={t.economicClass} className="grid grid-cols-[minmax(0,12rem)_1fr_7rem] items-center gap-2.5 text-xs sm:grid-cols-[14rem_1fr_9rem]">
                <span className="truncate" title={t.label}>
                  {t.label}
                </span>
                <span aria-hidden="true" className="relative h-3 bg-border-soft">
                  <span className={`absolute inset-y-0 left-0 ${GROUP_TONE[t.group]}`} style={{ width: `${share * 100}%` }} />
                </span>
                <span className="num text-right">
                  {t.shares === null ? "no count" : compact(t.shares)} <span className="text-text-muted">· {t.rows}</span>
                  {t.group === "discretionary" && t.countedRows < t.rows ? (
                    <span className="block text-[11px] text-text-muted">
                      {t.countedRows} counted{exclusionText(t) ? `, ${exclusionText(t)}` : ""}
                    </span>
                  ) : null}
                </span>
              </li>
            );
          })}
        </ul>
        <Legend
          items={(Object.keys(INSIDER_GROUP_LABEL) as InsiderGroup[]).map((g) => ({ label: INSIDER_GROUP_LABEL[g], swatch: "square" as const, className: GROUP_TONE[g] }))}
        />
        {superseded > 0 ? <p className="text-xs text-text-muted">{superseded} rows superseded by an amended filing are left out of the class table.</p> : null}
        {nd.plan_status_unknown_rows + nd.plan_possible_rows > 0 ? (
          <p className="text-xs text-text-muted">
            Rule 10b5-1: {nd.plan_possible_rows} counted rows are in filings that tick the plan box (the form does not say which transaction), {nd.plan_status_unknown_rows} do not state it.
            Pre-planned: {compact(toNum(nd.pre_planned_bought_shares))} bought, {compact(toNum(nd.pre_planned_sold_shares))} sold.
          </p>
        ) : null}
      </ChartFrame>

      {nd.exclusions.length > 0 || nd.by_insider.length > 0 ? (
        <Panel id="ins-detail" kicker="Behind the net figure" title="Who and what was excluded">
          {nd.by_insider.length > 0 ? (
            <DataTable
              caption="Net discretionary shares by insider"
              minWidth="40rem"
              columns={[{ label: "Insider" }, { label: "Role" }, { label: "Ownership" }, { label: "Bought", align: "right" }, { label: "Sold", align: "right" }, { label: "Net", align: "right" }]}
              rows={nd.by_insider.map((b) => [b.owner_name ?? b.reporting_owner_cik, b.insider_role, b.ownership, compact(toNum(b.bought_shares)), compact(toNum(b.sold_shares)), signedInt(toNum(b.net_shares))])}
            />
          ) : null}
          {nd.exclusions.length > 0 ? (
            <DataTable
              caption="Rows excluded from the net figure"
              minWidth="30rem"
              columns={[{ label: "Reason" }, { label: "Rows", align: "right" }, { label: "Shares", align: "right" }]}
              rows={nd.exclusions.map((e) => [e.reason, String(e.rows), compact(toNum(e.shares))])}
            />
          ) : null}
        </Panel>
      ) : null}

      <Panel id="ins-rows" kicker="Every row, with its basis" title="Classified transactions" description="The raw transaction code is shown only as reported; the economic class and the reason for it are what to read.">
        {!hasRows ? (
          <p className="text-sm text-text-muted">No transactions in the window.</p>
        ) : (
          <DataTable
            caption="Classified insider transactions, newest first"
            minWidth="62rem"
            columns={[
              { label: "Filed / traded" },
              { label: "Insider" },
              { label: "Class" },
              { label: "Shares", align: "right" },
              { label: "Price", align: "right" },
              { label: "Held" },
              { label: "Basis and notes" },
            ]}
            rows={data.transactions.map((t) => [
              <span key="d">
                {date(t.filed)}
                <span className="block text-[11px] text-text-muted">{t.transaction_date ? `traded ${date(t.transaction_date)}` : "trade date not stated"} · {t.form}</span>
              </span>,
              <span key="i">
                {t.owner_name ?? t.reporting_owner_cik}
                <span className="block text-[11px] text-text-muted">{t.insider_role}</span>
              </span>,
              <span key="c" className="text-[11px]">
                <span className="font-medium text-text">{classMeta(t.economic_class).label}</span>
                <span className="block text-text-muted">code {t.raw_transaction_code}{t.derivative ? " · derivative" : ""}</span>
              </span>,
              t.shares === null ? "—" : compact(toNum(t.shares)),
              toNum(t.price_per_share) === null ? "—" : num(toNum(t.price_per_share), 2),
              t.ownership,
              <span key="n" className="text-[11px] text-text-muted">
                {t.classification_basis ?? t.interpretation}
                {t.superseded_by ? <span className="block text-text font-medium">superseded by {t.superseded_by}</span> : null}
                {!t.counts_toward_net_discretionary && t.exclusion_reason ? <span className="block">excluded: {t.exclusion_reason}</span> : null}
                {t.rule_10b5_1_plan === true ? <span className="block">Rule 10b5-1 plan</span> : null}
                {t.footnote_text ? <span className="block">{t.footnote_text}</span> : null}
                <SafeLink href={t.source_url} className="text-accent-ink hover:underline">
                  filing
                </SafeLink>
              </span>,
            ])}
          />
        )}
        <div className="flex flex-wrap items-center gap-4">
          <JobTrigger job="insider-trades" />
        </div>
        <Limitations items={data.limitations} />
        {data.jurisdiction_coverage.length > 0 ? (
          <ul className="space-y-1 text-xs text-text-muted" aria-label="Jurisdiction coverage">
            {data.jurisdiction_coverage.map((j) => (
              <li key={j.jurisdiction}>
                <span className="font-medium text-text">{j.jurisdiction}</span> via {j.source}: {j.access}
                {j.detail ? ` — ${j.detail}` : ""}
              </li>
            ))}
          </ul>
        ) : null}
      </Panel>
    </>
  );
}

function Figure({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="border-l-2 border-border pl-3">
      <div className="kicker">{label}</div>
      <div className="num text-[22px] leading-tight font-semibold">{value}</div>
      <div className="text-xs text-text-muted">{note}</div>
    </div>
  );
}

// ---- UK PDMR -------------------------------------------------------------------------------------

function PdmrBlock({ ticker }: { ticker: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/pdmr-notifications", { params: { path: { ticker }, query: { limit: 100 } } }),
    [ticker],
  );
  const resource = useApiResource(fetcher);
  return (
    <ResourceShell state={resource.state} label="UK PDMR notifications">
      {(data, lastGood) => <PdmrBody data={data} lastGood={lastGood} />}
    </ResourceShell>
  );
}

function PdmrBody({ data, lastGood }: { data: HoldingPdmrOut; lastGood: boolean }) {
  const holding = pdmrHoldingState(data);
  const state = holding.state;
  const dates = data.notifications.map((n) => n.announced_at).filter((d): d is string => !!d).sort();
  const provenance: DataProvenance = {
    source: "UK MAR Article 19 PDMR notifications from the RNS stream",
    asOf: dates.length ? dates[dates.length - 1].slice(0, 10) : null,
    method: "Notifications are parsed from announcement text; one that cannot be read is listed with why, never dropped.",
    state: lastGood ? "unavailable" : state,
    reason: lastGood ? "The refresh failed; this is the last value that loaded." : holding.reason,
    coverage: holding.total > 0 ? { covered: holding.parsed, total: holding.total, unit: "of this holding's announcements parsed" } : null,
  };
  return (
    <Panel
      id="ins-pdmr"
      kicker="United Kingdom"
      title="PDMR notifications"
      description="Persons discharging managerial responsibilities report dealings under MAR Article 19. A different regime from Form 4: do not read the two as one series."
    >
      {data.notifications.length === 0 ? (
        <p className="text-sm text-text-muted">
          {data.status.state === "available" ? "No PDMR announcement is stored for this holding." : "No notification is available: see the channel status below."}
        </p>
      ) : (
        <DataTable
          caption="UK PDMR notifications"
          minWidth="60rem"
          columns={[{ label: "Announced" }, { label: "Person and role" }, { label: "Status" }, { label: "Transactions" }]}
          rows={data.notifications.map((n) => [
            <span key="a">
              {date(n.announced_at)}
              <SafeLink href={n.source_url} className="block text-[11px] text-accent-ink hover:underline">
                {n.headline ?? "announcement"}
              </SafeLink>
            </span>,
            <span key="p">
              {n.person_name ?? "not parsed"}
              <span className="block text-[11px] text-text-muted">{n.position ?? "role not parsed"}{n.is_amendment ? " · amendment" : ""}</span>
            </span>,
            <span key="s" className="text-[11px]">
              {PDMR_STATUS_LABEL[n.status] ?? n.status}
              {n.detail ? <span className="block text-text-muted">{n.detail}</span> : null}
              {n.attempts > 1 ? <span className="block text-text-muted">{n.attempts} attempts</span> : null}
            </span>,
            n.transactions.length === 0 ? (
              <span key="t" className="text-text-muted">
                none parsed
              </span>
            ) : (
              <ul key="t" className="space-y-0.5 text-[11px]">
                {n.transactions.map((t, i) => (
                  <li key={i}>
                    <span className="font-medium">{classMeta(t.economic_class).label}</span> · {t.nature ?? "nature not stated"} · {compact(toNum(t.volume))} @{" "}
                    {toNum(t.price) === null ? "no price" : `${num(toNum(t.price), 2)} ${t.currency ?? ""}`} · {date(t.transaction_date)}
                  </li>
                ))}
              </ul>
            ),
          ])}
        />
      )}
      <div className="flex flex-wrap items-center gap-2 text-xs text-text-muted">
        <DataStateBadge state={state} reason={holding.reason ?? undefined} />
        <span className="num">
          This holding: {holding.parsed} parsed of {holding.total} announcements{data.ticker_status && Object.keys(data.ticker_status.by_status).length > 0 ? ` (${Object.entries(data.ticker_status.by_status).map(([k, v]) => `${PDMR_STATUS_LABEL[k] ?? k} ${v}`).join(", ")})` : ""}. Channel-wide status, all issuers: {data.status.parsed} parsed of {data.status.attempted} attempted
          {Object.keys(data.status.by_status).length > 0 ? ` (${Object.entries(data.status.by_status).map(([k, v]) => `${PDMR_STATUS_LABEL[k] ?? k} ${v}`).join(", ")})` : ""}
        </span>
      </div>
      <Limitations items={data.limitations} />
      <ProvenanceFooter provenance={provenance} />
    </Panel>
  );
}
