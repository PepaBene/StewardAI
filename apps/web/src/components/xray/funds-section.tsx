"use client";

import { useCallback, useMemo, useState } from "react";

import { ChartFrame, DataStateBadge, Segmented } from "@/components/data-state";
import { getBrowserApiClient } from "@/lib/api/client";
import type { DataProvenance } from "@/lib/data-state/provenance";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { AUDIT_VERDICT_META, auditCounts, buildOverlapMatrix, lagText, latestAuditByFund, latestFactsheetVersions, rulesText } from "@/lib/xray/funds";
import { date, num, pct, weightPct } from "@/lib/xray/format";
import { parseAudit, parseFactsheet } from "@/lib/xray/parse";
import type { AuditRow, FactsheetRow, FundOverlapReport } from "@/lib/xray/types";

import { DataTable } from "./facts";
import { Panel, ResourceShell } from "./resource-shell";

export function FundsSection({ asOf }: { asOf: string }) {
  const overlapFetcher = useCallback(
    () => getBrowserApiClient().GET("/funds/overlap", { params: { query: asOf ? { as_of: asOf } : {} } }),
    [asOf],
  );
  const overlap = useApiResource(overlapFetcher);
  return (
    <div className="flex flex-col gap-5">
      <ResourceShell state={overlap.state} label="Fund overlap">
        {(report, lastGood) => (
          <>
            <OverlapBlock report={report} lastGood={lastGood} />
            <FactsheetBlock funds={report.funds.map((f) => ({ isin: f.isin, label: f.label }))} />
          </>
        )}
      </ResourceShell>
      <AuditBlock />
    </div>
  );
}

function overlapProvenance(report: FundOverlapReport, lastGood: boolean): DataProvenance {
  const dates = report.funds.map((f) => f.as_of).sort();
  const stale = report.funds.filter((f) => f.stale).length;
  const total = report.funds.length;
  return {
    source: "Stored issuer fund-holdings snapshots (iShares, Vanguard)",
    asOf: dates[0] ?? null,
    method: "Pairwise overlap = sum over shared canonical issuers of the smaller fund weight, equity rows only.",
    state: lastGood ? "unavailable" : total === 0 ? "not_covered" : stale > 0 ? "stale" : "verified",
    reason: lastGood
      ? "The refresh failed; this is the last value that loaded."
      : total === 0
        ? "No fund has a stored holdings snapshot."
        : stale > 0
          ? `${stale} of ${total} funds have stale holdings.`
          : null,
    coverage: total > 0 ? { covered: total - stale, total, unit: "funds with current holdings" } : null,
  };
}

