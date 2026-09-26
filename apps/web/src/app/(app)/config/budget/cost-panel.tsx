"use client";

import { LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { formatMoney, formatPercent } from "@/lib/format";

import { budgetApi, type CostSummary } from "./budget-api";
import { currentBudgetMonth } from "./budget-form";

const usd = (v: number | null | undefined) => formatMoney(v, "USD", { decimals: 2 });
/** Step costs are recorded to the millionth of a dollar; small ones show 4 decimals. */
const usdFine = (v: number) => formatMoney(v, "USD", { decimals: v > 0 && v < 1 ? 4 : 2 });

type State = { kind: "loading" } | { kind: "ok"; data: CostSummary } | { kind: "error"; message: string };

async function fetchSummary(month: string): Promise<State> {
  try {
    const result = await budgetApi.costSummary(month);
    return result.data
      ? { kind: "ok", data: result.data }
      : {
          kind: "error",
          message: `The API answered HTTP ${result.response.status}.`,
        };
  } catch (err) {
    return {
      kind: "error",
      message: err instanceof Error ? err.message : String(err),
    };
  }
}

/**
 * One budget month's AI spend (calendar month in Europe/Prague, as the budget guard counts
 * it) against the monthly cap, by job type and by model. Every figure is a sum of the steps'
 * recorded `cost_usd`, so the total equals the job log's step costs to the cent.
 */
export function CostPanel() {
  const [month, setMonth] = useState(currentBudgetMonth);
  const [state, setState] = useState<State>({ kind: "loading" });
  const sequence = useRef(0);

  const [reloads, setReloads] = useState(0);

  useEffect(() => {
    const mine = ++sequence.current;
    void fetchSummary(month).then((next) => mine === sequence.current && setState(next));
    return () => {
      sequence.current += 1;
    };
  }, [month, reloads]);

  const data = state.kind === "ok" ? state.data : null;

  return (
    <section
      id="costs"
      aria-labelledby="costs-heading"
      className="scroll-mt-24 space-y-3 rounded-lg border border-border bg-surface-raised p-4"
    >
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 id="costs-heading" className="text-lg font-semibold">
            Spend by month
          </h2>
          <p className="max-w-prose text-sm text-text-muted">
            The sum of every AI step&rsquo;s recorded cost, per calendar month in Prague time. Each step&rsquo;s cost is in the{" "}
            <Link href="/activity#job-log" className="text-accent hover:underline">
              job log
            </Link>
            .
          </p>
        </div>
        <div className="flex items-end gap-2">
          <div className="space-y-1">
            <Label htmlFor="cost-month">Month</Label>
            <Input
              id="cost-month"
              type="month"
              className="w-40"
              value={month}
              onChange={(e) => {
                if (!e.target.value) return;
                setState({ kind: "loading" });
                setMonth(e.target.value);
              }}
            />
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setState({ kind: "loading" });
              setReloads((n) => n + 1);
            }}
          >
            <RefreshCwIcon aria-hidden="true" />
            Reload
          </Button>
        </div>
      </div>

      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading the costs…
        </p>
      ) : null}
      {state.kind === "error" ? <AlertMessage title="Could not load the costs">{state.message}</AlertMessage> : null}

      {data ? (
        <>
          <CapBar data={data} />
          <div className="grid gap-4 lg:grid-cols-2">
            <CostTable
              caption="Spend by job type"
              head={["Job type", "Runs", "Cost", "Cap per run"]}
              rows={data.by_job_type.map((r) => ({
                key: r.job_type,
                cells: [
                  <>
                    <Link
                      href={`/activity?type=${encodeURIComponent(r.job_type)}#job-log`}
                      className="font-mono text-xs hover:text-accent hover:underline"
                    >
                      {r.job_type}
                    </Link>
                    {r.critical ? (
                      <span className="ml-1.5 rounded-sm border border-border px-1 text-xs text-text-muted">critical</span>
                    ) : null}
                  </>,
                  String(r.runs),
                  usdFine(r.cost_usd),
                  r.job_cap_usd === null ? "—" : usd(r.job_cap_usd),
                ],
              }))}
            />
            <CostTable
              caption="Spend by model"
              head={["Model", "Steps", "Cost"]}
              rows={data.by_model.map((r) => ({
                key: r.model,
                cells: [
                  <span key="m" className="font-mono text-xs">
                    {r.model}
                  </span>,
                  String(r.steps),
                  usdFine(r.cost_usd),
                ],
              }))}
            />
          </div>
        </>
      ) : null}
    </section>
  );
}

function CapBar({ data }: { data: CostSummary }) {
  if (data.cap_usd === null) {
    return (
      <p className="text-sm" role="status">
        <span className="num text-lg font-semibold">{usd(data.total_usd)}</span>{" "}
        <span className="text-text-muted">spent in {data.month}; no monthly cap set.</span>
      </p>
    );
  }
  const fraction = data.fraction ?? 0;
  const over = fraction >= 1;
  const warn = fraction >= data.warn_at_fraction;
  return (
    <div className="space-y-1.5">
      <p className="text-sm" role="status">
        <span className="num text-lg font-semibold">{usd(data.total_usd)}</span>{" "}
        <span className="text-text-muted">
          of {usd(data.cap_usd)} in {data.month} ({formatPercent(fraction, { decimals: 0 })})
          {over ? " — cap reached" : warn ? " — past the warning threshold" : ""}
        </span>
      </p>
      <div
        role="meter"
        aria-label="Spend against the monthly cap"
        aria-valuemin={0}
        aria-valuemax={data.cap_usd}
        aria-valuenow={Math.min(data.total_usd, data.cap_usd)}
        className="relative h-2.5 overflow-hidden rounded-full bg-surface-muted"
      >
        <div
          className={over || warn ? "h-full rounded-full bg-warning-signal" : "h-full rounded-full bg-accent"}
          style={{ width: `${Math.min(fraction, 1) * 100}%` }}
        />
        <div
          aria-hidden="true"
          className="absolute inset-y-0 w-px bg-text-muted"
          style={{ left: `${data.warn_at_fraction * 100}%` }}
          title="Warning threshold"
        />
      </div>
    </div>
  );
}

function CostTable({ caption, head, rows }: { caption: string; head: string[]; rows: { key: string; cells: React.ReactNode[] }[] }) {
  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full text-sm">
        <caption className="px-3 py-2 text-left text-sm font-medium">{caption}</caption>
        <thead className="bg-surface-muted">
          <tr className="border-b border-border text-text-muted">
            {head.map((h, i) => (
              <th key={h} scope="col" className={`px-3 py-2 font-medium ${i === 0 ? "text-left" : "text-right"}`}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={head.length} className="px-3 py-3 text-text-muted">
                No AI spend this month.
              </td>
            </tr>
          ) : (
            rows.map((r) => (
              <tr key={r.key} className="border-b border-border last:border-0">
                {r.cells.map((c, i) =>
                  i === 0 ? (
                    <th key={i} scope="row" className="px-3 py-2 text-left font-normal">
                      {c}
                    </th>
                  ) : (
                    <td key={i} className="num px-3 py-2 text-right">
                      {c}
                    </td>
                  ),
                )}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
