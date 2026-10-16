"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ResourceGate } from "@/components/company/primitives";
import { CoverageChip } from "@/components/insight/answer-strip";
import { KpiStrip } from "@/components/insight/kpi-tile";
import { shortTicker } from "@/lib/portfolio/ticker";
import { incomeChangeVerdict } from "@/lib/verdicts/portfolio";
import { ChartWithFooter } from "@/components/insight/chart-footer";
import { ForecastBars } from "@/components/insight/forecast-bars";
import { MonthCalendar } from "@/components/insight/month-calendar";
import { ScoreMark } from "@/components/insight/score-mark";
import { VerdictLine } from "@/components/insight/verdict-line";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDay } from "@/lib/charts/time";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  CALENDAR_KINDS,
  KIND_LABEL,
  money,
  orderedTiers,
  pct,
  TIER_BG,
  TIER_LABEL,
  type DividendCalendar,
  type PortfolioDividends,
} from "@/lib/insight/income";
import { monthBars, num, shiftMonth, yearBars, type ForecastBar } from "@/lib/insight/insight";
import { useMarks } from "@/lib/insight/use-marks";
import { cn } from "@/lib/utils";

const fetchDividends = () => getBrowserApiClient().GET("/portfolio/dividends");

const TH = "px-3 py-1.5 text-right font-medium";
const TH_LEFT = "px-3 py-1.5 text-left font-medium";
const TD = "num px-3 py-1.5 text-right";

export function IncomeView() {
  const { state } = useApiResource(fetchDividends);
  return (
    <ResourceGate state={state} label="dividend income">
      {(data: PortfolioDividends) => <IncomeBody data={data} />}
    </ResourceGate>
  );
}

function IncomeBody({ data }: { data: PortfolioDividends }) {
  const s = data.summary;
  const ccy = s.currency;
  if (data.status === "unavailable" && data.holdings.length === 0) {
    return (
      <AlertMessage tone="info" title="No holdings to project income for">
        Income appears once a portfolio sync has stored positions. {s.coverage_sentence}.
      </AlertMessage>
    );
  }
  const change = num(s.change_vs_trailing);
  const coverageTone = data.status === "complete" ? "neutral" : data.status === "partial" ? "partial" : "unavailable";
  return (
    <div className="flex flex-col gap-5">
      <KpiStrip
        label="Income at a glance"
        tiles={[
          {
            label: "Income, next 12 months",
            value: money(s.next_12m, ccy, 0),
            note: `${money(s.next_12m_declared, ccy, 0)} declared · ${money(s.next_12m_estimated, ccy, 0)} estimated`,
            verdict: incomeChangeVerdict(change),
          },
          { label: "Received, last 12 months", value: money(s.trailing_12m, ccy, 0), note: change !== null ? `Next 12 months ${pct(change, 1, true)}` : undefined },
          { label: "Monthly average", value: money(s.monthly_average, ccy, 0), note: "Over the next 12 months" },
          { label: "Yield on price", value: pct(s.yield_on_price, 2), note: "Next 12 months over today's value" },
          { label: "Yield on cost", value: pct(s.yield_on_cost, 2), note: "Over what you paid for open lots" },
        ]}
      />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-muted">
        <CoverageChip tone={coverageTone}>{s.coverage_sentence}</CoverageChip>
        <span>{data.assume_growth ? "Estimates assume each holding keeps its five-year dividend growth." : "Estimates repeat the last amount; growth is not assumed."}</span>
      </div>
      <Gaps data={data} />
      <IncomeChart data={data} />
      <Contributors data={data} />
      <div className="grid items-start gap-5 lg:grid-cols-2">
        <CalendarPanel today={s.as_of} />
        <Upcoming data={data} />
      </div>
      <Tiers data={data} />
      <Accuracy data={data} />
      <p className="text-xs text-text-faint">
        Method {data.explain.formula_version}. {data.explain.formula}
      </p>
    </div>
  );
}

