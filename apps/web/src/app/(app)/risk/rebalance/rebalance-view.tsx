"use client";

import { useState } from "react";

import { Field, Problem, problemText, SaveBar, SavedList, SELECT } from "@/components/risk/common";
import { ProposalSummary } from "@/components/risk/proposal-summary";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatMoney, formatPercent } from "@/lib/format";
import { parseNumber, type RebalanceRequest, type RebalanceResponse } from "@/lib/risk/risk";

import { Notes, Pct, TD, TH, TH_LEFT, Toned, Warnings } from "../../xray/shared";

const ACTION = { buy: "Buy", sell: "Sell", hold: "Hold" } as const;

export function RebalanceView() {
  const [mode, setMode] = useState<RebalanceRequest["mode"]>("to_target");
  const [deposit, setDeposit] = useState("");
  const [fx, setFx] = useState("15");
  const [cost, setCost] = useState("0");
  const [data, setData] = useState<RebalanceResponse | null>(null);
  const [used, setUsed] = useState<RebalanceRequest | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);

  function request(): RebalanceRequest | string {
    const d = parseNumber(deposit);
    const f = parseNumber(fx);
    const c = parseNumber(cost);
    if (d === undefined) return "The deposit must be a number, or blank.";
    if (f === undefined || f === null || f < 0) return "The currency conversion cost must be basis points such as 15.";
    if (c === undefined || c === null || c < 0) return "The trade cost must be basis points such as 0.";
    return { mode, deposit: d ?? 0, fx_cost_bps: f, trade_cost_bps: c };
  }

  async function run() {
    const body = request();
    if (typeof body === "string") return setProblem(body);
    setBusy(true);
    setProblem(null);
    // the previous plan was built from other inputs, so it is not left standing beside a new request
    setData(null);
    setUsed(null);
    try {
      const res = await getBrowserApiClient().POST("/portfolio/rebalance", { body });
      if (!res.response.ok || !res.data) return setProblem(problemText(res.error, "The plan could not be built."));
      setData(res.data);
      setUsed(body);
    } catch (err) {
      setProblem(err instanceof Error ? err.message : "The plan could not be built.");
    } finally {
      setBusy(false);
    }
  }

  async function save(name: string): Promise<string | null> {
    if (!used) return "Build a plan first.";
    const res = await getBrowserApiClient().POST("/portfolio/rebalance/save", { body: { name, request: used } });
    return res.response.ok ? null : problemText(res.error, "The plan could not be saved.");
  }

  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="p-4">
        <PanelHeading level={3} kicker="Plan" title="Get back to your targets" />
        <p className="mt-1 max-w-prose text-sm text-text-muted">
          Proposes the trades that bring positions to the targets in your strategy, keeping the cash floor. It only proposes; nothing is sent to
          your broker.
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-4">
          <Field label="Bring positions" hint="Back into the band costs fewer trades">
            <select className={SELECT} value={mode} onChange={(e) => setMode(e.target.value as RebalanceRequest["mode"])}>
              <option value="to_target">All the way to target</option>
              <option value="to_band">Only to the edge of the band</option>
            </select>
          </Field>
          <Field label="Cash added or taken out" hint="Negative takes cash out">
            <Input inputMode="decimal" value={deposit} onChange={(e) => setDeposit(e.target.value)} placeholder="0" />
          </Field>
          <Field label="Currency conversion, bp" hint="Assumed cost on a foreign buy">
            <Input inputMode="decimal" value={fx} onChange={(e) => setFx(e.target.value)} />
          </Field>
          <Field label="Trade cost, bp" hint="Commission and spread">
            <Input inputMode="decimal" value={cost} onChange={(e) => setCost(e.target.value)} />
          </Field>
        </div>
        <div className="mt-3">
          <Button type="button" onClick={run} disabled={busy}>
            {busy ? "Planning…" : "Build plan"}
          </Button>
        </div>
        <div className="mt-3">
          <Problem>{problem}</Problem>
        </div>
      </Blueprint>

      {data ? <Plan data={data} saveBar={<SaveBar save={save} onSaved={() => setRefresh((n) => n + 1)} label="Save plan" />} /> : null}

      <Blueprint className="p-4">
        <PanelHeading level={3} kicker="Kept" title="Saved plans" />
        <p className="mt-1 text-sm text-text-muted">Opening one shows the plan as it was on the day, not recalculated.</p>
        <div className="mt-3">
          <SavedList
            kind="rebalance_plan"
            refreshKey={refresh}
            onOpen={(payload) => {
              setData(payload.result as RebalanceResponse);
              setUsed(payload.request as RebalanceRequest);
            }}
          />
        </div>
      </Blueprint>
    </div>
  );
}

