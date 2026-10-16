"use client";

import { useCallback, useMemo, type ReactNode } from "react";

import { BarChart, ChartEmpty, HBar, SrTable, StackedBar } from "@/components/charts";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatCompact, formatNumber, formatPct } from "@/lib/charts/format";
import { formatDate } from "@/lib/format";
import { useApiResource, type ResourceState } from "@/lib/hooks/use-api-resource";
import {
  concentrationSides,
  controlStakeRows,
  fundPositionRows,
  insiderMonthly,
  overlapRows,
  parseDiagnostics,
  type StakeRow,
} from "@/lib/reference/holding-charts";
import { parseSessions } from "@/lib/reference/price-coverage";
import { SessionStrip } from "./holding-coverage";
import { InsiderTimeline } from "./insider-timeline";

function dataOf<T>(state: ResourceState<T>): T | null {
  return state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
}

/** The frame, heading and loading / error line every panel here shares. */
function PanelShell({
  id,
  kicker,
  title,
  state,
  hasData,
  unavailable,
  children,
}: {
  id: string;
  kicker: string;
  title: string;
  state: ResourceState<unknown>;
  hasData: boolean;
  unavailable: string;
  children: ReactNode;
}) {
  return (
    <Blueprint aria-labelledby={id} className="flex min-w-0 flex-col gap-3 px-[22px] py-5">
      <PanelHeading kicker={kicker} title={<span id={id}>{title}</span>} />
      {state.kind === "loading" && !hasData ? <p className="text-sm text-text-muted">Loading…</p> : null}
      {state.kind === "error" && !hasData ? (
        <p className="text-sm text-text-muted">
          {unavailable}: {state.title}
          {state.detail ? `. ${state.detail}` : ""}
        </p>
      ) : null}
      {children}
    </Blueprint>
  );
}

const monthLabel = (m: string) => {
  const [y, mo] = m.split("-");
  return `${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(mo) - 1] ?? mo} ${y.slice(2)}`;
};

/**
 * GET /holdings/{ticker}/insider-activity: net open-market shares bought (up) or sold (down) per
 * month, from the filings the backend counts toward net discretionary activity. Grants, tax
 * withholding and other non-discretionary rows are excluded; quiet months are gaps, not zero.
 */
export function InsiderActivityChart({ ticker }: { ticker: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/insider-activity", { params: { path: { ticker } } }),
    [ticker],
  );
  const { state } = useApiResource(fetcher);
  const raw = dataOf(state);
  const view = useMemo(() => insiderMonthly(raw), [raw]);
  if (state.kind === "loading" || (state.kind === "error" && !raw)) return null;
  const label = "Net insider shares by month";
  return (
    <div className="space-y-1.5">
      <h3 className="text-sm font-semibold">Net discretionary activity by month</h3>
      {view.months.length === 0 ? (
        <ChartEmpty minHeight={72} label="No open-market insider buys or sells in the last 12 months" />
      ) : (
        <>
          <BarChart
            label={`${label}, shares`}
            data={view.months.map((m) => ({ label: monthLabel(m.month), value: m.net }))}
            formatValue={(v) => formatCompact(v)}
            height={150}
          />
          <SrTable
            caption={`${label}, shares (buys minus sells)`}
            columns={["Month", "Net shares", "Buy rows", "Sell rows"]}
            rows={view.months.map((m) => [m.month, m.net === null ? "No counted transactions" : formatNumber(m.net), m.buys, m.sells])}
          />
          <p className="text-xs text-text-muted">
            Unit: shares, open-market buys minus sells; upward bars are net buying. {view.countedRows} counted rows,{" "}
            {view.excludedRows} excluded (grants, withholding, planned or superseded rows). Months without a counted
            transaction have no bar.
          </p>
        </>
      )}
      {raw ? <InsiderTimeline data={raw} /> : null}
    </div>
  );
}

