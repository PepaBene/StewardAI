"use client";

import { useState } from "react";

import { Absent, ResourceGate } from "@/components/company/primitives";
import { Field, Problem, problemText, SaveBar, SavedList, TEXTAREA } from "@/components/risk/common";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatMoney, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { buildShock, EMPTY_SHOCK_FORM, factorLabel, ladderShare, type ShockForm, type ShockResult, type ShockRun, type ShockSet } from "@/lib/risk/risk";
import { cn } from "@/lib/utils";

import { Notes, Pct, TD, TH, TH_LEFT, Toned, Warnings } from "../../xray/shared";

const fetchStandard = () => getBrowserApiClient().GET("/portfolio/scenarios/standard");

export function StressView() {
  const { state } = useApiResource(fetchStandard);
  return (
    <ResourceGate state={state} label="the standard scenarios">
      {(table) => <Workbench standard={table} />}
    </ResourceGate>
  );
}

type Standard = NonNullable<Awaited<ReturnType<typeof fetchStandard>>["data"]>;

function describeShocks(s: ShockSet): string {
  const parts: string[] = [];
  if (s.equity_pct != null) parts.push(`equities ${formatPercent(s.equity_pct, { signed: true, decimals: 0 })}`);
  if (s.rate_bp != null) parts.push(`rates ${s.rate_bp > 0 ? "+" : ""}${s.rate_bp} bp`);
  if (s.credit_bp != null) parts.push(`credit spreads ${s.credit_bp > 0 ? "+" : ""}${s.credit_bp} bp`);
  for (const [c, v] of Object.entries(s.fx ?? {})) parts.push(`${c} ${formatPercent(v, { signed: true, decimals: 0 })}`);
  for (const [c, v] of Object.entries(s.sector ?? {})) parts.push(`${c} ${formatPercent(v, { signed: true, decimals: 0 })}`);
  for (const [c, v] of Object.entries(s.country ?? {})) parts.push(`${c} ${formatPercent(v, { signed: true, decimals: 0 })}`);
  return parts.join(", ") || "no moves";
}

