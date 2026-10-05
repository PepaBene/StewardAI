"use client";

import { LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { StackedBar } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { dataOf, Panel, ResourceNotice } from "@/components/oversight/panel";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { flagText, rankRows, TIERS, tierCounts, tierLabel, type LedgerRow } from "@/lib/funds/ledger";

const fetchLedger = () => getBrowserApiClient().GET("/funds/coverage-ledger");

/**
 * Where each fund's or index's constituents come from. The tier says how far to trust a
 * look-through number: official files are exact, a proxy fund is an approximation, and an
 * entry with no source means its look-through is unknown rather than empty.
 */
export function LedgerPanel() {
  const { state, reload } = useApiResource(fetchLedger);
  const data = dataOf(state);
  const rows = useMemo(() => (data ? rankRows(data.entries) : []), [data]);
  const counts = useMemo(() => tierCounts(rows), [rows]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const refresh = async () => {
    setBusy(true);
    setNote(null);
    try {
      const r = await getBrowserApiClient().POST("/funds/coverage-ledger/refresh");
      setNote(r.response.ok ? "Ledger rebuilt from your Jobs config and stored snapshots." : `Refresh failed (HTTP ${r.response.status}).`);
      if (r.response.ok) reload();
    } catch (err) {
      setNote(`Refresh failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      id="ledger"
      title="Where holdings data comes from"
      description="How each fund's or index's constituents are sourced, best source first. A weaker tier means a look-through number built on it deserves less trust."
    >
      <ResourceNotice state={state} what="the coverage ledger" />
      {data && rows.length === 0 ? (
        <EmptyState title="The ledger is empty">Refresh it to build entries from your Jobs config and stored fund snapshots.</EmptyState>
      ) : null}

      {rows.length > 0 ? (
        <>
          <StackedBar
            title="Entries by source tier"
            note={`${rows.length} entries`}
            segments={counts.filter((c) => c.count > 0).map((c) => ({ label: c.label, value: c.count }))}
            formatShare={(f) => String(Math.round(f * rows.length))}
          />
          <div className="relative overflow-x-auto border border-border">
            <table className="w-full min-w-[44rem] text-sm">
              <caption className="sr-only">Source of constituents per fund or index, best tier first</caption>
              <thead className="bg-surface-muted">
                <tr className="border-b border-border text-left text-xs text-text-muted">
                  <th className="px-3 py-2 font-normal">Fund or index</th>
                  <th className="px-3 py-2 font-normal">Source tier</th>
                  <th className="px-3 py-2 font-normal">Total return</th>
                  <th className="px-3 py-2 font-normal">Past constituents</th>
                  <th className="px-3 py-2 font-normal">Holdings as of</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <LedgerLine key={`${r.subject_kind}:${r.subject_key}`} row={r} />
                ))}
              </tbody>
            </table>
          </div>
          {data && data.flagged_benchmarks.length > 0 ? (
            <p className="max-w-[72ch] text-xs text-text-muted">
              Not safe to use for performance or backtests until they gain total return or historical constituents:{" "}
              <span className="font-mono text-text">{data.flagged_benchmarks.join(", ")}</span>.
            </p>
          ) : null}
        </>
      ) : null}

      <details className="text-xs text-text-muted">
        <summary className="cursor-pointer text-accent-ink hover:underline">What the tiers mean</summary>
        <dl className="mt-2 grid gap-x-4 gap-y-1 sm:grid-cols-[10rem_1fr]">
          {TIERS.map((t) => (
            <div key={t.id} className="contents">
              <dt className="font-semibold text-text">{t.label}</dt>
              <dd>{t.meaning}</dd>
            </div>
          ))}
        </dl>
      </details>

      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" variant="outline" size="sm" onClick={refresh} disabled={busy}>
          {busy ? <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" /> : <RefreshCwIcon aria-hidden="true" className="size-4" />}
          Refresh ledger
        </Button>
        <span className="text-xs text-text-muted" role="status">
          {note ?? "A refresh rebuilds derived rows only. Entries you verified by hand are never overwritten."}
        </span>
      </div>
    </Panel>
  );
}

function LedgerLine({ row }: { row: LedgerRow }) {
  return (
    <tr className="border-b border-border-soft align-top last:border-0">
      <td className="px-3 py-2">
        <div className="font-medium">{row.label}</div>
        <div className="font-mono text-[11px] text-text-muted">
          {row.subject_kind === "fund" ? row.subject_key : `Index ${row.subject_key}`}
          {row.origin === "manual" ? " · verified by you" : ""}
        </div>
      </td>
      <td className="px-3 py-2">
        <span className="font-medium">{tierLabel(row.tier)}</span>
        {row.proxy_fund_isin ? (
          <div className="text-xs">
            via{" "}
            <Link href={`/funds?isin=${row.proxy_fund_isin}`} className="font-mono text-accent-ink hover:underline">
              {row.proxy_fund_isin}
            </Link>
          </div>
        ) : null}
        <div className="max-w-[40ch] text-xs text-text-muted">
          {row.reason}
          {row.source_url ? (
            <>
              {" "}
              <a href={row.source_url} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">
                Source
              </a>
            </>
          ) : null}
        </div>
      </td>
      <td className="px-3 py-2">{flagText(row.total_return_available)}</td>
      <td className="px-3 py-2">{flagText(row.historical_constituents)}</td>
      <td className="num px-3 py-2">{row.latest_holdings_as_of ? formatDate(row.latest_holdings_as_of) : "Not published"}</td>
    </tr>
  );
}
