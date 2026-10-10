"use client";

import { LoaderCircleIcon } from "lucide-react";
import { useState } from "react";

import { Problem, problemText } from "@/components/risk/common";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  actionText,
  gateText,
  improvesText,
  missingText,
  scoreText,
  stageLabel,
  type RankingResponse,
  type RankingRow,
  type SwitchLine,
  type SwitchResponse,
} from "@/lib/risk/ranking";

const fetchRanking = () => getBrowserApiClient().GET("/theses/expected-returns");

/** Every thesis, held or on the bench, ranked by risk-adjusted expected return, with the switches it suggests. */
export function RankingSection() {
  const { state } = useApiResource(fetchRanking);
  const ranking = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;

  return (
    <Blueprint className="p-4">
      <PanelHeading level={2} kicker="Decision loop" title="Ideas ranked by risk-adjusted return" />
      {state.kind === "loading" ? (
        <p className="mt-3 flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Ranking your theses…
        </p>
      ) : null}
      {state.kind === "error" ? <p className="mt-3 text-sm text-text">The ranking could not be loaded: {state.detail}</p> : null}
      {ranking ? <Ranking ranking={ranking} /> : null}
    </Blueprint>
  );
}

function Ranking({ ranking }: { ranking: RankingResponse }) {
  const held = ranking.rows.filter((r) => r.held).length;
  return (
    <div className="mt-2 flex flex-col gap-4">
      <p className="max-w-prose text-sm text-text-muted">{ranking.formula}</p>
      <p className="num text-sm text-text-muted">
        Cash rate {ranking.cash_rate == null ? "not stored" : formatPercent(ranking.cash_rate)}
        {ranking.cash_rate_label ? ` (${ranking.cash_rate_label})` : ""} · portfolio Sharpe ratio {scoreText(ranking.portfolio_sharpe)} · {held} held, {ranking.bench_count} on the bench
        (target {ranking.bench_target})
      </p>
      {(ranking.notes ?? []).map((n) => (
        <p key={n} className="text-sm text-text">
          {n}
        </p>
      ))}
      {ranking.rows.length > 0 ? <RankingTable rows={ranking.rows} /> : null}
      <Switches />
    </div>
  );
}

function RankingTable({ rows }: { rows: RankingRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">Theses ranked by risk-adjusted expected return</caption>
        <thead>
          <tr className="border-b border-border text-left">
            <th scope="col" className="py-1.5 pr-3 font-medium">#</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">Stock</th>
            <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Expected return</th>
            <th scope="col" className="num py-1.5 pr-3 text-right font-medium">A year</th>
            <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Volatility</th>
            <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Score</th>
            <th scope="col" className="py-1.5 font-medium">Against the book</th>
          </tr>
        </thead>
        {rows.map((r) => (
          <RankingRowView key={r.ticker} row={r} />
        ))}
      </table>
    </div>
  );
}

function RankingRowView({ row }: { row: RankingRow }) {
  const [open, setOpen] = useState(false);
  const gap = missingText(row);
  return (
    <tbody className="border-b border-border-soft last:border-b-0">
      <tr>
        <td className="num py-1.5 pr-3">{row.rank ?? "—"}</td>
        <th scope="row" className="py-1.5 pr-3 text-left font-semibold">
          <button
            type="button"
            className="text-left underline-offset-4 hover:underline focus-visible:underline"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
          >
            {row.ticker}
          </button>
          <span className="ml-1.5 text-xs font-normal text-text-muted">{row.held ? "held" : "bench"}</span>
        </th>
        <td className="num py-1.5 pr-3 text-right">{formatPercent(row.returns?.total, { signed: true })}</td>
        <td className="num py-1.5 pr-3 text-right">{formatPercent(row.returns?.annualized, { signed: true })}</td>
        <td className="num py-1.5 pr-3 text-right">{formatPercent(row.risk?.volatility_1y)}</td>
        <td className="num py-1.5 pr-3 text-right font-semibold">{scoreText(row.score)}</td>
        <td className="py-1.5 text-text-muted">{gap ? "No score yet" : improvesText(row.improves_portfolio)}</td>
      </tr>
      {open ? (
        <tr>
          <td />
          <td colSpan={6} className="pb-3 text-sm">
            <Drilldown row={row} />
          </td>
        </tr>
      ) : null}
    </tbody>
  );
}