function Workbench({ standard }: { standard: Standard }) {
  const [form, setForm] = useState<ShockForm>(EMPTY_SHOCK_FORM);
  const [run, setRun] = useState<ShockRun | null>(null);
  const [label, setLabel] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const currency = standard.base_currency ?? "";
  const set = (key: keyof ShockForm) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [key]: e.target.value }));

  async function execute(body: { shocks: ShockSet; assumptions: Standard["assumptions"] }, title: string) {
    setBusy(true);
    setProblem(null);
    const res = await getBrowserApiClient().POST("/portfolio/scenarios/shock", { body });
    setBusy(false);
    if (!res.response.ok || !res.data) return setProblem(problemText(res.error, "The scenario could not be run."));
    setRun(res.data);
    setLabel(title);
  }

  function custom() {
    const built = buildShock(form);
    if ("error" in built) return setProblem(built.error);
    void execute(built.body, "Your scenario");
  }

  async function save(name: string): Promise<string | null> {
    if (!run) return "Run a scenario first.";
    const res = await getBrowserApiClient().POST("/portfolio/scenarios/shock/save", {
      body: { name, shocks: run.result.shocks, assumptions: run.result.assumptions },
    });
    return res.response.ok ? null : problemText(res.error, "The scenario could not be saved.");
  }

  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="p-4">
        <PanelHeading level={3} kicker="Standard scenarios" title="What these would cost today" />
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">Result of each shock on the portfolio</caption>
            <thead className="text-xs text-text-muted">
              <tr className="border-b border-border">
                <th scope="col" className={TH_LEFT}>Scenario</th>
                <th scope="col" className={TH}>Change</th>
                <th scope="col" className={TH}>{currency}</th>
                <th scope="col" className={TH}>Not moved</th>
                <th scope="col"><span className="sr-only">Detail</span></th>
              </tr>
            </thead>
            <tbody>
              {standard.lines.map((line) => (
                <tr key={line.name} className="border-b border-border-soft">
                  <td className="px-3 py-1.5">
                    <div className="font-medium">{line.name}</div>
                    <div className="text-xs text-text-muted">{describeShocks(line.shocks)}</div>
                  </td>
                  <td className={TD}>
                    <Toned value={line.pnl_pct}>{formatPercent(line.pnl_pct, { signed: true })}</Toned>
                  </td>
                  <td className={TD}>
                    <Toned value={line.pnl}>{formatMoney(line.pnl, currency, { signed: true, decimals: 0 })}</Toned>
                  </td>
                  <td className={TD}>
                    <Pct value={line.unshocked_weight} decimals={0} />
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => void execute({ shocks: line.shocks, assumptions: standard.assumptions }, line.name)}>
                      Break down
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs text-text-muted">“Not moved” is the share of the portfolio with no sensitivity to the scenario; it is left where it is, not guessed.</p>
        <div className="mt-3">
          <Notes notes={standard.notes} />
        </div>
      </Blueprint>

      <Blueprint className="p-4">
        <PanelHeading level={3} kicker="Your own" title="Build a scenario" />
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          <Field label="Equity market, %" hint="-20 means a fall of 20%">
            <Input inputMode="decimal" value={form.equity} onChange={set("equity")} placeholder="-20" />
          </Field>
          <Field label="Interest rates, bp" hint="+100 means one point higher">
            <Input inputMode="decimal" value={form.rate} onChange={set("rate")} placeholder="100" />
          </Field>
          <Field label="Credit spreads, bp" hint="+200 means wider">
            <Input inputMode="decimal" value={form.credit} onChange={set("credit")} placeholder="200" />
          </Field>
          <Field label="Currencies against your base, %" hint="One per line: USD -10">
            <textarea className={TEXTAREA} value={form.fx} onChange={set("fx")} placeholder={"USD -10\nGBP -5"} />
          </Field>
          <Field label="Sectors, % on top of the market" hint="One per line: Technology -15">
            <textarea className={TEXTAREA} value={form.sector} onChange={set("sector")} placeholder="Technology -15" />
          </Field>
          <Field label="Countries, % on top of the market" hint="One per line, ISO code: US -10">
            <textarea className={TEXTAREA} value={form.country} onChange={set("country")} placeholder="US -10" />
          </Field>
        </div>
        <details className="mt-3 border border-border-soft px-3 py-2 text-sm">
          <summary className="cursor-pointer font-medium">Assumptions for holdings without measured sensitivities</summary>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <Field label="Beta for equities with no measured one" hint="Blank leaves them unmoved and lists them">
              <Input inputMode="decimal" value={form.unmeasuredBeta} onChange={set("unmeasuredBeta")} />
            </Field>
            <Field label="Betas you set" hint="One per line: VUAA 0.95">
              <textarea className={TEXTAREA} value={form.betas} onChange={set("betas")} />
            </Field>
            <Field label="Duration in years, for rate moves" hint="One per line: IEAC 7.5. Recorded fund durations are used first.">
              <textarea className={TEXTAREA} value={form.durations} onChange={set("durations")} />
            </Field>
            <Field label="Spread duration in years, for credit moves" hint="One per line">
              <textarea className={TEXTAREA} value={form.spreadDurations} onChange={set("spreadDurations")} />
            </Field>
          </div>
        </details>
        <div className="mt-3 flex items-center gap-3">
          <Button type="button" onClick={custom} disabled={busy}>
            {busy ? "Running…" : "Run scenario"}
          </Button>
        </div>
        <div className="mt-3">
          <Problem>{problem}</Problem>
        </div>
      </Blueprint>

      {run ? (
        <Blueprint className="p-4">
          <PanelHeading level={2} kicker={label ?? "Scenario"} title="Where the loss comes from" />
          <Ladder result={run.result} currency={currency} />
          <div className="mt-4 space-y-3">
            <Sensitivities run={run} />
            <Notes notes={run.notes} />
            <div className="border-t border-border-soft pt-3">
              <SaveBar save={save} onSaved={() => setRefresh((n) => n + 1)} />
            </div>
          </div>
        </Blueprint>
      ) : null}

      <Blueprint className="p-4">
        <PanelHeading level={3} kicker="Kept" title="Saved scenarios" />
        <div className="mt-3">
          <SavedList
            kind="shock_scenario"
            refreshKey={refresh}
            onOpen={(payload, name) => {
              setRun(payload.result as ShockRun);
              setLabel(`${name} (as saved)`);
            }}
          />
        </div>
      </Blueprint>
    </div>
  );
}