/** HBar rows on one shared, labelled scale (0 to the largest value shown). */
function StakeBars({ rows, unit, denominator }: { rows: StakeRow[]; unit: string; denominator: string }) {
  const max = Math.max(0, ...rows.map((r) => r.percent ?? 0));
  const scaleMax = max > 0 ? max : 1;
  return (
    <div className="space-y-2">
      <ul className="space-y-2">
        {rows.slice(0, 12).map((r) => (
          <li key={r.key} className="grid grid-cols-[minmax(0,12rem)_minmax(0,1fr)_5.5rem] items-center gap-3 text-sm">
            <span className="truncate" title={r.label}>
              {r.label}
              <span className="block text-xs text-text-muted">
                {r.detail}
                {r.asOf ? `, as of ${formatDate(r.asOf)}` : ", date not published"}
              </span>
            </span>
            <HBar value={r.percent} min={0} max={scaleMax} label={`${r.label}: ${unit}`} formatValue={(v) => `${formatNumber(v, 2)}%`} />
            <span className="num text-right">{r.percent === null ? "Not published" : `${formatNumber(r.percent, 2)}%`}</span>
          </li>
        ))}
      </ul>
      <p className="num flex justify-between text-xs text-text-muted">
        <span>Scale 0% to {formatNumber(scaleMax, 2)}%</span>
        <span>{denominator}</span>
      </p>
      {rows.length > 12 ? <p className="text-xs text-text-muted">Showing the largest 12 of {rows.length}.</p> : null}
      <SrTable
        caption={unit}
        columns={["Holder", "Percent", "As of"]}
        rows={rows.map((r) => [r.label, r.percent === null ? "Not published" : `${formatNumber(r.percent, 2)}%`, r.asOf ?? "Not published"])}
      />
    </div>
  );
}

/**
 * GET /holdings/{ticker}/control-stakes (13D/G) and /fund-positions (N-PORT). Two channels with
 * different denominators, drawn apart and never summed.
 */
export function StakesPanel({ ticker }: { ticker: string }) {
  const stakesFetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/control-stakes", { params: { path: { ticker } } }),
    [ticker],
  );
  const fundsFetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/fund-positions", { params: { path: { ticker } } }),
    [ticker],
  );
  const stakes = useApiResource(stakesFetcher);
  const funds = useApiResource(fundsFetcher);
  const stakeData = dataOf(stakes.state);
  const fundData = dataOf(funds.state);
  const stakeRows = useMemo(() => controlStakeRows(stakeData), [stakeData]);
  const fundRows = useMemo(() => fundPositionRows(fundData), [fundData]);
  const loading = stakes.state.kind === "loading" || funds.state.kind === "loading";

  return (
    <Blueprint aria-labelledby="stakes-heading" className="flex min-w-0 flex-col gap-4 px-[22px] py-5">
      <PanelHeading kicker="Ownership · stakes and fund positions" title={<span id="stakes-heading">Large stakes and fund positions</span>} />
      {loading && !stakeData && !fundData ? <p className="text-sm text-text-muted">Loading…</p> : null}
      <section className="space-y-2" aria-label="Beneficial-ownership stakes">
        <h3 className="text-sm font-semibold">Beneficial-ownership stakes (Schedule 13D/G)</h3>
        {stakes.state.kind === "error" && !stakeData ? (
          <p className="text-sm text-text-muted">Stakes unavailable: {stakes.state.title}</p>
        ) : stakeRows.length === 0 && stakeData ? (
          <p className="text-sm text-text-muted">
            No structured 13D/G stake is stored. Filings before December 2024 are not parsed, so this is not evidence of no stake.
          </p>
        ) : stakeRows.length > 0 ? (
          <StakeBars rows={stakeRows} unit="Filer-reported percent of the share class" denominator="Percent of the filer's share class, as reported" />
        ) : null}
      </section>
      <section className="space-y-2" aria-label="Fund positions">
        <h3 className="text-sm font-semibold">Fund positions (N-PORT)</h3>
        {funds.state.kind === "error" && !fundData ? (
          <p className="text-sm text-text-muted">Fund positions unavailable: {funds.state.title}</p>
        ) : fundRows.length === 0 && fundData ? (
          <p className="text-sm text-text-muted">No tracked fund reports a position in this holding. Absence is not evidence a fund does not hold it.</p>
        ) : fundRows.length > 0 ? (
          <StakeBars rows={fundRows} unit="Percent of the fund's net assets" denominator="Percent of each fund's own net assets (denominators differ)" />
        ) : null}
      </section>
      <p className="max-w-[68ch] text-xs text-text-muted">
        Stakes and fund positions are separate channels: do not add them to each other, to 13F holders or to insider holdings.
      </p>
    </Blueprint>
  );
}

