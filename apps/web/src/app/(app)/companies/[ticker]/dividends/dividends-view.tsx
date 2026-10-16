"use client";

import { CheckIcon, LoaderCircleIcon } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ResourceGate, SourceLink } from "@/components/company/primitives";
import { AnswerStrip, CoverageChip } from "@/components/insight/answer-strip";
import { ChartWithFooter } from "@/components/insight/chart-footer";
import { CheckList } from "@/components/insight/check-list";
import { ForecastBars } from "@/components/insight/forecast-bars";
import { VerdictLine } from "@/components/insight/verdict-line";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDay } from "@/lib/charts/time";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { KIND_LABEL, money, pct, type CompanyDividends } from "@/lib/insight/income";
import { num, type ForecastBar } from "@/lib/insight/insight";
import { cn } from "@/lib/utils";

const TH = "px-3 py-1.5 text-right font-medium";
const TH_LEFT = "px-3 py-1.5 text-left font-medium";
const TD = "num px-3 py-1.5 text-right";

const FREQUENCY: Record<string, string> = {
  monthly: "Pays monthly",
  quarterly: "Pays quarterly",
  semi_annual: "Pays twice a year",
  annual: "Pays once a year",
  irregular: "Irregular payer",
  none: "No regular dividend",
  unknown: "Pattern not known yet",
};

const ORIGIN_LABEL: Record<string, string> = {
  event: "Corporate action",
  single_source: "One source, unconfirmed",
  receipt: "Your receipt",
};

