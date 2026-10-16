"use client";

import { useCallback, useMemo, useState } from "react";

import { LineChart, StackedBar } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { dataOf, Panel, ResourceNotice } from "@/components/oversight/panel";
import { SELECT_CLASS } from "@/components/reference/form-bits";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatNumber } from "@/lib/charts/format";
import { auditFunds, parseAudit, VERDICTS, verdictCounts, verdictLabel, type AuditEntry } from "@/lib/funds/forms";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";

const fig = (v: number | null) => (v === null ? "Not published" : formatNumber(v, 1));

/**
 * Parser-drift audit of the issuer's fund P/E and P/B. Each row says whether a change in a
 * figure is a real move, a change in the issuer's method, or the reader picking up the wrong
 * number, so a P/E that jumped is not trusted until you know which.
 */
export function CharacteristicsAuditPanel() {
  const [fund, setFund] = useState("");
  const [alertsOnly, setAlertsOnly] = useState(false);
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/funds/characteristics-audit", { params: { query: { alerts_only: alertsOnly, limit: 100 } } }),
    [alertsOnly],
  );
  const { state } = useApiResource(fetcher);
  const raw = dataOf(state);
  const all = useMemo(() => (raw ? parseAudit(raw) : []), [raw]);
  const funds = useMemo(() => auditFunds(all), [all]);
  const rows = useMemo(() => (fund ? all.filter((r) => r.fundKey === fund) : all), [all, fund]);
  const counts = useMemo(() => verdictCounts(rows), [rows]);
  const series = useMemo(() => timeline(rows, "pe"), [rows]);
  const indexSeries = useMemo(() => timeline(rows, "indexPe"), [rows]);

  return (
    <Panel
      id="audit"
      title="Fund P/E and P/B audit"
      description="What changed in the issuer's published P/E and P/B, when we saw it, and whether it looks like a real move or a parser problem. Newest first. Figures the issuer did not publish stay empty."
    >
      <div className="flex flex-wrap items-end gap-4">
        <label className="space-y-1 text-sm">
          <span className="block font-medium">Fund</span>
          <select value={fund} onChange={(e) => setFund(e.target.value)} className={`${SELECT_CLASS} w-56`}>
            <option value="">All funds</option>
            {funds.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 pb-2 text-sm">
          <input type="checkbox" checked={alertsOnly} onChange={(e) => setAlertsOnly(e.target.checked)} className="size-4 accent-accent" />
          Only rows that raised an alert
        </label>
      </div>
      <ResourceNotice state={state} what="the characteristics audit" />
      {raw && all.length === 0 ? (
        <EmptyState title={alertsOnly ? "No alerts raised" : "No audit rows yet"}>
          {alertsOnly ? "No P/E or P/B observation has been flagged." : "The audit job stores a row when a fund's figures first appear or change."}
        </EmptyState>
      ) : null}

      {rows.length > 0 ? (
        <>
          <StackedBar
            title="Observations by verdict"
            note={`${rows.length} observations`}
            segments={counts.map((c) => ({ label: c.label, value: c.count }))}
            formatShare={(f) => String(Math.round(f * rows.length))}
          />
          {fund && series.length >= 2 ? (
            <div>
              <h3 className="mb-1 text-sm font-semibold">Fund P/E at each observation{indexSeries.length >= 2 ? ", dashed: its index" : ""}</h3>
              <LineChart
                data={series}
                benchmark={indexSeries.length >= 2 ? indexSeries : undefined}
                area={false}
                label="Fund P/E"
                benchmarkLabel="Index P/E"
                formatValue={(v) => formatNumber(v, 1)}
                ariaLabel={`P/E of ${fund} at each audit observation`}
              />
            </div>
          ) : null}
          <div className="relative overflow-x-auto border border-border">
            <table className="w-full min-w-[46rem] text-sm">
              <caption className="sr-only">Audit of issuer fund P/E and P/B, newest observation first</caption>
              <thead className="bg-surface-muted">
                <tr className="border-b border-border text-left text-xs text-text-muted">
                  <th scope="col" className="px-3 py-2 font-normal">Seen</th>
                  <th scope="col" className="px-3 py-2 font-normal">Fund</th>
                  <th scope="col" className="px-3 py-2 font-normal">Verdict</th>
                  <th scope="col" className="px-3 py-2 text-right font-normal">P/E</th>
                  <th scope="col" className="px-3 py-2 text-right font-normal">P/B</th>
                  <th scope="col" className="px-3 py-2 text-right font-normal">Index P/E</th>
                  <th scope="col" className="px-3 py-2 text-right font-normal">Index P/B</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.key} className="border-b border-border-soft align-top last:border-0">
                    <td className="num px-3 py-2 whitespace-nowrap">
                      {r.observedAt ? formatDate(r.observedAt) : "Unknown"}
                      {r.asOf ? <div className="text-xs text-text-muted">figures as of {formatDate(r.asOf)}</div> : null}
                    </td>
                    <td className="px-3 py-2">
                      <div className="font-mono text-xs">{r.fundKey}</div>
                      <div className="text-xs text-text-muted">Source: {r.provider ?? "Unknown"}</div>
                    </td>
                    <td className="px-3 py-2">
                      <span className="font-medium">
                        <span aria-hidden="true">{r.alert ? "▲ " : ""}</span>
                        {verdictLabel(r.verdict)}
                      </span>
                      {r.alert ? <div className="text-xs">Alert raised</div> : null}
                      {r.basis ? <div className="max-w-[28ch] text-xs text-text-muted">Stated basis: {r.basis}</div> : null}
                      {r.fingerprint ? <div className="font-mono text-xs text-text-muted">Page shape {r.fingerprint.slice(0, 8)}</div> : null}
                    </td>
                    <td className="num px-3 py-2 text-right">{fig(r.pe)}</td>
                    <td className="num px-3 py-2 text-right">{fig(r.pb)}</td>
                    <td className="num px-3 py-2 text-right">{fig(r.indexPe)}</td>
                    <td className="num px-3 py-2 text-right">{fig(r.indexPb)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      <details className="text-xs text-text-muted">
        <summary className="cursor-pointer text-accent-ink hover:underline">What the verdicts mean</summary>
        <dl className="mt-2 grid gap-x-4 gap-y-1 sm:grid-cols-[11rem_1fr]">
          {VERDICTS.map((v) => (
            <div key={v.id} className="contents">
              <dt className="font-semibold text-text">{v.label}</dt>
              <dd>{v.meaning}</dd>
            </div>
          ))}
        </dl>
      </details>
    </Panel>
  );
}

/** Oldest first, one point per observation date; a figure that was not published is a gap. */
function timeline(rows: readonly AuditEntry[], key: "pe" | "indexPe") {
  return rows
    .filter((r) => r.observedAt)
    .map((r) => ({ date: (r.observedAt as string).slice(0, 10), value: r[key] }))
    .sort((a, b) => a.date.localeCompare(b.date))
    .filter((p, i, a) => i === a.findIndex((q) => q.date === p.date));
}