/**
 * GET /holdings/{ticker}/customer-supplier-concentration: for the latest period of each side, one
 * 100% bar. The whole bar is the issuer's own revenue (customers) or purchases (suppliers).
 */
export function ConcentrationPanel({ ticker }: { ticker: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/customer-supplier-concentration", { params: { path: { ticker } } }),
    [ticker],
  );
  const { state } = useApiResource(fetcher);
  const raw = dataOf(state);
  const sides = useMemo(() => concentrationSides(raw), [raw]);
  return (
    <PanelShell
      id="concentration-heading"
      kicker="Dependencies · confirmed disclosures"
      title="Customer and supplier concentration"
      state={state}
      hasData={raw !== null}
      unavailable="Concentration unavailable"
    >
      {raw && sides.length === 0 ? (
        <p className="max-w-[68ch] text-sm text-text-muted">
          No confirmed, dated customer or supplier share is stored, so concentration is unknown. It is never inferred.
        </p>
      ) : null}
      <div className="grid gap-5 md:grid-cols-2">
        {sides.map((s) => (
          <div key={s.side} className="space-y-1.5">
            <StackedBar
              title={`${s.side === "customer" ? "Customers" : "Suppliers"} · ${s.period}`}
              note={`Share of ${s.denominator}`}
              segments={s.segments}
              formatShare={(f) => formatPct(f, 0)}
            />
            <p className="text-xs text-text-muted">
              {formatPct(Math.min(s.reported, 1), 0)} of {s.denominator} is attributed to a disclosed counterparty
              {s.missingShare > 0 ? `; ${s.missingShare} disclosed counterpart${s.missingShare === 1 ? "y reports" : "ies report"} no share` : ""}.
              {s.exceedsTotal ? " Reported shares add up to more than 100%, so the figures conflict; check the sources." : ""}
              {s.contradictions > 0 ? ` ${s.contradictions} open contradiction${s.contradictions === 1 ? "" : "s"}.` : ""}
            </p>
            <SrTable
              caption={`${s.side} share of ${s.denominator}, ${s.period}`}
              columns={["Segment", "Share"]}
              rows={s.segments.map((x) => [x.label, formatPct(x.value, 1)])}
            />
          </div>
        ))}
      </div>
    </PanelShell>
  );
}

/**
 * GET /holdings/{ticker}/prices/diagnostics: how complete each stored price series is against the
 * exchange's own session calendar, plus a per-session calendar strip. Corporate-action
 * coverage has its own panel (holding-coverage.tsx).
 */
