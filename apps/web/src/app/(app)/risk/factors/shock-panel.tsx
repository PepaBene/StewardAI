"use client";

import { useState } from "react";

import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatMoney, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  buildSpec,
  describeSpec,
  SHOCK_FIELDS,
  type Scenario,
  type ShockFieldId,
  type ShockResult,
  type ShockSpec,
} from "@/lib/risk/shock";
import { cn } from "@/lib/utils";

type Region = "developed" | "europe" | "us";

const fetchScenarios = () => getBrowserApiClient().GET("/portfolio/risk/scenarios");

const inputClass =
  "h-9 w-full border border-border bg-surface px-2 text-sm num focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";
const buttonClass =
  "h-9 border border-border px-3 text-sm transition-colors hover:border-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50";

/** What if some factors moved? Runs a stated shock on today's loadings, and keeps named scenarios. */
export function ShockPanel({ region }: { region: Region }) {
  const [fields, setFields] = useState<Partial<Record<ShockFieldId, string>>>({ market: "-20" });
  const [fallback, setFallback] = useState("1");
  const [owner, setOwner] = useState<{ ticker: string; beta: string }[]>([]);
  const [name, setName] = useState("");
  const [result, setResult] = useState<ShockResult | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const { state: scenarioState, reload: loadScenarios } = useApiResource(fetchScenarios);
  const scenarios: Scenario[] = scenarioState.kind === "ok" ? scenarioState.data : [];

  const run = async (spec: ShockSpec, scenarioId?: number) => {
    setBusy(true);
    setMessage(null);
    try {
      const res = scenarioId
        ? await getBrowserApiClient().POST("/portfolio/risk/scenarios/{scenario_id}/run", {
            params: { path: { scenario_id: scenarioId }, query: { region } },
          })
        : await getBrowserApiClient().POST("/portfolio/risk/factor-shock", { params: { query: { region } }, body: spec });
      if (res.response.ok && res.data) setResult(res.data);
      else setMessage("The shock could not be run. Check that the factor data is loaded and try again.");
    } catch {
      setMessage("The backend could not be reached.");
    } finally {
      setBusy(false);
    }
  };

  const submit = async (save: boolean) => {
    const { spec, problems: found } = buildSpec(fields, fallback, owner);
    setProblems(found);
    if (found.length > 0) return;
    if (save) {
      if (name.trim() === "") {
        setProblems(["Give the scenario a name to save it."]);
        return;
      }
      const saved = await getBrowserApiClient().POST("/portfolio/risk/scenarios", { body: { name: name.trim(), spec } });
      if (!saved.response.ok) {
        setMessage("The scenario could not be saved.");
        return;
      }
      setMessage(`Saved “${name.trim()}”.`);
      loadScenarios();
    }
    await run(spec);
  };

  const remove = async (s: Scenario) => {
    const res = await getBrowserApiClient().DELETE("/portfolio/risk/scenarios/{scenario_id}", { params: { path: { scenario_id: s.id } } });
    if (res.response.ok) {
      setMessage(`Deleted “${s.name}”. Its saved runs are kept.`);
      loadScenarios();
    }
  };

  const load = (s: Scenario) => {
    const next: Partial<Record<ShockFieldId, string>> = {};
    for (const f of SHOCK_FIELDS) {
      const v = s.spec[f.id];
      if (typeof v === "number") next[f.id] = String(f.unit === "%" ? Math.round(v * 1000) / 10 : v);
    }
    setFields(next);
    setFallback(s.spec.unmeasured_beta == null ? "" : String(s.spec.unmeasured_beta));
    setOwner(Object.entries(s.spec.owner_betas ?? {}).map(([ticker, beta]) => ({ ticker, beta: String(beta) })));
    setName(s.name);
  };

  return (
    <Blueprint className="p-4">
      <PanelHeading level={3} kicker="What if" title="Move the factors and see what your holdings do" />
      <p className="mt-1 max-w-prose text-sm text-text-muted">
        Type how far each factor moves; leave a field empty to keep it still. Each holding moves by its own loadings above. Large moves are an
        extrapolation of what the last two years showed.
      </p>
      <form
        className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
        onSubmit={(e) => {
          e.preventDefault();
          void submit(false);
        }}
      >
        {SHOCK_FIELDS.map((f) => (
          <label key={f.id} className="flex flex-col gap-1 text-sm">
            <span className="font-medium">
              {f.label} <span className="font-normal text-text-muted">({f.unit})</span>
            </span>
            <input
              inputMode="decimal"
              className={inputClass}
              value={fields[f.id] ?? ""}
              onChange={(e) => setFields({ ...fields, [f.id]: e.target.value })}
              aria-describedby={`${f.id}-hint`}
            />
            <span id={`${f.id}-hint`} className="text-xs text-text-muted">
              {f.hint}
            </span>
          </label>
        ))}
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">Beta for holdings with no loadings</span>
          <input inputMode="decimal" className={inputClass} value={fallback} onChange={(e) => setFallback(e.target.value)} aria-describedby="fallback-hint" />
          <span id="fallback-hint" className="text-xs text-text-muted">Empty leaves them unchanged and lists them.</span>
        </label>
        <fieldset className="flex flex-col gap-2 sm:col-span-2 lg:col-span-3">
          <legend className="text-sm font-medium">Your own market beta for a holding (replaces the measured one)</legend>
          {owner.map((row, i) => (
            <div key={i} className="flex gap-2">
              <input aria-label="Holding ticker" placeholder="Ticker" className={cn(inputClass, "max-w-40")} value={row.ticker} onChange={(e) => setOwner(owner.map((r, j) => (j === i ? { ...r, ticker: e.target.value } : r)))} />
              <input aria-label="Market beta" placeholder="Beta" inputMode="decimal" className={cn(inputClass, "max-w-24")} value={row.beta} onChange={(e) => setOwner(owner.map((r, j) => (j === i ? { ...r, beta: e.target.value } : r)))} />
              <button type="button" className={buttonClass} onClick={() => setOwner(owner.filter((_, j) => j !== i))}>
                Remove
              </button>
            </div>
          ))}
          <div>
            <button type="button" className={buttonClass} onClick={() => setOwner([...owner, { ticker: "", beta: "" }])}>
              Add a holding
            </button>
          </div>
        </fieldset>
        <div className="flex flex-wrap items-end gap-2 sm:col-span-2 lg:col-span-3">
          <button type="submit" className={cn(buttonClass, "border-accent font-semibold")} disabled={busy}>
            Run shock
          </button>
          <label className="flex flex-col gap-1 text-sm">
            <span className="sr-only">Scenario name</span>
            <input className={cn(inputClass, "w-56")} placeholder="Name to save it" value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <button type="button" className={buttonClass} disabled={busy} onClick={() => void submit(true)}>
            Save scenario and run
          </button>
        </div>
      </form>
      {problems.length > 0 ? (
        <ul role="alert" className="mt-3 list-disc space-y-1 pl-5 text-sm text-warning-signal">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      ) : null}
      {message ? (
        <p role="status" className="mt-3 text-sm text-text-muted">
          {message}
        </p>
      ) : null}
      {scenarios.length > 0 ? (
        <div className="mt-4">
          <h4 className="text-xs font-medium text-text-muted">Saved scenarios</h4>
          <ul className="mt-1">
            {scenarios.map((s) => (
              <li key={s.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border-soft py-2 text-sm last:border-b-0">
                <span className="min-w-0 flex-1">
                  <span className="font-semibold">{s.name}</span>
                  <span className="block text-xs text-text-muted">{describeSpec(s.spec)}</span>
                </span>
                <button type="button" className={buttonClass} disabled={busy} onClick={() => void run(s.spec, s.id)}>
                  Run
                </button>
                <button type="button" className={buttonClass} onClick={() => load(s)}>
                  Edit
                </button>
                <button type="button" className={buttonClass} onClick={() => void remove(s)}>
                  Delete
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {result ? <ShockOutcome result={result} /> : null}
    </Blueprint>
  );
}

function ShockOutcome({ result }: { result: ShockResult }) {
  const currency = result.base_currency ?? "CZK";
  if (result.loss === null || result.loss === undefined) {
    return <p className="mt-4 text-sm text-text-muted">{result.reason ?? "Nothing to show."}</p>;
  }
  return (
    <div className="mt-4 flex flex-col gap-3 border-t border-border pt-4">
      <p className="text-sm">
        <span className="num text-2xl font-semibold">{formatPercent(result.loss, { signed: true })}</span>{" "}
        <span className="num text-text-muted">{formatMoney(result.pnl, currency, { decimals: 0, signed: true })}</span> on the whole portfolio.
        {result.breaches_limit && result.drawdown_limit != null ? (
          <span className="text-warning-signal"> That reaches your {formatPercent(result.drawdown_limit, { decimals: 0 })} drawdown limit, which asks for a review.</span>
        ) : null}
      </p>
      {result.unshocked_weight > 0.0005 ? (
        <p className="text-sm text-text-muted">{formatPercent(result.unshocked_weight, { decimals: 0 })} of the portfolio has no loadings and was left unchanged.</p>
      ) : null}
      {result.warnings.map((w) => (
        <p key={w} className="text-xs text-text-muted">
          {w}
        </p>
      ))}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">Each holding’s move in the shock and what drove it</caption>
          <thead>
            <tr className="border-b border-border text-left">
              <th scope="col" className="py-1.5 pr-3 font-medium">Holding</th>
              <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Move</th>
              <th scope="col" className="num py-1.5 pr-3 text-right font-medium">{currency}</th>
              <th scope="col" className="py-1.5 font-medium">Mostly because of</th>
            </tr>
          </thead>
          <tbody>
            {result.positions.map((p) => (
              <tr key={p.ticker} className="border-b border-border-soft last:border-b-0">
                <th scope="row" className="py-1.5 pr-3 text-left font-semibold">{p.ticker}</th>
                <td className="num py-1.5 pr-3 text-right">{p.move === null || p.move === undefined ? "—" : formatPercent(p.move)}</td>
                <td className="num py-1.5 pr-3 text-right">{p.basis === "unshocked" ? "—" : formatMoney(p.pnl, currency, { decimals: 0, signed: true })}</td>
                <td className="py-1.5 text-text-muted">
                  {p.basis === "unshocked"
                    ? "no loadings"
                    : [...p.drivers.slice(0, 2).map((d) => `${d.label} ${formatPercent(d.contribution, { signed: true })}`), p.basis === "owner" ? "your beta" : p.basis === "fallback" ? "fallback beta" : ""]
                        .filter(Boolean)
                        .join(", ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
