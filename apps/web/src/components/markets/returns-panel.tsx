import type * as React from "react";

import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { DataStateBadge, ProvenanceFooter } from "@/components/data-state";
import { formatPct } from "@/lib/charts/format";
import { formatDay } from "@/lib/charts/time";
import type { ReturnFigure, ReturnsReport } from "@/lib/markets/types";
import type { DataState } from "@/lib/data-state/states";

function Row({ label, basis, fig, note }: { label: string; basis: string; fig: ReturnFigure; note?: string }) {
  return (
    <tr className="border-b border-border-soft last:border-0 align-top">
      <th scope="row" className="py-2 pr-3 text-left font-normal">
        <span className="font-medium">{label}</span>
        <span className="block text-xs text-text-muted">{basis}</span>
      </th>
      <td className="num py-2 pr-3 text-right text-lg">{fig.value === null ? "unavailable" : formatPct(fig.value, 2, true)}</td>
      <td className="py-2 text-xs text-text-muted">{fig.value === null ? (fig.reason ?? "No reason given by the backend.") : (note ?? "")}</td>
    </tr>
  );
}

/**
 * Raw price return, adjusted price return and reinvested total return of the served series over
 * one window, side by side: they are different bases and are never merged. Total return is shown
 * only when the backend computed it; otherwise the backend's reason stands in its place while
 * the price returns stay.
 */
export function ReturnsPanel({ report, windowControl }: { report: ReturnsReport; windowControl?: React.ReactNode }) {
  const r = report.result;
  const state: DataState = !r ? "not_covered" : r.coverage !== null && r.coverage < 1 ? "partial" : r.actionCoverage === "incomplete" ? "partial" : "verified";
  return (
    <Blueprint as="section" aria-labelledby="returns-heading" className="flex flex-col gap-3 px-5 py-5">
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
        <PanelHeading kicker="Returns" title={<span id="returns-heading">Raw, adjusted and total return</span>} />
        {windowControl}
      </div>
      {!r ? (
        <p role="status" className="border border-dashed border-border px-3 py-3 text-sm text-text-muted">
          <DataStateBadge state="not_covered" /> <span className="ml-2">{report.reason}</span>
        </p>
      ) : (
        <>
          <p className="m-0 max-w-prose text-sm">
            {formatDay(r.start)} to {formatDay(r.end)} on {r.source} {r.symbol}
            {r.currency ? `, quote ${r.currency}` : ""}: {r.observedSessions ?? "?"} of {r.expectedSessions ?? "?"} expected sessions observed ({r.coverage === null ? "coverage unknown" : formatPct(r.coverage, 1)}).
          </p>
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[26rem] text-sm">
              <caption className="sr-only">Window returns by basis</caption>
              <thead>
                <tr className="border-b border-border text-[11px] tracking-[0.08em] text-text-muted uppercase">
                  <th scope="col" className="py-1.5 pr-3 text-left font-medium">Basis</th>
                  <th scope="col" className="py-1.5 pr-3 text-right font-medium">Return</th>
                  <th scope="col" className="py-1.5 text-left font-medium">Note</th>
                </tr>
              </thead>
              <tbody>
                <Row label="Raw price return" basis="Closes as stored, no action applied" fig={r.rawPriceReturn} note="A split in the window shows as a fall here." />
                <Row label="Adjusted price return" basis="Adjusted closes (served series)" fig={r.priceReturn} />
                <Row label="Total return" basis="Raw closes + confirmed actions, dividends reinvested" fig={r.totalReturn} />
              </tbody>
            </table>
          </div>
          {r.missingActions.totalReturn.length + r.missingActions.priceAdjusted.length + r.missingActions.priceRaw.length > 0 ? (
            <ul className="m-0 flex list-none flex-col gap-1 border-l-[3px] border-l-warning-signal bg-warning-signal-subtle p-0 px-3 py-1.5 text-sm">
              {[...new Set([...r.missingActions.priceRaw, ...r.missingActions.priceAdjusted, ...r.missingActions.totalReturn])].map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          ) : null}
          <p className="m-0 text-xs text-text-muted">
            Corporate-action coverage: {r.actionCoverage === "available" ? "verified by the owner through the window end" : "not verified through the window end, so total return is withheld"}. Calendar basis: {r.calendarBasis ?? "not reported"}.
          </p>
        </>
      )}
      <ProvenanceFooter
        provenance={{
          source: r ? `${r.source} · ${r.symbol}` : "Price series",
          asOf: report.knownAsOf,
          method: "Computed on request from stored bars and confirmed actions; never written by this view",
          state,
          reason: r ? null : report.reason,
          coverage: r && r.expectedSessions !== null && r.observedSessions !== null ? { covered: r.observedSessions, total: r.expectedSessions, unit: "expected sessions" } : null,
        }}
      />
    </Blueprint>
  );
}