export function PriceDataPanel({ ticker }: { ticker: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/prices/diagnostics", { params: { path: { ticker } } }),
    [ticker],
  );
  const { state } = useApiResource(fetcher);
  const raw = dataOf(state);
  const series = useMemo(() => parseDiagnostics(raw), [raw]);
  return (
    <PanelShell
      id="price-data-heading"
      kicker="Data quality · stored price series"
      title="Price history completeness"
      state={state}
      hasData={raw !== null}
      unavailable="Price diagnostics unavailable"
    >
      {raw && series.length === 0 ? <p className="text-sm text-text-muted">No stored price series for this holding.</p> : null}
      <ul className="space-y-3">
        {series.map((s) => {
          const covered = s.expected > 0 && s.missing !== null ? Math.max(0, s.expected - s.missing) / s.expected : null;
          return (
            <li key={s.key} className="space-y-1">
              <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
                <span>
                  {s.label}
                  {s.chosen ? <span className="ml-2 border border-accent bg-accent-soft px-1.5 py-0.5 text-xs text-accent-ink">Served</span> : null}
                </span>
                <span className="num text-xs text-text-muted">
                  {covered === null ? "Not computable: no exchange calendar" : `${formatPct(covered, 1)} of ${s.expected} expected sessions present`}
                </span>
              </div>
              <HBar value={covered} min={0} max={1} label={`${s.label}: share of expected sessions present`} formatValue={(v) => formatPct(v, 1)} />
              <p className="num text-xs text-text-muted">
                {s.missing === null ? "Missing days unknown" : `${s.missing} missing day${s.missing === 1 ? "" : "s"}`}
                {s.stale === null ? ", staleness unknown" : `, ${s.stale} stale session${s.stale === 1 ? "" : "s"}`}
                {s.lastSession ? `, last session ${formatDate(s.lastSession)}` : ", no last session"}
                {s.basis ? `, calendar: ${s.basis}` : ""}
              </p>
              <SessionStrip cells={parseSessions(s.recentSessions)} mic={s.mic} label={s.label} />
            </li>
          );
        })}
      </ul>
      {series.length > 0 ? <p className="text-xs text-text-muted">Scale 0% to 100% of the sessions the exchange held in the window.</p> : null}
    </PanelShell>
  );
}

/**
 * GET /holdings/{ticker}/patent-competitor-overlap: the share of this holding's patent families
 * that file in at least one technology class a peer also files in. A research signal only, not a
 * freedom-to-operate assessment.
 */
export function PatentOverlapPanel({ ticker }: { ticker: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/patent-competitor-overlap", { params: { path: { ticker } } }),
    [ticker],
  );
  const { state } = useApiResource(fetcher);
  const raw = dataOf(state);
  const rows = useMemo(() => overlapRows(raw), [raw]);
  return (
    <PanelShell
      id="overlap-heading"
      kicker="Innovation · EPO patent families"
      title="Technology overlap with competitors"
      state={state}
      hasData={raw !== null}
      unavailable="Patent overlap unavailable"
    >
      {raw && rows.length === 0 ? (
        <p className="max-w-[68ch] text-sm text-text-muted">No confirmed peers or competitors with patent data, so there is nothing to compare.</p>
      ) : null}
      <ul className="space-y-2">
        {rows.map((r) => (
          <li key={r.peer} className="grid grid-cols-[6rem_minmax(0,1fr)_6rem] items-center gap-3 text-sm">
            <span className="font-mono">{r.peer}</span>
            <HBar value={r.share} min={0} max={1} label={`${r.peer}: share of own patent families overlapping`} formatValue={(v) => formatPct(v, 0)} />
            <span className="num text-right">{r.share === null ? "Not computable" : formatPct(r.share, 0)}</span>
            {r.reason ? <span className="col-span-3 -mt-1 text-xs text-text-muted">{r.reason}</span> : null}
          </li>
        ))}
      </ul>
      {rows.length > 0 ? (
        <>
          <p className="text-xs text-text-muted">
            Scale 0% to 100% of this holding&apos;s {rows[0].ownFamilies} patent families (the denominator), on CPC subclass. Not an infringement or
            freedom-to-operate assessment.
          </p>
          <SrTable
            caption="Share of own patent families overlapping each peer"
            columns={["Peer", "Overlap", "Peer families"]}
            rows={rows.map((r) => [r.peer, r.share === null ? "Not computable" : formatPct(r.share, 0), r.peerFamilies])}
          />
        </>
      ) : null}
    </PanelShell>
  );
}
