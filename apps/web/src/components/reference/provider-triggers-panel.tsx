"use client";

import { useState } from "react";

import { QuotaButton, SaveNote, useMutation } from "@/components/reference/action-bits";
import { Field, SELECT_CLASS } from "@/components/reference/form-bits";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { AV_FUNCTIONS, validateAv } from "@/lib/reference/endpoint-forms";

const STATEMENTS = [
  { id: "INCOME_STATEMENT", label: "Income statement" },
  { id: "BALANCE_SHEET", label: "Balance sheet" },
  { id: "CASH_FLOW", label: "Cash flow" },
] as const;

/**
 * Manual provider calls for one holding: Diffbot and People Data Labs company research, Alpha
 * Vantage commodity or FX context and statements, and the Finnhub peer and EPS-surprise lookup.
 * Every call spends free-tier credits, so each needs its own explicit confirmation.
 */
export function ProviderTriggersPanel({ ticker, onChanged }: { ticker: string; onChanged?: () => void }) {
  const api = getBrowserApiClient();
  const diffbot = useMutation();
  const pdl = useMutation();
  const finnhub = useMutation();
  const avStmt = useMutation();
  const avExp = useMutation();
  const [statement, setStatement] = useState<(typeof STATEMENTS)[number]["id"]>("INCOME_STATEMENT");
  const [fn, setFn] = useState<(typeof AV_FUNCTIONS)[number]>("BRENT");
  const [series, setSeries] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [touched, setTouched] = useState(false);
  const avErrors = validateAv(fn, series, from, to);
  const done = (what: string) => () => `${what} refreshed for ${ticker}.`;

  return (
    <Blueprint aria-labelledby="provider-triggers-heading" className="flex min-w-0 flex-col gap-4 px-[22px] py-5">
      <PanelHeading kicker="Providers · manual calls" title={<span id="provider-triggers-heading">Fetch more data for {ticker}</span>} />
      <p className="max-w-[68ch] text-sm text-text-muted">
        Nothing below runs on its own. Each button calls a provider on its free tier, and each call counts against that provider&apos;s quota. A provider that
        denies the call is recorded and not retried; see Data sources for what is left.
      </p>

      <div className="space-y-3">
        <QuotaButton
          label="Company research from Diffbot"
          provider="Diffbot"
          cost="credits from the monthly free allowance"
          busy={diffbot.state.kind === "saving"}
          onConfirm={() => diffbot.run(() => api.POST("/holdings/{ticker}/company-research/diffbot", { params: { path: { ticker } } }), done("Diffbot research"), onChanged)}
        />
        <SaveNote state={diffbot.state} />
        <QuotaButton
          label="Company research from People Data Labs"
          provider="People Data Labs"
          cost="free-plan lookups, which are few"
          busy={pdl.state.kind === "saving"}
          onConfirm={() => pdl.run(() => api.POST("/holdings/{ticker}/company-research/pdl", { params: { path: { ticker } } }), done("People Data Labs research"), onChanged)}
        />
        <SaveNote state={pdl.state} />
        <QuotaButton
          label="Peer candidates and EPS surprises from Finnhub"
          provider="Finnhub"
          cost="calls against the free per-minute and daily limits"
          busy={finnhub.state.kind === "saving"}
          onConfirm={() => finnhub.run(() => api.POST("/holdings/{ticker}/financial-history/finnhub-research", { params: { path: { ticker } } }), done("Finnhub research"), onChanged)}
        />
        <SaveNote state={finnhub.state} />
      </div>

      <div className="space-y-2 border-t border-border-soft pt-3">
        <h3 className="text-base font-semibold">Alpha Vantage statements</h3>
        <p className="text-xs text-text-muted">Alpha Vantage&apos;s free key allows about 25 requests a day across everything.</p>
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Statement" className="w-56">
            <select className={SELECT_CLASS} value={statement} onChange={(e) => setStatement(e.target.value as typeof statement)}>
              {STATEMENTS.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          </Field>
          <QuotaButton
            label={`Fetch ${STATEMENTS.find((s) => s.id === statement)?.label.toLowerCase()}`}
            provider="Alpha Vantage"
            cost="1 request of the daily free quota"
            busy={avStmt.state.kind === "saving"}
            onConfirm={() => avStmt.run(() => api.POST("/holdings/{ticker}/financial-history/alpha-vantage/{statement}", { params: { path: { ticker, statement } } }), done("Statement"), onChanged)}
          />
        </div>
        <SaveNote state={avStmt.state} />
      </div>

      <div className="space-y-2 border-t border-border-soft pt-3">
        <h3 className="text-base font-semibold">Alpha Vantage commodity or FX series</h3>
        <p className="max-w-[68ch] text-xs text-text-muted">A price series this holding is exposed to, kept as context. It does not change what you hold.</p>
        <div className="grid gap-3 sm:grid-cols-4">
          <Field label="Series">
            <select className={SELECT_CLASS} value={fn} onChange={(e) => setFn(e.target.value as typeof fn)}>
              {AV_FUNCTIONS.map((f) => (
                <option key={f} value={f}>
                  {f.replaceAll("_", " ").toLowerCase()}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Name to store it under" error={touched ? avErrors.series : undefined}>
            <Input value={series} onChange={(e) => setSeries(e.target.value)} />
          </Field>
          {fn === "FX_DAILY" ? (
            <>
              <Field label="From currency" error={touched ? avErrors.from : undefined}>
                <Input value={from} onChange={(e) => setFrom(e.target.value)} maxLength={3} />
              </Field>
              <Field label="To currency" error={touched ? avErrors.to : undefined}>
                <Input value={to} onChange={(e) => setTo(e.target.value)} maxLength={3} />
              </Field>
            </>
          ) : null}
        </div>
        <QuotaButton
          label="Fetch series"
          provider="Alpha Vantage"
          cost="1 request of the daily free quota"
          busy={avExp.state.kind === "saving"}
          onConfirm={() => {
            setTouched(true);
            if (Object.keys(avErrors).length > 0) return;
            void avExp.run(
              () =>
                api.POST("/holdings/{ticker}/exposure-context/alpha-vantage", {
                  params: { path: { ticker } },
                  body: { function: fn, series_name: series.trim(), ...(fn === "FX_DAILY" ? { from_currency: from.trim().toUpperCase(), to_currency: to.trim().toUpperCase() } : {}) },
                }),
              done("Series"),
              onChanged,
            );
          }}
        />
        <SaveNote state={avExp.state} />
      </div>
    </Blueprint>
  );
}