function Gaps({ data }: { data: PortfolioDividends }) {
  const items: React.ReactNode[] = [];
  if (data.without_schedule.length) {
    const names = data.without_schedule.map((t) => data.holdings.find((h) => h.ticker === t)?.name ?? shortTicker(t));
    items.push(
      <li key="ws">
        No schedule yet, history too short or not confirmed: {names.join(", ")}. Their income is left out, not counted as zero.
      </li>,
    );
  }
  if (data.unconfirmed_events) {
    items.push(
      <li key="uc">
        {data.unconfirmed_events} suggested dividend event{data.unconfirmed_events === 1 ? "" : "s"} await confirmation ({money(data.summary.next_12m_unconfirmed, data.summary.currency)}, not in the totals).{" "}
        <Link href="/inbox" className="text-accent-ink underline underline-offset-2">
          Review in Inbox
        </Link>
      </li>,
    );
  }
  for (const d of data.disagreements) items.push(<li key={d}>{d}</li>);
  if (!items.length) return null;
  return (
    <AlertMessage tone="info" title="What the figures leave out">
      <details>
        <summary className="cursor-pointer">
          {items.length} {items.length === 1 ? "note" : "notes"} on holdings and payments the totals leave out. Show them.
        </summary>
        <ul className="mt-2 list-disc space-y-1 pl-4">{items}</ul>
      </details>
    </AlertMessage>
  );
}