/** The formula and its inputs for one row. */
function Drilldown({ row }: { row: RankingRow }) {
  const r = row.returns;
  const k = row.risk;
  const s = row.scenarios;
  return (
    <div className="flex flex-col gap-1.5 text-text-muted">
      {missingText(row) ? <p className="text-text">{missingText(row)}</p> : null}
      {s ? (
        <p className="num">
          Scenarios {s.bear} / {s.base} / {s.bull} {row.currency ?? ""} at {formatPercent(s.p_bear, { decimals: 0 })} / {formatPercent(s.p_base, { decimals: 0 })} /{" "}
          {formatPercent(s.p_bull, { decimals: 0 })}
          {r ? `, worth ${r.expected_value.toFixed(2)} against a price of ${row.price?.toFixed(2) ?? "—"}${row.price_date ? ` on ${formatDate(row.price_date)}` : ""}` : ""}.
        </p>
      ) : null}
      {r ? (
        <p className="num">
          Over {r.horizon_years.toFixed(1)} years ({r.horizon_source}): {formatPercent(r.total, { signed: true })} in total, {formatPercent(r.annualized, { signed: true })} a year. Base-case upside{" "}
          {formatPercent(r.upside, { signed: true })} against a bear-case loss of {formatPercent(r.loss)}
          {r.asymmetry == null ? "" : `, ${r.asymmetry.toFixed(1)} to 1`}. {gateText(row)}.
        </p>
      ) : null}
      {k ? (
        <p className="num">
          Volatility {formatPercent(k.volatility_1y)} over a year, {formatPercent(k.volatility_3m)} over three months; worst fall {formatPercent(k.max_drawdown)}; correlation with the portfolio{" "}
          {k.correlation == null ? "—" : k.correlation.toFixed(2)}; marginal risk {k.marginal_risk == null ? "—" : k.marginal_risk.toFixed(2)}
          {k.marginal_stage ? ` (${stageLabel(k.marginal_stage)})` : ""}. {k.observations} closes
          {k.window_start && k.window_end ? ` from ${formatDate(k.window_start)} to ${formatDate(k.window_end)}` : ""}; figures are {stageLabel(k.stage)}.
        </p>
      ) : null}
      {row.bear_loss_vs_history ? <p>{row.bear_loss_vs_history}</p> : null}
      {row.volatility_band != null ? (
        <p className="num">
          The thesis expects volatility around {formatPercent(row.volatility_band, { decimals: 0 })}; a review opens when the last three months run well above it.
        </p>
      ) : null}
      {row.earnings_moves ? (
        <p className="num">
          Around {row.earnings_moves.count} past result{row.earnings_moves.count === 1 ? "" : "s"}, the price moved a median {formatPercent(row.earnings_moves.median_abs)} and up to{" "}
          {formatPercent(row.earnings_moves.largest_abs)} (on {formatDate(row.earnings_moves.largest_date)}). Size a position down ahead of a binary event.
        </p>
      ) : null}
    </div>
  );
}

