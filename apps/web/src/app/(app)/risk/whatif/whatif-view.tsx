"use client";

import { useState } from "react";

import { Field, Problem, problemText, SaveBar, SavedList, SELECT, TEXTAREA } from "@/components/risk/common";
import { PretradeChecks } from "@/components/risk/pretrade-checks";
import { ProposalSummary } from "@/components/risk/proposal-summary";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPercent } from "@/lib/format";
import { isRatio, parseKeyed, parseNumber, parsePercent, statLabel, type RiskReport, type WhatIfCase, type WhatIfResult } from "@/lib/risk/risk";

import { Notes } from "../../xray/shared";
import { Ladder } from "../stress/stress-view";

type Kind = WhatIfCase["kind"];

const KINDS: { value: Kind; label: string; help: string }[] = [
  { value: "new_security", label: "Add a new security", help: "Give it a weight; fund it from cash or from every holding in proportion." },
  { value: "contribution", label: "Add money", help: "Where the deposit goes; whatever you do not assign stays in cash." },
  { value: "withdrawal", label: "Take money out", help: "Taken from cash first, then from every holding in proportion." },
  { value: "reinvest_dividends", label: "Reinvest dividends", help: "Put an amount of dividends to work." },
  { value: "fx_move", label: "A currency moves", help: "How the portfolio is hit if a currency moves against your base." },
  { value: "benchmark_change", label: "Use another benchmark", help: "Recompute the risk figures against a different index." },
  { value: "thesis_exit", label: "Exit a holding", help: "Sell it all and see what remains." },
];

const SHOW_STATS = ["volatility", "max_drawdown", "var_95", "beta", "tracking_error"];

