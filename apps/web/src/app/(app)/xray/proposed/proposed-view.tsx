"use client";

import { PlusIcon, XIcon } from "lucide-react";
import { useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatMoney, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  applyScenario,
  buildProposal,
  cashRemainder,
  pointsLabel,
  type AddRow,
  type CandidateData,
  type EditRow,
  type ProposalData,
  type ProposedRequest,
  type XrayData,
} from "@/lib/xray/xray";

import { Notes, TD, TH, TH_LEFT, Toned } from "../shared";
import { SavedScenarios } from "./saved-scenarios";

const fetchXray = () => getBrowserApiClient().GET("/portfolio/xray");

export function ProposedView() {
  const { state } = useApiResource(fetchXray);
  return (
    <ResourceGate state={state} label="holdings">
      {(data) => <Builder data={data} />}
    </ResourceGate>
  );
}

type Added = AddRow & { name: string | null; looked_through: boolean | null };

const initialText = (weight: number) => (weight * 100).toFixed(3);

function Builder({ data }: { data: XrayData }) {
  const [texts, setTexts] = useState<Record<string, string>>(() =>
    Object.fromEntries(data.holdings.map((h) => [h.ticker, initialText(h.weight)])),
  );
  const [added, setAdded] = useState<Added[]>([]);
  const [result, setResult] = useState<ProposalData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const rows: EditRow[] = data.holdings.map((h) => ({ ticker: h.ticker, current: h.weight, text: texts[h.ticker] ?? "" }));
  const cash = cashRemainder(rows, added);
  const currency = data.base_currency ?? "";

  function reset() {
    setTexts(Object.fromEntries(data.holdings.map((h) => [h.ticker, initialText(h.weight)])));
    setAdded([]);
    setResult(null);
    setError(null);
  }

  const built = buildProposal(rows, added);
  const currentRequest = built.ok ? built.request : null;

  function openScenario(scenario: ProposedRequest, label: string) {
    const loaded = applyScenario(data.holdings, scenario);
    setTexts(loaded.texts);
    setAdded(loaded.added.map((a) => ({ ...a, name: null, looked_through: null })));
    setResult(null);
    setError(
      loaded.missing.length > 0
        ? `"${label}" mentions ${loaded.missing.join(", ")}, which you no longer hold. Those weights were left out.`
        : null,
    );
  }

  async function run() {
    if (!built.ok) {
      setError(built.error);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await getBrowserApiClient().POST("/portfolio/xray/proposed", { body: built.request });
      if (res.response.ok && res.data) {
        setResult(res.data);
      } else {
        const detail = res.error && typeof res.error === "object" && "detail" in res.error ? String(res.error.detail) : `HTTP ${res.response.status}`;
        setError(detail);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="px-[22px] py-4">
        <PanelHeading kicker="What if" title="Try different weights without touching your account" />
        <p className="mt-1 max-w-3xl text-sm text-text-muted">
          Change a weight or add a security from your watchlist and see the exposure, overlap and fees before and after. This works on a copy:
          nothing in your Trading212 data is changed. Cash is whatever the weights leave over.
        </p>

        <div className="mt-4 overflow-x-auto border border-border">
          <table className="w-full text-sm">
            <caption className="sr-only">Your holdings with an editable proposed weight</caption>
            <thead>
              <tr className="bg-surface-muted">
                <th scope="col" className={TH_LEFT}>Holding</th>
                <th scope="col" className={TH}>Now</th>
                <th scope="col" className={TH}>Proposed %</th>
              </tr>
            </thead>
            <tbody>
              {data.holdings.map((h) => (
                <tr key={h.ticker} className="border-t border-border-soft">
                  <th scope="row" className="px-3 py-1.5 text-left font-normal">
                    {h.name ?? h.ticker}
                    <span className="block text-[11px] text-text-muted">
                      {h.ticker}
                      {h.is_fund ? (h.looked_through ? " · fund, opened up" : " · fund, holdings not stored") : ""}
                    </span>
                  </th>
                  <td className={TD}>{formatPercent(h.weight, { decimals: 1 })}</td>
                  <td className="px-3 py-1.5 text-right">
                    <WeightInput
                      label={`Proposed weight of ${h.name ?? h.ticker}, percent`}
                      value={texts[h.ticker] ?? ""}
                      onChange={(v) => setTexts((t) => ({ ...t, [h.ticker]: v }))}
                    />
                  </td>
                </tr>
              ))}
              {added.map((a) => (
                <tr key={a.ticker} className="border-t border-border-soft bg-accent-soft/40">
                  <th scope="row" className="px-3 py-1.5 text-left font-normal">
                    {a.name ?? a.ticker}
                    <span className="block text-[11px] text-text-muted">
                      {a.ticker} · added
                      {a.looked_through === false ? " · fund, holdings not stored: its weight will be unknown" : ""}
                    </span>
                  </th>
                  <td className={TD}>
                    <button
                      type="button"
                      onClick={() => setAdded((list) => list.filter((x) => x.ticker !== a.ticker))}
                      className="inline-flex items-center gap-1 text-xs text-text-muted hover:text-text focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none"
                    >
                      <XIcon aria-hidden="true" className="size-3.5" /> Remove
                    </button>
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    <WeightInput
                      label={`Proposed weight of ${a.name ?? a.ticker}, percent`}
                      value={a.text}
                      onChange={(v) => setAdded((list) => list.map((x) => (x.ticker === a.ticker ? { ...x, text: v } : x)))}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-border bg-surface-muted">
                <th scope="row" className="px-3 py-1.5 text-left font-medium">Cash (what is left)</th>
                <td className={TD}>{formatPercent(data.cash_weight, { decimals: 1 })}</td>
                <td className={`${TD} ${cash !== null && cash < -0.00005 ? "text-loss" : ""}`}>
                  {cash === null ? "—" : `${cash < 0 ? "−" : ""}${Math.abs(cash * 100).toFixed(1)}%`}
                  {cash !== null && cash < -0.00005 ? <span className="block text-[11px]">over 100%</span> : null}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>

        <AddSecurity added={added} held={data.holdings.map((h) => h.ticker)} onAdd={(a) => setAdded((list) => [...list, a])} />

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={run}
            disabled={busy}
            className="inline-flex h-9 items-center bg-accent px-4 text-sm font-medium text-accent-foreground hover:bg-accent-strong disabled:opacity-60 focus-visible:ring-2 focus-visible:ring-focus/40 focus-visible:outline-none"
          >
            {busy ? "Working…" : "Show before and after"}
          </button>
          <button
            type="button"
            onClick={reset}
            className="inline-flex h-9 items-center border border-border px-4 text-sm hover:bg-surface-muted focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none"
          >
            Reset to my holdings
          </button>
        </div>
        {error ? (
          <div className="mt-3">
            <AlertMessage title="Cannot run this yet">{error}</AlertMessage>
          </div>
        ) : null}
      </Blueprint>

      <SavedScenarios request={currentRequest} onOpen={openScenario} />

      {result ? <Result data={result} currency={currency} /> : null}
    </div>
  );
}

function WeightInput({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <input
      type="text"
      inputMode="decimal"
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="num h-8 w-24 border border-border bg-surface px-2 text-right text-sm focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none"
    />
  );
}

function AddSecurity({ added, held, onAdd }: { added: Added[]; held: string[]; onAdd: (a: Added) => void }) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<CandidateData[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function search(q: string | null) {
    setLoading(true);
    setProblem(null);
    try {
      const res = await getBrowserApiClient().GET("/portfolio/xray/candidates", { params: { query: q ? { q } : {} } });
      if (res.response.ok && res.data) setItems(res.data.items);
      else setProblem(`The search failed (HTTP ${res.response.status}).`);
    } catch (err) {
      setProblem(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  const taken = new Set([...held, ...added.map((a) => a.ticker)]);
  return (
    <div className="mt-5 border-t border-border-soft pt-4">
      <p className="text-sm font-medium">Add a security</p>
      <p className="text-xs text-text-muted">From your watchlist, or search the investable universe by ticker or name.</p>
      <form
        className="mt-2 flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void search(query.trim() || null);
        }}
      >
        <label htmlFor="xray-search" className="sr-only">Search securities</label>
        <input
          id="xray-search"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Ticker or name"
          className="h-9 w-56 border border-border bg-surface px-2 text-sm focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none"
        />
        <button type="submit" disabled={loading} className="inline-flex h-9 items-center border border-border px-3 text-sm hover:bg-surface-muted disabled:opacity-60">
          {query.trim() ? "Search" : "Show my watchlist"}
        </button>
      </form>
      {problem ? <p className="mt-2 text-sm text-loss">{problem}</p> : null}
      {items ? (
        items.length === 0 ? (
          <p className="mt-2 text-sm text-text-muted">Nothing found. A security must be in the investable universe to be added.</p>
        ) : (
          <ul className="mt-2 divide-y divide-border-soft border border-border">
            {items.map((c) => {
              const disabled = taken.has(c.ticker);
              return (
                <li key={c.ticker} className="flex flex-wrap items-center justify-between gap-2 px-3 py-1.5 text-sm">
                  <span>
                    {c.name ?? c.ticker}
                    <span className="ml-2 text-xs text-text-muted">
                      {c.ticker}
                      {c.source === "watchlist" ? ` · watchlist${c.list_name ? ` (${c.list_name})` : ""}` : ""}
                      {c.looked_through === false ? " · fund, holdings not stored" : ""}
                    </span>
                  </span>
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() => onAdd({ ticker: c.ticker, name: c.name ?? null, looked_through: c.looked_through ?? null, text: "5" })}
                    className="inline-flex items-center gap-1 text-accent-ink hover:underline disabled:text-text-faint disabled:no-underline"
                  >
                    <PlusIcon aria-hidden="true" className="size-3.5" />
                    {c.held ? "Held" : disabled ? "Added" : "Add"}
                  </button>
                </li>
              );
            })}
          </ul>
        )
      ) : null}
    </div>
  );
}

type SideKey = keyof ProposalData["before"];

function pctRow(label: string, key: SideKey, data: ProposalData, note?: string) {
  const b = data.before[key] as number | null;
  const a = data.after[key] as number | null;
  return { label, b, a, note, kind: "pct" as const };
}

function Result({ data, currency }: { data: ProposalData; currency: string }) {
  const [lens, setLens] = useState(data.views.find((v) => v.dimension === "sector")?.dimension ?? data.views[0]?.dimension);
  const view = data.views.find((v) => v.dimension === lens);
  const effective = { label: "Effective number of issuers", b: data.before.effective_issuers, a: data.after.effective_issuers, kind: "num" as const };
  const summary = [
    pctRow("Cash", "cash_weight", data),
    pctRow("Equity identified", "equity_weight", data),
    pctRow("Unknown fund weight", "unknown_weight", data),
    pctRow("Funds not opened up", "not_looked_through_weight", data),
    pctRow("Ten largest issuers", "top10_weight", data),
    effective,
    { label: "Issuers in 2+ channels", b: data.before.shared_issuers, a: data.after.shared_issuers, kind: "int" as const },
    pctRow("Redundant exposure", "redundant_weight", data, "Beyond the largest single channel"),
    pctRow("Fund fees a year", "fee_drag", data, "Funds with a known charge only"),
    pctRow("Earnings yield", "earnings_yield", data, `covers ${formatPercent(data.after.earnings_yield_coverage, { decimals: 0 })} of equity after`),
    pctRow("Dividend yield", "dividend_yield", data, `covers ${formatPercent(data.after.dividend_yield_coverage, { decimals: 0 })} of equity after`),
  ];
  const fmt = (kind: "pct" | "num" | "int", v: number | null) => {
    if (v === null || v === undefined) return "—";
    if (kind === "pct") return formatPercent(v, { decimals: 1 });
    if (kind === "int") return String(v);
    return v.toFixed(1);
  };
  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="px-[22px] py-4">
        <PanelHeading
          kicker="Before and after"
          title="What changes"
          action={<span className="border border-border px-2 py-0.5 text-xs text-text-muted">Your saved holdings are unchanged</span>}
        />
        <p className="mt-1 text-sm text-text-muted">
          Total value held at {currency ? formatMoney(data.before.total_value, currency, { decimals: 0 }) : data.before.total_value}.
        </p>
        <div className="mt-3 overflow-x-auto border border-border">
          <table className="w-full text-sm">
            <caption className="sr-only">Portfolio figures before and after the proposed weights</caption>
            <thead>
              <tr className="bg-surface-muted">
                <th scope="col" className={TH_LEFT}>Measure</th>
                <th scope="col" className={TH}>Now</th>
                <th scope="col" className={TH}>Proposed</th>
                <th scope="col" className={TH}>Change</th>
              </tr>
            </thead>
            <tbody>
              {summary.map((r) => {
                const delta = r.a !== null && r.b !== null && r.a !== undefined && r.b !== undefined ? r.a - r.b : null;
                return (
                  <tr key={r.label} className="border-t border-border-soft align-top">
                    <th scope="row" className="px-3 py-1.5 text-left font-normal">
                      {r.label}
                      {"note" in r && r.note ? <span className="block text-[11px] text-text-muted">{r.note}</span> : null}
                    </th>
                    <td className={TD}>{fmt(r.kind, r.b ?? null)}</td>
                    <td className={TD}>{fmt(r.kind, r.a ?? null)}</td>
                    <td className={TD}>
                      {delta === null ? "—" : r.kind === "pct" ? <Toned value={0}>{pointsLabel(delta, 2)}</Toned> : <span>{delta > 0 ? "+" : delta < 0 ? "−" : ""}{Math.abs(delta).toFixed(r.kind === "int" ? 0 : 1)}</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {data.coverage.length > 0 ? (
          <div className="mt-3">
            <AlertMessage tone="info" title="Where the two sides know different amounts">
              <ul className="list-disc space-y-0.5 pl-4">
                {data.coverage.map((c) => (
                  <li key={c}>{c}</li>
                ))}
              </ul>
            </AlertMessage>
          </div>
        ) : null}
      </Blueprint>

      <Blueprint className="px-[22px] py-4">
        <PanelHeading kicker="Exposure" title="Before and after by lens" />
        <div className="mt-3">
          <label htmlFor="proposed-lens" className="text-sm">Lens </label>
          <select
            id="proposed-lens"
            value={lens}
            onChange={(e) => setLens(e.target.value)}
            className="h-9 border border-border bg-surface px-2 text-sm"
          >
            {data.views.map((v) => (
              <option key={v.dimension} value={v.dimension}>{v.label}</option>
            ))}
          </select>
        </div>
        {view ? (
          <div className="mt-3 overflow-x-auto border border-border">
            <table className="w-full text-sm">
              <caption className="sr-only">{view.label} before and after</caption>
              <thead>
                <tr className="bg-surface-muted">
                  <th scope="col" className={TH_LEFT}>{view.label}</th>
                  <th scope="col" className={TH}>Now</th>
                  <th scope="col" className={TH}>Proposed</th>
                  <th scope="col" className={TH}>Change</th>
                </tr>
              </thead>
              <tbody>
                {view.rows.map((r) => (
                  <tr key={r.key} className="border-t border-border-soft">
                    <th scope="row" className="px-3 py-1.5 text-left font-normal">{r.label}</th>
                    <td className={TD}>{formatPercent(r.before, { decimals: 1 })}</td>
                    <td className={TD}>{formatPercent(r.after, { decimals: 1 })}</td>
                    <td className={TD}>{pointsLabel(r.delta)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Blueprint>

      <Blueprint className="px-[22px] py-4">
        <PanelHeading kicker="Issuers" title="Who gains and loses weight" />
        <ul className="mt-3 space-y-1 text-sm">
          {data.issuers.filter((i) => Math.abs(i.delta) > 0.00005).map((i) => (
            <li key={i.key} className="flex justify-between gap-3">
              <span>{i.label}</span>
              <span className="num">
                {formatPercent(i.before, { decimals: 2 })} → {formatPercent(i.after, { decimals: 2 })}
                <span className="ml-2"><Toned value={i.delta}>{pointsLabel(i.delta, 2)}</Toned></span>
              </span>
            </li>
          ))}
        </ul>
      </Blueprint>
      <Notes notes={data.notes} />
    </div>
  );
}
