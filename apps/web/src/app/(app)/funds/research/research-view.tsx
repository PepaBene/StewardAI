"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";

import { Absent, ResourceGate } from "@/components/company/primitives";
import { PageHeader } from "@/components/page-header";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { compareHref, fundHref, lagDays, money, pct, type CatalogRow, type SavedComparison } from "@/lib/fundresearch/fund-research";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

const MAX_COMPARE = 5;

/** Every fund the app holds a factsheet or holdings snapshot for. It is the covered set, not all funds. */
export function ResearchView() {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/funds/catalog"), []);
  const { state } = useApiResource(fetcher);
  const [picked, setPicked] = useState<readonly string[]>([]);
  const [filter, setFilter] = useState("");

  const toggle = (isin: string) =>
    setPicked((p) => (p.includes(isin) ? p.filter((x) => x !== isin) : p.length >= MAX_COMPARE ? p : [...p, isin]));

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Research & Verification"
        title="Fund research"
        description="Funds the app holds issuer data for. Open one for its profile, holdings, performance and due diligence, or pick two to five to compare."
        aside={
          picked.length >= 2 ? (
            <Link href={compareHref(picked)} className="border border-accent px-3 py-1.5 text-sm font-semibold text-text hover:bg-surface-muted">
              Compare {picked.length} funds
            </Link>
          ) : (
            <span>{picked.length === 0 ? "Tick two to five funds to compare" : "Tick at least one more"}</span>
          )
        }
      />
      <SavedComparisons />
      <Blueprint className="space-y-3 px-[22px] py-5">
        <label className="block max-w-sm text-sm">
          <span className="mb-1 block text-xs text-text-muted">Find a fund by name, ISIN or ticker</span>
          <input value={filter} onChange={(e) => setFilter(e.target.value)} className="w-full border border-border bg-surface px-2 py-1.5" />
        </label>
        <ResourceGate state={state} label="funds">
          {(rows: CatalogRow[]) => <Catalog rows={rows} filter={filter} picked={picked} onToggle={toggle} />}
        </ResourceGate>
      </Blueprint>
    </div>
  );
}

function SavedComparisons() {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/fund-comparisons"), []);
  const { state, reload } = useApiResource(fetcher);
  const [error, setError] = useState<string | null>(null);

  async function archive(id: number) {
    setError(null);
    const { error: err } = await getBrowserApiClient().DELETE("/fund-comparisons/{comparison_id}", { params: { path: { comparison_id: id } } });
    if (err) return setError("The comparison could not be archived.");
    reload();
  }

  return (
    <ResourceGate state={state} label="saved comparisons">
      {(rows: SavedComparison[]) =>
        rows.length === 0 ? null : (
          <Blueprint aria-labelledby="fr-saved" className="space-y-3 px-[22px] py-5">
            <PanelHeading kicker="Reopen a set of funds; the figures are recomputed each time" title={<span id="fr-saved">Saved comparisons</span>} />
            {error ? <p role="alert" className="text-sm text-loss">{error}</p> : null}
            <ul className="divide-y divide-border-soft text-sm">
              {rows.map((c) => (
                <li key={c.id} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 py-1.5">
                  <Link href={compareHref(c.isins)} className="font-medium text-accent-ink hover:underline">{c.name}</Link>
                  <span className="font-mono text-xs text-text-muted">{c.isins.join(", ")}</span>
                  {c.note ? <span className="text-xs text-text-muted">{c.note}</span> : null}
                  <span className="ml-auto text-xs text-text-muted">saved {formatDate(c.created_at)}</span>
                  <button type="button" onClick={() => archive(c.id)} className="border border-border px-2 py-0.5 text-xs hover:border-accent">Archive</button>
                </li>
              ))}
            </ul>
          </Blueprint>
        )
      }
    </ResourceGate>
  );
}

function Catalog({ rows, filter, picked, onToggle }: { rows: CatalogRow[]; filter: string; picked: readonly string[]; onToggle: (isin: string) => void }) {
  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return q ? rows.filter((r) => `${r.name ?? ""} ${r.isin} ${r.tickers.join(" ")}`.toLowerCase().includes(q)) : rows;
  }, [rows, filter]);
  if (rows.length === 0) return <p className="text-sm text-text-muted">No fund factsheet or holdings snapshot is stored yet. Run the fund holdings jobs from the data sources page.</p>;
  return (
    <>
      <p className="text-sm text-text-muted">
        {shown.length} of {rows.length} covered funds.
      </p>
      <div className="overflow-x-auto border border-border">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-surface-muted text-left">
              <th scope="col" className="w-10 px-3 py-1.5"><span className="sr-only">Compare</span></th>
              <th scope="col" className="px-2 py-1.5 font-medium">Fund</th>
              <th scope="col" className="px-2 py-1.5 font-medium">Issuer</th>
              <th scope="col" className="px-2 py-1.5 text-right font-medium">Ongoing charge</th>
              <th scope="col" className="px-2 py-1.5 text-right font-medium">Total assets</th>
              <th scope="col" className="px-2 py-1.5 font-medium">Holdings</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const lag = lagDays(r.holdings_as_of);
              const on = picked.includes(r.isin);
              return (
                <tr key={r.isin} className={cn("border-t border-border-soft", on && "bg-surface-muted")}>
                  <td className="px-3 py-1.5">
                    <input type="checkbox" checked={on} onChange={() => onToggle(r.isin)} disabled={!on && picked.length >= MAX_COMPARE} aria-label={`Compare ${r.name ?? r.isin}`} />
                  </td>
                  <th scope="row" className="px-2 py-1.5 text-left font-normal">
                    <Link href={fundHref(r.isin)} className="font-medium hover:text-accent-ink hover:underline">{r.name ?? r.isin}</Link>
                    <span className="block font-mono text-xs text-text-muted">{r.isin}{r.tickers.length ? ` · ${r.tickers.join(", ")}` : ""}</span>
                  </th>
                  <td className="px-2 py-1.5">{r.provider ?? <Absent reason="missing: not stored" />}</td>
                  <td className="num px-2 py-1.5 text-right">{pct(r.expense_ratio) ?? <Absent reason="missing: no factsheet" />}</td>
                  <td className="num px-2 py-1.5 text-right">{money(r.total_assets, r.total_assets_currency) ?? <Absent reason="missing: no factsheet" />}</td>
                  <td className="px-2 py-1.5">
                    {r.has_holdings ? (
                      <span className={cn(lag !== null && lag > 45 && "text-unconfirmed")}>{r.holdings_as_of ? formatDate(r.holdings_as_of) : ""}{lag !== null ? ` (${lag} d)` : ""}</span>
                    ) : (
                      <Absent reason="missing: no holdings snapshot" />
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
