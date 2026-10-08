"use client";

import { useState } from "react";

import { Field, Problem, problemText, TEXTAREA } from "@/components/risk/common";
import { PretradeChecks } from "@/components/risk/pretrade-checks";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { parseLegs, parseNumber, type PretradeResult } from "@/lib/risk/risk";

export function PretradeView() {
  const [text, setText] = useState("");
  const [deposit, setDeposit] = useState("");
  const [result, setResult] = useState<PretradeResult | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    const legs = parseLegs(text);
    if ("error" in legs) return setProblem(legs.error);
    const cash = parseNumber(deposit);
    if (cash === undefined) return setProblem("The deposit must be a number, or blank.");
    setBusy(true);
    setProblem(null);
    const res = await getBrowserApiClient().POST("/portfolio/pretrade", { body: { trades: legs.legs, deposit: cash ?? 0 } });
    setBusy(false);
    if (!res.response.ok || !res.data) return setProblem(problemText(res.error, "The trade could not be checked."));
    setResult(res.data);
  }

  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="p-4">
        <PanelHeading level={3} kicker="Before you place it" title="Check a trade against your rules" />
        <p className="mt-1 max-w-prose text-sm text-text-muted">
          One trade is a buy or a sell. A sell and a buy together are read as a switch, with its margin and its tax cost. Amounts are in your base currency.
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-[2fr_1fr]">
          <Field label="Trades" hint="One per line: buy VUAA 1500, sell CSPX 800">
            <textarea className={TEXTAREA} value={text} onChange={(e) => setText(e.target.value)} placeholder={"buy VUAA 1500\nsell CSPX 800"} />
          </Field>
          <Field label="Cash added alongside" hint="Blank for none; negative takes cash out">
            <Input inputMode="decimal" value={deposit} onChange={(e) => setDeposit(e.target.value)} placeholder="0" />
          </Field>
        </div>
        <div className="mt-3">
          <Button type="button" onClick={run} disabled={busy}>
            {busy ? "Checking…" : "Check trade"}
          </Button>
        </div>
        <div className="mt-3">
          <Problem>{problem}</Problem>
        </div>
      </Blueprint>
      {result ? (
        <Blueprint className="p-4">
          <PanelHeading level={2} kicker={result.is_switch ? "Switch" : "Trade"} title="What the checks say" />
          <div className="mt-3">
            <PretradeChecks result={result} />
          </div>
        </Blueprint>
      ) : null}
    </div>
  );
}