function OverlapBlock({ report, lastGood }: { report: FundOverlapReport; lastGood: boolean }) {
  const m = useMemo(() => buildOverlapMatrix(report), [report]);
  const provenance = overlapProvenance(report, lastGood);
  const takeaway = m.max
    ? `The largest pair overlap is ${m.labels[m.max.fund_a] ?? m.max.fund_a} and ${m.labels[m.max.fund_b] ?? m.max.fund_b} at ${weightPct(m.max.overlap)} of fund weight across ${m.max.shared_issuers} shared issuers.`
    : report.funds.length > 1
      ? "No pair of funds shares an issuer."
      : "Overlap needs at least two funds with stored holdings.";
  return (
    <>
      <ChartFrame
        id="funds-overlap"
        kicker="Fund overlap"
        title="Pairwise overlap between funds"
        takeaway={takeaway}
        units="Overlap in % of fund weight (a pair's smaller weight per shared issuer, summed). Funds are compared on their own holdings dates."
        provenance={provenance}
        hasValue={report.funds.length > 0}
      >
        {report.funds.length > 1 ? (
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[28rem] border-collapse text-xs">
              <caption className="sr-only">Pairwise fund overlap, % of fund weight</caption>
              <thead>
                <tr>
                  <td />
                  {m.isins.map((isin) => (
                    <th key={isin} scope="col" className="px-2 py-1.5 text-left font-medium text-text-muted">
                      {m.labels[isin]}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {m.isins.map((a) => (
                  <tr key={a}>
                    <th scope="row" className="px-2 py-1.5 text-left font-medium">
                      {m.labels[a]}
                    </th>
                    {m.isins.map((b) => {
                      const p = m.cell(a, b);
                      return (
                        <td key={b} className="num border border-border-soft px-2 py-1.5 text-right" title={p ? `${p.shared_issuers} shared issuers; as of ${p.as_of_a} and ${p.as_of_b}` : undefined}>
                          {a === b ? <span className="text-text-faint">—</span> : p ? weightPct(p.overlap) : <span className="text-text-muted">no data</span>}
                          {p && !p.same_date ? <span className="block text-[10px] text-warning-signal">different dates</span> : null}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-text-muted">Only one fund has stored holdings, so there is no pair to compare.</p>
        )}
        {m.mixedDates ? (
          <p role="status" className="text-xs text-warning-signal">
            Some pairs compare holdings from different dates; read those cells as approximate.
          </p>
        ) : null}
        {report.notes.map((n, i) => (
          <p key={i} className="text-xs text-text-muted">
            {n}
          </p>
        ))}
      </ChartFrame>

      <Panel
        id="funds-concentration"
        kicker="Concentration and coverage"
        title="Each fund's own concentration and unknown weight"
        description="HHI is the sum of squared issuer weights, effective N is 1/HHI, top 10 is the share of the ten largest issuers. Unknown weight is what the published rows do not account for."
      >
        <DataTable
          caption="Per-fund concentration, coverage and reporting lag"
          minWidth="58rem"
          columns={[
            { label: "Fund" },
            { label: "Holdings as of", align: "right" },
            { label: "Equity", align: "right" },
            { label: "Cash, derivatives, other", align: "right" },
            { label: "Unknown", align: "right" },
            { label: "Equity rows without ISIN", align: "right" },
            { label: "Issuers", align: "right" },
            { label: "Effective N", align: "right" },
            { label: "Top 10", align: "right" },
          ]}
          rows={report.funds.map((f) => [
            f.label,
            <span key="d">
              {date(f.as_of)}
              <span className={f.stale ? "block text-[11px] text-warning-signal" : "block text-[11px] text-text-muted"}>
                {lagText(f.lag_days)}
                {f.stale ? " · stale" : ""}
              </span>
            </span>,
            pct(f.equity_weight),
            pct(f.cash_derivative_other_weight),
            pct(f.unknown_weight),
            pct(f.unidentified_equity_weight),
            String(f.issuers),
            f.effective_n === null ? "—" : num(f.effective_n, 0),
            f.top10_share === null ? "—" : pct(f.top10_share),
          ])}
        />
      </Panel>
    </>
  );
}

function FactsheetBlock({ funds }: { funds: { isin: string; label: string }[] }) {
  const [isin, setIsin] = useState(funds[0]?.isin ?? "");
  const selected = funds.some((f) => f.isin === isin) ? isin : (funds[0]?.isin ?? "");
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/funds/{isin}/factsheet", { params: { path: { isin: selected }, query: { limit: 24 } } }),
    [selected],
  );
  const resource = useApiResource(fetcher);
  if (funds.length === 0) return null;
  return (
    <Panel
      id="funds-factsheet"
      kicker="Factsheet history"
      title="Fund characteristics and fees"
      description="Charges, benchmark, distribution policy and NAV basis per stored version. A missing figure stays missing: an unknown expense ratio is never treated as zero."
      action={
        funds.length > 1 ? (
          <Segmented label="Fund" options={funds.map((f) => ({ id: f.isin, label: f.label }))} value={selected} onChange={setIsin} />
        ) : null
      }
    >
      <ResourceShell state={resource.state} label="Factsheet history">
        {(body) => <FactsheetTable parsed={parseFactsheet(body)} />}
      </ResourceShell>
    </Panel>
  );
}

function FactsheetTable({ parsed }: { parsed: ReturnType<typeof parseFactsheet> }) {
  if (parsed === null) {
    return <p role="status" className="text-sm text-text-muted">The factsheet response was not in the expected shape, so nothing is shown (not even zeros).</p>;
  }
  const rows: FactsheetRow[] = latestFactsheetVersions(parsed.rows);
  if (rows.length === 0) return <p className="text-sm text-text-muted">No factsheet has been stored for this fund yet.</p>;
  const unverified = rows.some((r) => !r.fieldNamesVerified);
  return (
    <>
      {parsed.rejected > 0 ? <p className="text-xs text-warning-signal">{parsed.rejected} rows were not in the expected shape and are left out.</p> : null}
      {unverified ? (
        <p className="text-xs text-text-muted">The issuer payload field names for some rows are unverified candidates; the row says so.</p>
      ) : null}
      <DataTable
        caption="Factsheet history"
        minWidth="60rem"
        columns={[
          { label: "As of" },
          { label: "Source" },
          { label: "Expense ratio", align: "right" },
          { label: "Total assets", align: "right" },
          { label: "Benchmark" },
          { label: "Distribution" },
          { label: "Rows reconcile" },
          { label: "Remainder", align: "right" },
        ]}
        rows={rows.map((r) => [
          date(r.asOf),
          <span key="s" className="text-[11px]">
            {r.source}
            {!r.fieldNamesVerified ? <span className="block text-warning-signal">field names unverified</span> : null}
            {r.extractionWarnings.map((w, i) => (
              <span key={i} className="block text-text-muted">
                {w}
              </span>
            ))}
          </span>,
          r.expenseRatio === null ? "unknown" : pct(r.expenseRatio, 2),
          r.totalAssets === null ? "unknown" : `${num(r.totalAssets / 1e9, 2)} bn ${r.totalAssetsCurrency ?? ""}`,
          r.benchmarkName ?? "—",
          r.distributionPolicy ?? "—",
          r.reconciles === null ? "unknown" : r.reconciles ? "yes" : "no",
          <span key="m" className="text-[11px]">
            cash {pct(r.cashWeight)} · deriv. {pct(r.derivativeWeight)} · other {pct(r.otherWeight)} · unknown {pct(r.residualWeight)}
          </span>,
        ])}
      />
    </>
  );
}

function AuditBlock() {
  const [alertsOnly, setAlertsOnly] = useState<"all" | "alerts">("all");
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/funds/characteristics-audit", { params: { query: { alerts_only: alertsOnly === "alerts", limit: 100 } } }),
    [alertsOnly],
  );
  const resource = useApiResource(fetcher);
  return (
    <Panel
      id="funds-audit"
      kicker="Fund characteristics audit"
      title="Real changes versus parser drift"
      description="The issuers' fund-level P/E and P/B come from undocumented pages. Each observation is compared with the last one: a changed page shape is parser drift (the parser is suspect, no value alert); a changed figure or changed capping/exclusion rule is a real change and alerts. Fund-level figures are not compared with constituent-weighted metrics unless the definitions match."
      action={
        <Segmented
          label="Show"
          options={[
            { id: "all", label: "All observations" },
            { id: "alerts", label: "Alerts only" },
          ]}
          value={alertsOnly}
          onChange={setAlertsOnly}
        />
      }
    >
      <ResourceShell state={resource.state} label="Characteristics audit">
        {(body) => <AuditTable parsed={parseAudit(body)} />}
      </ResourceShell>
    </Panel>
  );
}

function AuditTable({ parsed }: { parsed: ReturnType<typeof parseAudit> }) {
  if (parsed === null) {
    return <p role="status" className="text-sm text-text-muted">The audit response was not in the expected shape, so nothing is shown.</p>;
  }
  if (parsed.rows.length === 0) {
    return <p className="text-sm text-text-muted">No audit observation is stored yet (the characteristics audit job has not run, or no alert matches).</p>;
  }
  const counts = auditCounts(parsed.rows);
  const latest = latestAuditByFund(parsed.rows);
  const row = (r: AuditRow) => [
    <span key="f" className="font-medium">
      {r.fundKey}
      {r.provider ? <span className="block text-[11px] font-normal text-text-muted">{r.provider}</span> : null}
    </span>,
    date(r.asOf),
    <span key="v" className="inline-flex flex-col gap-0.5">
      <DataStateBadge state={AUDIT_VERDICT_META[r.verdict].state} reason={AUDIT_VERDICT_META[r.verdict].description} />
      <span className="text-[11px]">
        {AUDIT_VERDICT_META[r.verdict].label}
        {r.alert ? " · alert" : ""}
        {r.needsParserReview ? " · needs parser review" : ""}
      </span>
    </span>,
    <span key="p" className="text-[11px]">
      P/E {num(r.pe)} · P/B {num(r.pb)}
      <span className="block text-text-muted">
        index P/E {num(r.indexPe)} · P/B {num(r.indexPb)}
      </span>
    </span>,
    <span key="r" className="text-[11px] text-text-muted">
      {r.basis ? `${r.basis}. ` : "Basis not stated. "}
      {rulesText(r.rules)}
      {r.changes.length > 0 ? (
        <span className="block text-text">
          {r.changes.map((c) => `${c.metric}: ${c.from === null ? "—" : num(c.from, 2)} → ${c.to === null ? "removed" : num(c.to, 2)}`).join("; ")}
        </span>
      ) : null}
    </span>,
    date(r.observedAt),
  ];
  return (
    <>
      <p className="num text-sm">
        <span className="font-medium">{counts.alerts}</span> alerts and <span className="font-medium">{counts.drift}</span> parser-drift observations in the {parsed.rows.length} rows shown across {latest.length} funds.
      </p>
      {parsed.rejected > 0 ? <p className="text-xs text-warning-signal">{parsed.rejected} rows were not in the expected shape and are left out.</p> : null}
      <DataTable
        caption="Fund characteristics audit observations, newest first"
        minWidth="56rem"
        columns={[
          { label: "Fund" },
          { label: "As of" },
          { label: "Verdict" },
          { label: "Fund figures (issuer basis)" },
          { label: "Stated rules and changes" },
          { label: "Observed" },
        ]}
        rows={parsed.rows.map(row)}
      />
    </>
  );
}