export function CompanyDividendsView({ ticker }: { ticker: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/dividends", { params: { path: { ticker } } }), [ticker]);
  const { state, reload } = useApiResource(fetcher);
  return (
    <ResourceGate state={state} label="dividends">
      {(d: CompanyDividends) => <Body d={d} onChanged={reload} />}
    </ResourceGate>
  );
}

function dpsBars(d: CompanyDividends): ForecastBar[] {
  const bars: ForecastBar[] = d.annual.map((a) => {
    const v = num(a.per_share) ?? 0;
    return { key: String(a.year), label: String(a.year), known: v, estimated: 0, phase: "history", total: v };
  });
  if (d.schedule.length) {
    const known = d.schedule.filter((p) => p.kind === "declared").reduce((s, p) => s + (num(p.per_share) ?? 0), 0);
    const est = d.schedule.filter((p) => p.kind === "estimated").reduce((s, p) => s + (num(p.per_share) ?? 0), 0);
    bars.push({ key: "next", label: "Next 12m", known, estimated: est, phase: "forecast", total: known + est });
  }
  return bars;
}

function Body({ d, onChanged }: { d: CompanyDividends; onChanged: () => void }) {
  const ccy = d.history[0]?.currency ?? d.schedule[0]?.currency ?? null;
  const bars = useMemo(() => dpsBars(d), [d]);
  const singleSource = d.history.filter((h) => h.origin === "single_source").length;
  if (d.frequency === "none" && d.history.length === 0) {
    return (
      <div className="flex flex-col gap-5">
        <AlertMessage tone="info" title="No dividend on record">
          {d.reason ?? "No confirmed or suggested cash dividend is stored for this listing."} The dividend checks read “not applicable”, not zero.
        </AlertMessage>
        {d.checks?.length ? (
          <Blueprint className="px-[22px] py-4">
            <CheckList checks={d.checks} ticker={d.ticker} title="Dividend checks" />
          </Blueprint>
        ) : null}
      </div>
    );
  }
  const axis = d.dividend_axis;
  const next = d.schedule[0];
  const forward = d.schedule.reduce((s, p) => s + (num(p.per_share) ?? 0), 0);
  return (
    <div className="flex flex-col gap-5">
      <AnswerStrip
        lead={{
          label: "Dividend per share, next 12 months",
          value: d.schedule.length ? money(forward, ccy, 2) : "No schedule",
          sentence: d.schedule.length
            ? `${FREQUENCY[d.frequency] ?? d.frequency}. Declared payments are stored as announced; the rest repeat the last amount and are marked estimated.`
            : d.reason ?? "History too short to estimate a schedule.",
        }}
        chip={
          <CoverageChip tone={d.status === "complete" && !singleSource ? "neutral" : d.status === "unavailable" ? "unavailable" : "partial"}>
            {d.status === "complete"
              ? singleSource
                ? `${singleSource} of ${d.history.length} past payments from one source, unconfirmed`
                : "Confirmed history"
              : d.reason ?? "Partly confirmed history"}
          </CoverageChip>
        }
        figures={[
          { label: "Years without a cut", value: d.years_without_cut ?? "—" },
          { label: "5-year growth a year", value: pct(d.growth_5y, 1, true) },
          { label: "10-year growth a year", value: pct(d.growth_10y, 1, true) },
          { label: "Dividend checks", value: axis ? (axis.applicable ? `${axis.passes} of ${axis.applicable}` : "n/a") : "—", note: axis?.unknown ? `${axis.unknown} unknown` : undefined },
        ]}
      />
      {d.buy_before ? (
        <VerdictLine verdict="neutral">
          Buy before {formatDay(d.buy_before)} to receive the next payment
          {next ? ` of ${money(next.per_share, next.currency ?? ccy, 4)} a share on ${formatDay(next.date)} (${KIND_LABEL[next.kind].toLowerCase()})` : ""}.
        </VerdictLine>
      ) : null}
      {d.disagreements.length || d.filed_dps_check ? (
        <AlertMessage tone="info" title="Sources do not fully agree">
          <ul className="list-disc space-y-1 pl-4">
            {d.disagreements.map((x) => (
              <li key={x}>{x}</li>
            ))}
            {d.filed_dps_check ? <li>{d.filed_dps_check}</li> : null}
          </ul>
        </AlertMessage>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Blueprint className="px-[22px] py-4">
          <PanelHeading kicker={`Per share, ${ccy ?? "listing currency"}`} title="Dividend per share by year" />
          {d.annual_problem ? <p className="mt-1 text-xs text-text-muted">{d.annual_problem}</p> : null}
          <ChartWithFooter
            className="mt-3"
            chart={<ForecastBars bars={bars} format={(v) => money(v, ccy, 2)} label="Dividend per share by year" />}
            table={
              <table className="w-full text-sm">
                <caption className="sr-only">Dividend payments per share</caption>
                <thead>
                  <tr className="bg-surface-muted text-xs text-text-muted">
                    <th scope="col" className={TH_LEFT}>Paid</th>
                    <th scope="col" className={TH_LEFT}>Ex-date</th>
                    <th scope="col" className={TH}>Per share</th>
                    <th scope="col" className={TH_LEFT}>Recorded from</th>
                  </tr>
                </thead>
                <tbody>
                  {[...d.history].reverse().map((h) => (
                    <tr key={h.ref} className="border-t border-border-soft">
                      <td className="num px-3 py-1">{formatDay(h.pay_date)}</td>
                      <td className="num px-3 py-1">{h.ex_date ? formatDay(h.ex_date) : "—"}</td>
                      <td className={TD}>{money(h.per_share, h.currency, 4)}</td>
                      <td className="px-3 py-1 text-text-muted">{ORIGIN_LABEL[h.origin]}{h.special ? ", special" : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            }
            dataset="Confirmed corporate actions and your receipts; specials excluded from growth"
            source={d.sources.join(", ") || null}
            learnHref="/methods/dividend_income"
          />
        </Blueprint>
        <div className="flex flex-col gap-5">
          <YieldVsPeers d={d} />
          <PayoutPanel d={d} />
        </div>
      </div>

      <Blueprint className="px-[22px] py-4">
        <PanelHeading kicker="Six checks, failed first" title="Is the dividend dependable" />
        <CheckList className="mt-2" checks={d.checks ?? []} ticker={d.ticker} failedFirst />
      </Blueprint>

      {d.schedule.length ? <Schedule d={d} ccy={ccy} /> : null}
      {d.specials.length ? (
        <Blueprint className="px-[22px] py-4">
          <PanelHeading kicker="Not repeated in estimates" title="Special dividends" level={3} />
          <ul className="mt-2 space-y-1 text-sm">
            {d.specials.map((s) => (
              <li key={s.ref} className="num">
                {formatDay(s.pay_date)}: {money(s.per_share, s.currency, 4)} a share
              </li>
            ))}
          </ul>
        </Blueprint>
      ) : null}
      {d.held ? <Received d={d} /> : null}
      <Events d={d} onChanged={onChanged} />
      <p className="text-xs text-text-faint">
        Method {d.method_version}. {d.explain.formula}
      </p>
    </div>
  );
}

function YieldVsPeers({ d }: { d: CompanyDividends }) {
  const y = d.yield_vs_peers;
  const c = num(y?.company);
  const p = num(y?.peer_median);
  const max = Math.max(c ?? 0, p ?? 0, 0.0001);
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker={y?.basis ?? "Trailing yield"} title="Yield against peers" level={3} />
      {c === null && p === null ? (
        <p className="mt-2 text-sm text-text-faint">{y?.reason ?? "No yield or peer median available."}</p>
      ) : (
        <div className="mt-3 space-y-2 text-sm">
          {[
            { label: "This company", v: c, cls: "bg-chart-line" },
            { label: `Peer median${y?.peer_count ? ` (${y.peer_count} peers)` : ""}`, v: p, cls: "bg-chart-benchmark" },
          ].map((r) => (
            <div key={r.label}>
              <div className="flex justify-between">
                <span className="text-text-muted">{r.label}</span>
                <span className="num font-medium">{r.v === null ? "—" : pct(r.v, 2)}</span>
              </div>
              <div className="mt-1 h-2 bg-surface-muted">
                <div className={cn("h-full", r.cls)} style={{ width: `${r.v === null ? 0 : (r.v / max) * 100}%` }} />
              </div>
            </div>
          ))}
          {y?.reason ? <p className="text-xs text-text-muted">{y.reason}</p> : null}
        </div>
      )}
    </Blueprint>
  );
}

function PayoutPanel({ d }: { d: CompanyDividends }) {
  const p = d.payout;
  if (!p) {
    return (
      <Blueprint className="px-[22px] py-4">
        <PanelHeading kicker="Share of profit and cash paid out" title="Payout" level={3} />
        <p className="mt-2 text-sm text-text-faint">No filed dividends paid with earnings or cash flow to compare.</p>
      </Blueprint>
    );
  }
  const rows = [
    { label: "Of earnings", v: num(p.earnings), limit: num(p.limit_earnings) ?? 0.75 },
    { label: "Of free cash flow", v: num(p.cash), limit: num(p.limit_cash) ?? 0.75 },
  ];
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker={p.fiscal_year ? `Fiscal ${p.fiscal_year}` : "Latest year"} title="Payout" level={3} />
      <div className="mt-3 space-y-3 text-sm">
        {rows.map((r) => {
          const scaleMax = Math.max(1, r.limit * 1.4, r.v ?? 0);
          const over = r.v !== null && r.v > r.limit;
          return (
            <div key={r.label}>
              <div className="flex justify-between">
                <span className="text-text-muted">{r.label}</span>
                <span className="num font-medium">
                  {r.v === null ? "—" : pct(r.v, 0)} <span className="text-xs font-normal text-text-muted">limit {pct(r.limit, 0)}</span>
                </span>
              </div>
              <div className="relative mt-1 h-2 bg-surface-muted">
                {r.v !== null ? <div className={cn("h-full", over ? "bg-loss" : "bg-gain")} style={{ width: `${(Math.max(0, r.v) / scaleMax) * 100}%` }} /> : null}
                <span aria-hidden="true" className="absolute -top-1 h-4 w-px bg-text" style={{ left: `${(r.limit / scaleMax) * 100}%` }} />
              </div>
              <p className="mt-0.5 text-[11px] text-text-faint">{r.v === null ? "Input missing" : over ? "Above the limit" : "Within the limit"}</p>
            </div>
          );
        })}
      </div>
    </Blueprint>
  );
}

function Schedule({ d, ccy }: { d: CompanyDividends; ccy: string | null }) {
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker="Declared and estimated" title="Next 12 months" level={3} />
      <div className="mt-3 overflow-x-auto border border-border">
        <table className="w-full text-sm">
          <caption className="sr-only">Dividend schedule for the next 12 months</caption>
          <thead>
            <tr className="bg-surface-muted text-xs text-text-muted">
              <th scope="col" className={TH_LEFT}>Paid</th>
              <th scope="col" className={TH_LEFT}>Ex-date</th>
              <th scope="col" className={TH_LEFT}>Kind</th>
              <th scope="col" className={TH}>Per share</th>
              <th scope="col" className={TH}>{d.held ? "Your amount" : "Amount"}</th>
            </tr>
          </thead>
          <tbody>
            {d.schedule.map((p, i) => (
              <tr key={i} className="border-t border-border-soft">
                <td className="num px-3 py-1.5">{formatDay(p.date)}</td>
                <td className="num px-3 py-1.5">{p.ex_date ? formatDay(p.ex_date) : "—"}</td>
                <td className="px-3 py-1.5">{KIND_LABEL[p.kind]}</td>
                <td className={TD}>{money(p.per_share, p.currency ?? ccy, 4)}</td>
                <td className={TD}>{d.held ? money(p.amount, d.account_currency) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Blueprint>
  );
}

function Received({ d }: { d: CompanyDividends }) {
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker="Trading212 payments matched to the declared dividend" title="What you received" level={3} />
      {d.received.length === 0 ? (
        <p className="mt-2 text-sm text-text-faint">No dividend payment from this holding is recorded yet.</p>
      ) : (
        <div className="mt-3 overflow-x-auto border border-border">
          <table className="w-full text-sm">
            <caption className="sr-only">Received payments with their declared counterpart</caption>
            <thead>
              <tr className="bg-surface-muted text-xs text-text-muted">
                <th scope="col" className={TH_LEFT}>Paid</th>
                <th scope="col" className={TH}>Received</th>
                <th scope="col" className={TH}>Declared, in account currency</th>
                <th scope="col" className={cn(TH, "hidden sm:table-cell")}>Withheld</th>
                <th scope="col" className={cn(TH_LEFT, "hidden md:table-cell")}>Note</th>
              </tr>
            </thead>
            <tbody>
              {[...d.received].reverse().map((r) => (
                <tr key={r.payment_id} className="border-t border-border-soft">
                  <td className="num px-3 py-1.5">{formatDay(r.paid_on)}</td>
                  <td className={TD}>{money(r.received, d.account_currency)}</td>
                  <td className={TD}>{r.declared_event_id ? money(r.declared_in_account, d.account_currency) : <span className="text-text-faint">No declared record</span>}</td>
                  <td className={cn(TD, "hidden sm:table-cell")}>{pct(r.withholding_share, 0)}</td>
                  <td className="hidden px-3 py-1.5 text-xs text-text-muted md:table-cell">{r.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Blueprint>
  );
}

function Events({ d, onChanged }: { d: CompanyDividends; onChanged: () => void }) {
  const suggested = d.events.filter((e) => e.status === "suggested");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirm = async (ids: number[]) => {
    setBusy(true);
    setError(null);
    const res = await getBrowserApiClient().POST("/companies/{ticker}/dividends/confirm", { params: { path: { ticker: d.ticker } }, body: { event_ids: ids } });
    setBusy(false);
    if (res.error) setError("The events could not be confirmed. Reload and try again.");
    else onChanged();
  };
  const all = [...d.events, ...d.unmatched_events];
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading
        kicker="Corporate actions behind the history"
        title="Dividend events"
        level={3}
        action={
          suggested.length ? (
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void confirm(suggested.map((e) => e.id))}>
              {busy ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <CheckIcon aria-hidden="true" />}
              Confirm {suggested.length} suggested
            </Button>
          ) : null
        }
      />
      {error ? <AlertMessage className="mt-2" title={error} /> : null}
      {all.length === 0 ? (
        <p className="mt-2 text-sm text-text-faint">No events stored.</p>
      ) : (
        <div className="mt-3 max-h-96 overflow-auto border border-border">
          <table className="w-full text-sm">
            <caption className="sr-only">Stored dividend events</caption>
            <thead>
              <tr className="sticky top-0 bg-surface-muted text-xs text-text-muted">
                <th scope="col" className={TH_LEFT}>Ex-date</th>
                <th scope="col" className={TH}>Per share</th>
                <th scope="col" className={TH_LEFT}>Status</th>
                <th scope="col" className={TH_LEFT}>Source</th>
              </tr>
            </thead>
            <tbody>
              {all.map((e) => (
                <tr key={e.id} className="border-t border-border-soft">
                  <td className="num px-3 py-1.5">{formatDay(e.ex_date)}</td>
                  <td className={TD}>
                    {money(e.per_share, e.currency, 4)}
                    {e.special ? <span className="ml-1 text-xs text-text-muted">special</span> : null}
                  </td>
                  <td className="px-3 py-1.5">
                    {e.status === "suggested" ? (
                      <button type="button" disabled={busy} onClick={() => void confirm([e.id])} className="text-accent-ink underline underline-offset-2">
                        Suggested: confirm
                      </button>
                    ) : (
                      e.status
                    )}
                    {e.alternatives.length ? <span className="ml-1 text-xs text-text-muted">also {e.alternatives.join(", ")}</span> : null}
                  </td>
                  <td className="px-3 py-1.5">
                    <SourceLink href={e.source_url}>{e.source}</SourceLink>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Blueprint>
  );
}