export function WhatIfView() {
  const [kind, setKind] = useState<Kind>("new_security");
  const [ticker, setTicker] = useState("");
  const [weight, setWeight] = useState("");
  const [fundFrom, setFundFrom] = useState<WhatIfCase["fund_from"]>("cash");
  const [amount, setAmount] = useState("");
  const [split, setSplit] = useState("");
  const [currency, setCurrency] = useState("");
  const [move, setMove] = useState("");
  const [benchmark, setBenchmark] = useState("");
  const [result, setResult] = useState<WhatIfResult | null>(null);
  const [lastCase, setLastCase] = useState<WhatIfCase | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const meta = KINDS.find((k) => k.value === kind)!;

  function build(): WhatIfCase | string {
    const c: WhatIfCase = { kind, fund_from: fundFrom };
    if (kind === "new_security") {
      const w = parsePercent(weight);
      if (ticker.trim() === "") return "Enter the ticker of the security to add.";
      if (w === null || w === undefined || w <= 0) return "Enter the weight as a percentage, such as 5.";
      // Broker tickers are case-sensitive (CSPXl_EQ); the server matches the case to a held one.
      return { ...c, ticker: ticker.trim(), weight: w };
    }
    if (kind === "contribution" || kind === "withdrawal" || kind === "reinvest_dividends") {
      const a = parseNumber(amount);
      if (a === null || a === undefined || a <= 0) return "Enter the amount in your base currency.";
      const s = parseKeyed(split, { percent: true, keepCase: true });
      if (s.problems.length > 0) return s.problems[0];
      return { ...c, amount: a, split: s.values };
    }
    if (kind === "fx_move") {
      const m = parsePercent(move);
      if (currency.trim().length !== 3) return "Enter a three-letter currency code, such as USD.";
      if (m === null || m === undefined) return "Enter the move as a percentage, such as -10.";
      return { ...c, currency: currency.trim().toUpperCase(), move: m };
    }
    if (kind === "benchmark_change") {
      if (benchmark.trim() === "") return "Enter the symbol of the benchmark to compare with.";
      // Stored series keep their own case (issuer series are `ishares-bm-…`).
      return { ...c, benchmark_symbol: benchmark.trim() };
    }
    if (ticker.trim() === "") return "Enter the ticker of the holding to exit.";
    return { ...c, ticker: ticker.trim() };
  }

  async function run() {
    const body = build();
    if (typeof body === "string") return setProblem(body);
    setBusy(true);
    setProblem(null);
    const res = await getBrowserApiClient().POST("/portfolio/scenarios/whatif", { body });
    setBusy(false);
    if (!res.response.ok || !res.data) return setProblem(problemText(res.error, "The case could not be run."));
    setResult(res.data);
    setLastCase(body);
  }

  async function save(name: string): Promise<string | null> {
    if (!lastCase) return "Run a case first.";
    const res = await getBrowserApiClient().POST("/portfolio/scenarios/whatif/save", { body: { name, case: lastCase } });
    return res.response.ok ? null : problemText(res.error, "The case could not be saved.");
  }

  const needsTicker = kind === "new_security" || kind === "thesis_exit";
  const needsAmount = kind === "contribution" || kind === "withdrawal" || kind === "reinvest_dividends";

  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="p-4">
        <PanelHeading level={3} kicker="Try it" title="What if I…" />
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          <Field label="Change" hint={meta.help} className="sm:col-span-3">
            <select className={SELECT} value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
              {KINDS.map((k) => (
                <option key={k.value} value={k.value}>
                  {k.label}
                </option>
              ))}
            </select>
          </Field>
          {needsTicker ? (
            <Field label="Ticker">
              <Input value={ticker} onChange={(e) => setTicker(e.target.value)} placeholder="e.g. VUAA" />
            </Field>
          ) : null}
          {kind === "new_security" ? (
            <>
              <Field label="Weight, %" hint="Share of the total">
                <Input inputMode="decimal" value={weight} onChange={(e) => setWeight(e.target.value)} placeholder="5" />
              </Field>
              <Field label="Pay for it from">
                <select className={SELECT} value={fundFrom} onChange={(e) => setFundFrom(e.target.value as WhatIfCase["fund_from"])}>
                  <option value="cash">Cash</option>
                  <option value="pro_rata">Every holding in proportion</option>
                </select>
              </Field>
            </>
          ) : null}
          {needsAmount ? (
            <>
              <Field label="Amount, base currency">
                <Input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="2000" />
              </Field>
              <Field label="Split, %" hint="One per line: VUAA 50. Blank keeps it in cash (dividends follow current weights)." className="sm:col-span-2">
                <textarea className={TEXTAREA} value={split} onChange={(e) => setSplit(e.target.value)} />
              </Field>
            </>
          ) : null}
          {kind === "fx_move" ? (
            <>
              <Field label="Currency">
                <Input value={currency} onChange={(e) => setCurrency(e.target.value)} maxLength={3} placeholder="USD" />
              </Field>
              <Field label="Move against your base, %" hint="-10 means it weakens by 10%">
                <Input inputMode="decimal" value={move} onChange={(e) => setMove(e.target.value)} placeholder="-10" />
              </Field>
            </>
          ) : null}
          {kind === "benchmark_change" ? (
            <Field label="Benchmark symbol">
              <Input value={benchmark} onChange={(e) => setBenchmark(e.target.value)} placeholder="e.g. VWCE" />
            </Field>
          ) : null}
        </div>
        <div className="mt-3">
          <Button type="button" onClick={run} disabled={busy}>
            {busy ? "Running…" : "Run case"}
          </Button>
        </div>
        <div className="mt-3">
          <Problem>{problem}</Problem>
        </div>
      </Blueprint>

      {result ? (
        <Blueprint className="p-4">
          <PanelHeading level={2} kicker="Result" title="What it would change" />
          <ul className="mt-3 space-y-1 text-sm">
            {result.summary.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
          {result.proposal ? (
            <div className="mt-4">
              <h3 className="mb-1 text-sm font-medium">What the X-Ray would show</h3>
              <ProposalSummary proposal={result.proposal} />
            </div>
          ) : null}
          {result.shock ? <Ladder result={result.shock} currency={result.proposal?.base_currency ?? ""} /> : null}
          {result.risk ? <RiskCompare after={result.risk} before={result.risk_current} /> : null}
          {result.pretrade ? (
            <div className="mt-4">
              <h3 className="mb-1 text-sm font-medium">Checked against your rules</h3>
              <PretradeChecks result={result.pretrade} />
            </div>
          ) : null}
          <div className="mt-4 space-y-3">
            <Notes notes={[...result.assumptions, ...result.notes]} title="Assumptions and limits" />
            <div className="border-t border-border-soft pt-3">
              <SaveBar save={save} onSaved={() => setRefresh((n) => n + 1)} label="Save case" />
            </div>
          </div>
        </Blueprint>
      ) : null}

      <Blueprint className="p-4">
        <PanelHeading level={3} kicker="Kept" title="Saved cases" />
        <div className="mt-3">
          <SavedList
            kind="whatif_case"
            refreshKey={refresh}
            onOpen={(payload) => {
              setResult(payload.result as WhatIfResult);
              setLastCase(payload.request as WhatIfCase);
            }}
          />
        </div>
      </Blueprint>
    </div>
  );
}

function figure(key: string, report: RiskReport | null | undefined): string {
  const f = report?.stats[key];
  if (!f || f.value === null || f.value === undefined) return "—";
  const n = Number(f.value);
  if (!Number.isFinite(n)) return "—";
  return isRatio(key) ? n.toFixed(2) : formatPercent(n, { decimals: 1 });
}

function RiskCompare({ after, before }: { after: RiskReport; before: RiskReport | null | undefined }) {
  return (
    <div className="mt-4">
      <h3 className="mb-1 text-sm font-medium">Risk figures</h3>
      <div className="overflow-x-auto border border-border">
        <table className="w-full text-sm">
            <caption className="sr-only">Portfolio measures before and after the change</caption>
          <thead>
            <tr className="bg-surface-muted text-left">
              <th scope="col" className="px-3 py-1.5 font-medium">Measure</th>
              {before ? <th scope="col" className="px-3 py-1.5 text-right font-medium">Now</th> : null}
              <th scope="col" className="px-3 py-1.5 text-right font-medium">{before ? "After" : (after.benchmark_symbol ?? "Result")}</th>
            </tr>
          </thead>
          <tbody>
            {SHOW_STATS.map((key) => (
              <tr key={key} className="border-t border-border-soft">
                <th scope="row" className="px-3 py-1.5 text-left font-normal">{statLabel(key)}</th>
                {before ? <td className="num px-3 py-1.5 text-right">{figure(key, before)}</td> : null}
                <td className="num px-3 py-1.5 text-right">{figure(key, after)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-1 text-xs text-text-muted">Computed on past returns; a new security uses only the history it has.</p>
    </div>
  );
}