function Plan({ data, saveBar }: { data: RebalanceResponse; saveBar: React.ReactNode }) {
  const p = data.plan;
  const currency = data.xray?.base_currency ?? "";
  const money = (v: number, signed = false) => formatMoney(v, currency, { decimals: 0, signed });
  const actions = p.trades.filter((t) => t.action !== "hold");
  const sellImpacts = Object.values(data.lot_impacts);
  return (
    <Blueprint className="p-4">
      <PanelHeading level={2} kicker={`As of ${formatDate(data.as_of)}`} title="Proposed trades" />
      <Warnings warnings={p.warnings} />
      <dl className="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-5">
        <Stat label="Buys" value={p.buys} />
        <Stat label="Sells" value={p.sells} />
        <Stat label="Assumed costs" text={money(p.costs)} />
        <Stat label="Cash after" text={money(p.cash_after)} note={`floor ${money(p.cash_floor)}`} />
        <Stat label="Outside band" text={`${p.drift_before.out_of_band} → ${p.drift_after.out_of_band}`} />
      </dl>
      {p.unfunded > 0 ? (
        <p className="mt-3 text-sm text-text-muted">
          {money(p.unfunded)} of buying could not be funded without breaking the cash floor, so buys were scaled back.
        </p>
      ) : null}

      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-sm">
            <caption className="sr-only">Proposed trades and the weights they give</caption>
          <thead className="text-xs text-text-muted">
            <tr className="border-b border-border">
              <th scope="col" className={TH_LEFT}>Holding</th>
              <th scope="col" className={TH_LEFT}>Action</th>
              <th scope="col" className={TH}>Amount</th>
              <th scope="col" className={TH}>Weight now</th>
              <th scope="col" className={TH}>Target</th>
              <th scope="col" className={TH}>Weight after</th>
              <th scope="col" className={TH}>Units</th>
            </tr>
          </thead>
          <tbody>
            {p.trades.map((t) => (
              <tr key={t.ticker} className="border-b border-border-soft align-top">
                <td className="px-3 py-1.5">
                  <div className="font-medium">{t.ticker}</div>
                  <div className="max-w-xs text-xs text-text-muted">{t.reason}</div>
                </td>
                <td className="px-3 py-1.5">{ACTION[t.action]}</td>
                <td className={TD}>{t.action === "hold" ? "—" : <Toned value={t.trade_value}>{money(t.trade_value, true)}</Toned>}</td>
                <td className={TD}>
                  <Pct value={t.weight_now} />
                </td>
                <td className={TD}>
                  <Pct value={t.target_weight} />
                </td>
                <td className={TD}>
                  <Pct value={t.weight_after} />
                </td>
                <td className={TD}>{t.estimated_units === null ? "—" : t.estimated_units.toFixed(2)}</td>
              </tr>
            ))}
            {actions.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-3 py-3 text-text-muted">
                  Nothing needs to move: every position with a target is inside its band.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      {sellImpacts.length > 0 ? (
        <div className="mt-5">
          <h3 className="mb-1 text-sm font-medium">What the sales would do to your tax position</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
            <caption className="sr-only">Sales and the gain each would realise</caption>
              <thead className="text-xs text-text-muted">
                <tr className="border-b border-border">
                  <th scope="col" className={TH_LEFT}>Sale</th>
                  <th scope="col" className={TH}>Proceeds</th>
                  <th scope="col" className={TH}>Gain</th>
                  <th scope="col" className={TH}>Sold inside the holding test</th>
                  <th scope="col" className={TH_LEFT}>Note</th>
                </tr>
              </thead>
              <tbody>
                {sellImpacts.map((s) => (
                  <tr key={s.ticker} className="border-b border-border-soft align-top">
                    <td className="px-3 py-1.5 font-medium">{s.ticker}</td>
                    <td className={TD}>{money(s.gross_proceeds)}</td>
                    <td className={TD}>
                      <Toned value={s.gain}>{money(s.gain, true)}</Toned>
                    </td>
                    <td className={TD}>{money(s.gross_within_time_test)}</td>
                    <td className="px-3 py-1.5 text-xs text-text-muted">
                      {s.units_unmatched > 0 ? `${s.units_unmatched.toFixed(2)} units have no lot on record. ` : ""}
                      {s.near_test_lots > 0 && s.earliest_pass ? `${s.near_test_lots} lots pass the test soon; the earliest on ${formatDate(s.earliest_pass)}. ` : ""}
                      {s.method_note}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      <div className="mt-5">
        <h3 className="mb-1 text-sm font-medium">Drift before and after</h3>
        <ul className="grid gap-x-8 gap-y-1 text-sm sm:grid-cols-2">
          {p.drift_after.positions
            .filter((d) => d.target_weight !== null)
            .map((after) => {
              const before = p.drift_before.positions.find((b) => b.ticker === after.ticker);
              return (
                <li key={after.ticker} className="flex justify-between gap-3 border-b border-border-soft py-1">
                  <span>{after.ticker}</span>
                  <span className="num text-text-muted">
                    {before?.drift == null ? "—" : formatPercent(before.drift, { signed: true })} → <span className="text-text">{after.drift == null ? "—" : formatPercent(after.drift, { signed: true })}</span>
                  </span>
                </li>
              );
            })}
        </ul>
      </div>

      {data.xray ? (
        <div className="mt-5">
          <h3 className="mb-1 text-sm font-medium">What the X-Ray looks like after</h3>
          <ProposalSummary proposal={data.xray} />
        </div>
      ) : null}

      <div className="mt-5 space-y-3">
        <Notes notes={[...data.notes, p.method]} title="How this was worked out" />
        <div className="border-t border-border-soft pt-3">{saveBar}</div>
      </div>
    </Blueprint>
  );
}

function Stat({ label, value, text, note }: { label: string; value?: number; text?: string; note?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className="num mt-0.5 text-xl leading-tight font-semibold">{text ?? value}</dd>
      {note ? <p className="mt-0.5 text-xs text-text-muted">{note}</p> : null}
    </div>
  );
}