function IncomeChart({ data }: { data: PortfolioDividends }) {
  const [mode, setMode] = useState<"monthly" | "annual">("monthly");
  const ccy = data.summary.currency;
  const bars = useMemo(() => (mode === "monthly" ? monthBars(data.months) : yearBars(data.years)), [mode, data.months, data.years]);
  const byMonth = useMemo(() => new Map(data.months.map((m) => [m.month, m])), [data.months]);
  const fmt = (v: number) => money(v, ccy, 0);
  const payments = data.months.flatMap((m) => m.payments.map((p) => ({ ...p, month: m.month })));
  const historyMonths = data.months.filter((m) => m.phase === "history").length;
  const received = historyMonths === 1 ? "1 month" : `${historyMonths} months`;
  const detail = (bar: ForecastBar) => {
    const month = byMonth.get(bar.key);
    if (!month) return <p className="text-xs text-text-muted">Annual total; switch to Monthly to list payments.</p>;
    if (!month.payments.length) return <p className="text-xs text-text-muted">No payments this month.</p>;
    return (
      <ul className="space-y-1 text-xs">
        {month.payments.map((p, i) => (
          <li key={i} className="flex flex-wrap justify-between gap-2">
            <span>
              <Link href={`/companies/${encodeURIComponent(p.ticker)}/dividends`} className="font-medium hover:underline">
                {p.name ?? p.ticker}
              </Link>{" "}
              <span className="text-text-muted">
                {formatDay(p.date)} · {KIND_LABEL[p.kind]}
              </span>
            </span>
            <span className="num">{money(p.amount, ccy)}</span>
          </li>
        ))}
      </ul>
    );
  };
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading
        kicker={`${ccy ?? "Account currency"} · ${received} since the first dividend, 12 months ahead`}
        title="Dividend income by month"
        action={
          <div role="group" aria-label="Period" className="flex border border-border text-xs">
            {(["monthly", "annual"] as const).map((m) => (
              <button
                key={m}
                type="button"
                aria-pressed={mode === m}
                onClick={() => setMode(m)}
                className={cn("px-2.5 py-1", mode === m ? "bg-accent-soft text-accent-ink" : "text-text-muted hover:text-text")}
              >
                {m === "monthly" ? "Monthly" : "Annual"}
              </button>
            ))}
          </div>
        }
      />
      <ChartWithFooter
        className="mt-3"
        chart={<ForecastBars bars={bars} format={fmt} label="Dividend income by period" detail={detail} />}
        table={
          <div className="max-h-96 overflow-auto border border-border">
            <table className="w-full text-sm">
              <caption className="sr-only">Every payment behind the bars</caption>
              <thead>
                <tr className="sticky top-0 bg-surface-muted text-xs text-text-muted">
                  <th scope="col" className={TH_LEFT}>Month</th>
                  <th scope="col" className={TH_LEFT}>Holding</th>
                  <th scope="col" className={TH_LEFT}>Date</th>
                  <th scope="col" className={TH_LEFT}>Kind</th>
                  <th scope="col" className={TH}>Amount</th>
                </tr>
              </thead>
              <tbody>
                {payments.map((p, i) => (
                  <tr key={i} className="border-t border-border-soft">
                    <td className="px-3 py-1 text-text-muted">{p.month}</td>
                    <th scope="row" className="px-3 py-1 text-left font-normal">{p.name ?? p.ticker}</th>
                    <td className="num px-3 py-1">{formatDay(p.date)}</td>
                    <td className="px-3 py-1">{KIND_LABEL[p.kind]}</td>
                    <td className={TD}>{money(p.amount, ccy)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        }
        dataset="Trading212 dividend receipts, confirmed corporate actions, estimated schedule"
        asOf={formatDay(data.summary.as_of)}
        source="Trading212, Twelve Data, Yahoo"
        learnHref="/methods/dividend_income"
      />
    </Blueprint>
  );
}

/** One ranked list, largest first. The smallest payers are added only when there are more than 8 payers. */
function Contributors({ data }: { data: PortfolioDividends }) {
  const currency = data.summary.currency;
  const payers = data.holdings.filter((h) => h.pays).length;
  const seen = new Set<string>();
  const lines = [...data.largest, ...(payers > 8 ? data.smallest : [])]
    .filter((l) => (seen.has(l.ticker) ? false : (seen.add(l.ticker), true)))
    .sort((a, b) => (num(b.amount) ?? 0) - (num(a.amount) ?? 0));
  const max = Math.max(0, ...lines.map((l) => num(l.amount) ?? 0));
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker="Last 12 months received" title="Who pays the income" level={3} />
      {lines.length === 0 ? (
        <p className="mt-3 text-sm text-text-faint">No dividends received in the last 12 months.</p>
      ) : (
        <ol className="mt-3 space-y-2">
          {lines.map((l) => {
            const a = num(l.amount) ?? 0;
            return (
              <li key={l.ticker} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1">
                <Link href={`/companies/${encodeURIComponent(l.ticker)}/dividends`} className="truncate text-sm hover:underline">
                  {l.name ?? l.ticker}
                </Link>
                <span className="num text-sm">
                  {money(a, currency, 0)} <span className="text-text-muted">{pct(l.share, 0)}</span>
                </span>
                <span className="col-span-2 h-1.5 bg-surface-muted">
                  <span className="block h-full bg-chart-line" style={{ width: `${max > 0 ? (a / max) * 100 : 0}%` }} />
                </span>
              </li>
            );
          })}
        </ol>
      )}
    </Blueprint>
  );
}

function Tiers({ data }: { data: PortfolioDividends }) {
  const ccy = data.summary.currency;
  const tiers = orderedTiers(data.tiers);
  const allPaying = data.holdings.filter((h) => h.pays);
  const [limit, setLimit] = useState(50);
  const paying = allPaying.slice(0, limit);
  const marks = useMarks(paying.map((h) => h.ticker));
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading
        kicker="Dividend checks"
        title="How dependable the income is"
        action={
          <Link href="/discover?template=quality-payers" className="text-sm text-accent-ink underline underline-offset-2">
            Find quality payers
          </Link>
        }
      />
      <p className="mt-1 text-xs text-text-muted">{data.tier_rule}</p>
      <div className="mt-3 grid gap-3 sm:grid-cols-4">
        {tiers.map((t) => (
          <div key={t.tier} className="border-l-2 border-border pl-3">
            <p className="flex items-center gap-1.5 text-xs text-text-muted">
              <span aria-hidden="true" className={cn("size-2", TIER_BG[t.tier])} />
              {TIER_LABEL[t.tier]}
            </p>
            <p className="num text-lg font-semibold">{money(t.income, ccy, 0)}</p>
            <p className="text-xs text-text-faint">
              {t.holdings} holding{t.holdings === 1 ? "" : "s"}, {pct(t.share, 0)} of income
            </p>
          </div>
        ))}
      </div>
      <div className="mt-3 flex h-3 w-full gap-0.5" role="img" aria-label={`Income by tier: ${tiers.map((t) => `${TIER_LABEL[t.tier]} ${pct(t.share, 0)}`).join(", ")}`}>
        {tiers.map((t) => {
          const w = (num(t.share) ?? 0) * 100;
          return w > 0 ? <span key={t.tier} className={cn("h-full", TIER_BG[t.tier])} style={{ width: `${w}%` }} /> : null;
        })}
      </div>
      <div className="mt-4 overflow-x-auto border border-border">
        <table className="w-full text-sm">
          <caption className="sr-only">Paying holdings with their dividend checks</caption>
          <thead>
            <tr className="bg-surface-muted text-xs text-text-muted">
              <th scope="col" className={TH_LEFT}>Holding</th>
              <th scope="col" className={TH_LEFT}>Dividend checks</th>
              <th scope="col" className={TH}>Next 12 months</th>
              <th scope="col" className={cn(TH, "hidden sm:table-cell")}>Yield</th>
              <th scope="col" className={cn(TH, "hidden sm:table-cell")}>Yield on cost</th>
              <th scope="col" className={cn(TH, "hidden md:table-cell")}>5-year growth</th>
              <th scope="col" className={cn(TH_LEFT, "hidden md:table-cell")}>Next payment</th>
            </tr>
          </thead>
          <tbody>
            {paying.map((h) => (
              <tr key={h.ticker} className="border-t border-border-soft">
                <th scope="row" className="px-3 py-1.5 text-left font-normal">
                  <span className="flex items-center gap-2">
                    {marks.petals.get(h.ticker) ? <ScoreMark petals={marks.petals.get(h.ticker)!} label={h.name ?? h.ticker} /> : <span className="inline-block size-6" />}
                    <Link href={`/companies/${encodeURIComponent(h.ticker)}/dividends`} className="hover:underline">
                      {h.name ?? h.ticker}
                    </Link>
                  </span>
                </th>
                <td className="px-3 py-1.5">
                  {h.score ? (
                    <span className="flex items-center gap-1.5">
                      <span aria-hidden="true" className={cn("size-2", TIER_BG[(h.score.tier as keyof typeof TIER_BG | null) ?? "unscored"])} />
                      <span className="num">
                        {h.score.passes} of {h.score.applicable} applicable
                      </span>
                    </span>
                  ) : (
                    <span className="text-text-faint">Not checked yet</span>
                  )}
                </td>
                <td className={TD}>{h.next_12m === null ? <span className="text-text-faint" title={h.reason ?? undefined}>No schedule</span> : money(h.next_12m, data.summary.currency)}</td>
                <td className={cn(TD, "hidden sm:table-cell")}>{pct(h.yield_on_price, 2)}</td>
                <td className={cn(TD, "hidden sm:table-cell")}>{pct(h.yield_on_cost, 2)}</td>
                <td className={cn(TD, "hidden md:table-cell")} title={h.growth_5y_reason ?? undefined}>{pct(h.growth_5y, 1, true)}</td>
                <td className="hidden px-3 py-1.5 md:table-cell">
                  {h.next_payment ? (
                    <span className="num">
                      {formatDay(h.next_payment.date)} <span className="text-text-muted">{KIND_LABEL[h.next_payment.kind]}</span>
                    </span>
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {allPaying.length > limit ? (
        <p className="mt-2 flex items-center justify-between gap-3 text-sm text-text-muted">
          <span>
            Showing {paying.length} of {allPaying.length} paying holdings.
          </span>
          <button type="button" onClick={() => setLimit((n) => n + 50)} className="font-medium text-accent-ink hover:underline">
            Show more
          </button>
        </p>
      ) : null}
    </Blueprint>
  );
}

function CalendarPanel({ today }: { today: string }) {
  const [month, setMonth] = useState(today.slice(0, 7));
  const fetcher = useCallback(() => getBrowserApiClient().GET("/portfolio/dividends/calendar", { params: { query: { month } } }), [month]);
  const { state } = useApiResource(fetcher);
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker="Ex-dividend, payment and earnings dates" title="Calendar" />
      <div className="mt-3">
        <ResourceGate state={state} label="calendar">
          {(cal: DividendCalendar) => (
            <MonthCalendar
              month={cal.month}
              firstDay={cal.first_day}
              today={today}
              onMonth={(d) => setMonth(shiftMonth(month, d))}
              kinds={CALENDAR_KINDS}
              items={cal.events.map((e, i) => ({
                key: `${e.ticker}-${e.kind}-${e.date}-${i}`,
                date: e.date,
                kind: e.kind,
                title: e.ticker.split("_")[0],
                amount: e.amount !== null && e.amount !== undefined ? money(e.amount, cal.currency) : null,
                note: [e.name, e.note].filter(Boolean).join(" · ") || null,
              }))}
              footer={
                <p className="text-xs text-text-muted">
                  Payments this month: <span className="num font-medium text-text">{money(cal.total_payments, cal.currency)}</span>, estimated ones included and marked hollow.
                </p>
              }
            />
          )}
        </ResourceGate>
      </div>
    </Blueprint>
  );
}

function Upcoming({ data }: { data: PortfolioDividends }) {
  const ccy = data.summary.currency;
  const rows = data.months
    .filter((m) => m.phase === "forecast")
    .flatMap((m) => m.payments)
    .sort((a, b) => (a.ex_date ?? a.date).localeCompare(b.ex_date ?? b.date))
    .slice(0, 12);
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker="Next payments" title="Upcoming" level={3} />
      {rows.length === 0 ? (
        <p className="mt-3 text-sm text-text-faint">Nothing declared or estimated yet.</p>
      ) : (
        <div className="mt-3 overflow-x-auto border border-border">
          <table className="w-full text-sm">
            <caption className="sr-only">Upcoming dividend events</caption>
            <thead>
              <tr className="bg-surface-muted text-xs text-text-muted">
                <th scope="col" className={TH_LEFT}>Holding</th>
                <th scope="col" className={TH_LEFT}>Buy before</th>
                <th scope="col" className={TH_LEFT}>Paid</th>
                <th scope="col" className={TH_LEFT}>Kind</th>
                <th scope="col" className={TH}>Amount</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p, i) => (
                <tr key={i} className="border-t border-border-soft">
                  <th scope="row" className="px-3 py-1.5 text-left font-normal">{p.name ?? p.ticker}</th>
                  <td className="num px-3 py-1.5">{p.ex_date ? formatDay(p.ex_date) : "—"}</td>
                  <td className="num px-3 py-1.5">{formatDay(p.date)}</td>
                  <td className="px-3 py-1.5">{KIND_LABEL[p.kind]}</td>
                  <td className={TD}>{money(p.amount, ccy)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Blueprint>
  );
}

function Accuracy({ data }: { data: PortfolioDividends }) {
  const a = data.accuracy;
  if (!a.available) {
    return (
      <VerdictLine verdict="unknown" className="text-text-muted">
        Forecast accuracy: available from {a.available_from ? formatDay(a.available_from) : "a year after the first stored forecast"}, once a year-old forecast can be compared with what was paid.
      </VerdictLine>
    );
  }
  return (
    <VerdictLine verdict="neutral">
      Forecast made {a.snapshot_as_of ? formatDay(a.snapshot_as_of) : "a year ago"}: {a.hits} of {a.rows} payments arrived as expected ({pct(a.hit_rate, 0)}), mean amount error {pct(a.mean_error, 1)}.
      {a.note ? ` ${a.note}` : ""}
    </VerdictLine>
  );
}