function Switches() {
  const [result, setResult] = useState<SwitchResponse | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    setBusy(true);
    setProblem(null);
    const res = await getBrowserApiClient().GET("/theses/switches");
    setBusy(false);
    if (!res.response.ok || !res.data) return setProblem(problemText(res.error, "The switches could not be checked."));
    setResult(res.data);
  }

  return (
    <div className="flex flex-col gap-3 border-t border-border-soft pt-3">
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" variant="outline" onClick={() => void load()} disabled={busy}>
          {busy ? "Checking switches…" : "Check switches"}
        </Button>
        <p className="max-w-prose text-sm text-text-muted">
          For each holding, the best-ranked idea that beats it, against the tax on the lots sold, costs and your safety margin. Flags only: nothing is traded.
        </p>
      </div>
      <Problem>{problem}</Problem>
      {result ? <SwitchList result={result} /> : null}
    </div>
  );
}

function SwitchList({ result }: { result: SwitchResponse }) {
  return (
    <div className="flex flex-col gap-3">
      {result.lines.length === 0 ? <p className="text-sm text-text-muted">No idea beats a holding yet.</p> : null}
      {(result.notes ?? []).map((n) => (
        <p key={n} className="text-sm text-text-muted">
          {n}
        </p>
      ))}
      <ul className="flex flex-col gap-3">
        {result.lines.map((line) => (
          <SwitchCard key={`${line.sell}-${line.buy}`} line={line} />
        ))}
      </ul>
      <p className="text-xs text-text-muted">{result.disclaimer}</p>
    </div>
  );
}

function SwitchCard({ line }: { line: SwitchLine }) {
  const [state, setState] = useState<"idle" | "busy" | "done">("idle");
  const [problem, setProblem] = useState<string | null>(null);

  async function record() {
    setState("busy");
    setProblem(null);
    const res = await getBrowserApiClient().POST("/theses/switches/log", {
      body: {
        sold_ticker: line.sell,
        bought_ticker: line.buy,
        sold_value_base: line.sell_value,
        bought_value_base: line.buy_value > 0 ? line.buy_value : null,
        line: line as unknown as Record<string, unknown>,
      },
    });
    if (!res.response.ok) {
      setState("idle");
      return setProblem(problemText(res.error, "The switch could not be logged."));
    }
    setState("done");
  }

  return (
    <li className="border-l-2 border-border py-0.5 pl-3 text-sm">
      <p className="font-semibold">
        {actionText(line)}
        {line.flagged ? <span className="ml-2 text-xs font-normal">Suggested</span> : null}
      </p>
      <p className="num text-text-muted">
        Expected return {formatPercent(line.idea_return, { signed: true })} for {line.buy_name ?? line.buy} against {formatPercent(line.holding_return, { signed: true })} for{" "}
        {line.sell_name ?? line.sell}: a gap of {formatPercent(line.return_gap, { signed: true })} against a hurdle of {formatPercent(line.hurdle)}
        {line.tax != null ? `, of which tax ${line.tax.toFixed(0)}` : ""}
        {line.safety_margin != null ? ` and a safety margin of ${formatPercent(line.safety_margin, { decimals: 0 })}` : ""}.
      </p>
      {line.action !== "hold" ? (
        <p className="num text-text-muted">
          Sell {line.sell_value.toFixed(0)}, buy {line.buy_value.toFixed(0)} at equal risk (volatility {formatPercent(line.holding_volatility, { decimals: 0 })} to {formatPercent(line.idea_volatility, { decimals: 0 })})
          {line.remainder > 0 ? `; ${line.remainder.toFixed(0)} goes to the next idea or the core ETFs` : ""}.
        </p>
      ) : null}
      {line.wait_note ? <p className="text-text-muted">{line.wait_note}</p> : null}
      {(line.reasons ?? []).map((reason) => (
        <p key={reason} className="text-text">
          {reason}.
        </p>
      ))}
      {line.action !== "hold" ? (
        <div className="mt-1.5 flex items-center gap-3">
          <Button type="button" size="sm" variant="outline" onClick={() => void record()} disabled={state !== "idle"}>
            {state === "busy" ? "Logging…" : state === "done" ? "Logged" : "I made this switch"}
          </Button>
          <Problem>{problem}</Problem>
        </div>
      ) : null}
    </li>
  );
}