/** The one distinctive element: a ladder of the loss, each rung a holding, with what was not moved at the foot. */
export function Ladder({ result, currency }: { result: ShockResult; currency: string }) {
  const rows = [...result.positions].sort((a, b) => a.pnl - b.pnl);
  const largest = Math.max(0, ...rows.map((r) => Math.abs(r.pnl)));
  const factors = Object.entries(result.by_factor).filter(([, v]) => Math.abs(v) > 0).sort((a, b) => a[1] - b[1]);
  return (
    <div className="mt-3 space-y-4">
      <Warnings warnings={result.warnings} />
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
        <p className="num text-3xl font-semibold">
          <Toned value={result.pnl}>{formatMoney(result.pnl, currency, { signed: true, decimals: 0 })}</Toned>
        </p>
        <p className="text-sm text-text-muted">
          <Toned value={result.pnl_pct}>{formatPercent(result.pnl_pct, { signed: true })}</Toned> of the portfolio, from{" "}
          {formatMoney(result.before_total, currency, { decimals: 0 })} to {formatMoney(result.after_total, currency, { decimals: 0 })}.
          {result.reconciles ? "" : " The parts do not add up to the total; treat this as unreliable."}
        </p>
      </div>
      <div
        className="flex h-3 w-full overflow-hidden border border-border"
        role="img"
        aria-label={`${formatPercent(result.shocked_weight, { decimals: 0 })} of the portfolio moved, ${formatPercent(result.unshocked_weight, { decimals: 0 })} not moved, ${formatPercent(result.cash_weight, { decimals: 0 })} cash`}
      >
        <span className="bg-accent" style={{ width: `${result.shocked_weight * 100}%` }} />
        <span className="bg-border-soft" style={{ width: `${result.cash_weight * 100}%` }} />
        <span className="bg-[repeating-linear-gradient(135deg,var(--border)_0_3px,transparent_3px_6px)]" style={{ width: `${result.unshocked_weight * 100}%` }} />
      </div>
      <p className="-mt-2 text-xs text-text-muted">
        Moved <Pct value={result.shocked_weight} decimals={0} />, cash <Pct value={result.cash_weight} decimals={0} />, hatched = not moved <Pct value={result.unshocked_weight} decimals={0} />.
      </p>

      {factors.length > 0 ? (
        <div>
          <h3 className="mb-1 text-sm font-medium">By cause</h3>
          <ul className="space-y-1">
            {factors.map(([key, v]) => (
              <Rung key={key} name={factorLabel(key)} value={v} largest={Math.max(...factors.map(([, x]) => Math.abs(x)))} currency={currency} />
            ))}
          </ul>
        </div>
      ) : null}

      <div>
        <h3 className="mb-1 text-sm font-medium">By holding</h3>
        <ul className="space-y-1">
          {rows.map((r) => (
            <Rung key={r.key} name={r.label} value={r.pnl} largest={largest} currency={currency} note={r.return_pct === null ? undefined : formatPercent(r.return_pct, { signed: true })} />
          ))}
        </ul>
      </div>

      {result.unshocked.length > 0 ? (
        <div>
          <h3 className="mb-1 text-sm font-medium">Not moved, and why</h3>
          <ul className="divide-y divide-border-soft border border-border-soft text-sm">
            {result.unshocked.map((u) => (
              <li key={u.label} className="flex flex-wrap items-baseline justify-between gap-2 px-3 py-1.5">
                <span>
                  {u.label} <span className="text-text-muted">{u.reason}</span>
                </span>
                <Pct value={u.weight} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function Rung({ name, value, largest, currency, note }: { name: string; value: number; largest: number; currency: string; note?: string }) {
  const share = ladderShare(value, largest) * 50;
  const loss = value < 0;
  return (
    <li className="grid grid-cols-[minmax(6rem,12rem)_1fr_auto] items-center gap-3 text-sm">
      <span className="truncate" title={name}>
        {name}
      </span>
      <span aria-hidden="true" className="relative h-4 border-x border-border-soft">
        <span className="absolute inset-y-0 left-1/2 w-px bg-border" />
        <span
          className={cn("absolute inset-y-1", loss ? "bg-loss" : "bg-gain")}
          style={loss ? { right: "50%", width: `${share}%` } : { left: "50%", width: `${share}%` }}
        />
      </span>
      <span className="num w-40 text-right">
        <Toned value={value}>{formatMoney(value, currency, { signed: true, decimals: 0 })}</Toned>
        {note ? <span className="ml-2 text-xs text-text-muted">{note}</span> : null}
      </span>
    </li>
  );
}

function Sensitivities({ run }: { run: ShockRun }) {
  if (run.measured.length === 0 && run.context.length === 0) return null;
  return (
    <details className="border border-border-soft px-3 py-2 text-sm">
      <summary className="cursor-pointer font-medium">Sensitivities used and where they came from</summary>
      {run.measured.length > 0 ? (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full">
            <caption className="sr-only">Sensitivity used for each holding and where it came from</caption>
            <thead className="text-xs text-text-muted">
              <tr className="border-b border-border">
                <th scope="col" className={TH_LEFT}>Holding</th>
                <th scope="col" className={TH_LEFT}>Measure</th>
                <th scope="col" className={TH}>Value</th>
                <th scope="col" className={TH_LEFT}>Source</th>
              </tr>
            </thead>
            <tbody>
              {run.measured.map((m) => (
                <tr key={`${m.ticker}-${m.kind}`} className="border-b border-border-soft align-top">
                  <td className="px-3 py-1.5">{m.label ?? m.ticker}</td>
                  <td className="px-3 py-1.5">{m.kind}</td>
                  <td className={TD}>{m.value === null ? <Absent reason={m.detail} /> : m.value.toFixed(2)}</td>
                  <td className="px-3 py-1.5 text-text-muted">
                    {m.source}. {m.detail}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {run.context.length > 0 ? (
        <div className="mt-3">
          <p className="font-medium">Where the market stands now</p>
          <ul className="mt-1 space-y-0.5 text-text-muted">
            {run.context.map((c) => (
              <li key={c.series_id}>
                {c.label}: <span className="num text-text">{c.value}</span> {c.unit ?? ""} ({c.period}, {c.source})
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </details>
  );
}
