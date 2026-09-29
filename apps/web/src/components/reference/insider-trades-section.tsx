"use client";

import { ExternalLinkIcon, LoaderCircleIcon } from "lucide-react";
import { useCallback, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";

const formOptions = ["3", "4", "5"] as const;
const transactionOptions = [
  { code: "P", label: "Open-market buy" },
  { code: "S", label: "Open-market sell" },
  { code: "A", label: "Grant / award" },
  { code: "M", label: "Option exercise" },
  { code: "F", label: "Tax withholding" },
  { code: "G", label: "Gift" },
];

export function useInsiderTrades(ticker: string, forms: string[]) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/holdings/{ticker}/insider-trades", {
    params: { path: { ticker }, query: { limit: 100, forms } },
  }), [ticker, forms]);
  return useApiResource(fetcher);
}

export function InsiderTradesSection({ ticker }: { ticker: string }) {
  const [selectedForms, setSelectedForms] = useState<string[]>([...formOptions]);
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState("");
  const resource = useInsiderTrades(ticker, selectedForms.flatMap((form) => [form, `${form}/A`]));
  const { state } = resource;
  const rows = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const visibleRows = rows?.filter((row) => selectedCode === null || row.transaction_code === selectedCode);
  return <Blueprint aria-labelledby="insider-heading" className="space-y-3 px-[22px] py-5">
    <div>
      <PanelHeading kicker="SEC ownership filings" title={<span id="insider-heading">Insider transactions</span>} />
      <p className="mt-1 text-xs text-text-muted">Forms 3, 4 and 5 report beneficial ownership. A reported transaction is not necessarily an open market trade.</p>
    </div>
    <div className="flex flex-wrap gap-2" aria-label="Filter by SEC form">
      {formOptions.map((form) => <button key={form} type="button" aria-pressed={selectedForms.includes(form)}
        onClick={() => setSelectedForms((current) => current.includes(form) ? current.filter((item) => item !== form) : [...current, form])}
        className={`border px-2.5 py-1 text-xs ${selectedForms.includes(form) ? "border-accent bg-accent-soft text-accent-ink" : "border-border text-text-muted hover:bg-surface-raised"}`}>
        Form {form}{form === "4" ? " · transactions" : ""}
      </button>)}
    </div>
    <div className="flex flex-wrap gap-2" aria-label="Filter by transaction code">
      {[{ code: null, label: "All codes" }, ...transactionOptions].map((option) => <button key={option.code ?? "all"} type="button" aria-pressed={selectedCode === option.code}
        onClick={() => setSelectedCode(option.code)} className={`border px-2.5 py-1 text-xs ${selectedCode === option.code ? "border-accent bg-accent-soft text-accent-ink" : "border-border text-text-muted hover:bg-surface-raised"}`}>
        {option.label}
      </button>)}
    </div>
    {state.kind === "loading" ? <p className="flex items-center gap-2 text-sm text-text-muted"><LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />Loading SEC filings…</p> : null}
    {state.kind === "error" ? <AlertMessage title={`Insider filings: ${state.title}`}>{state.detail}</AlertMessage> : null}
    {rows?.length === 0 ? <p className="text-sm text-text-muted">No structured insider transactions have been loaded for this holding.</p> : null}
    {rows && visibleRows?.length === 0 ? <p className="text-sm text-text-muted">No filings match these filters.</p> : null}
    {visibleRows && visibleRows.length > 0 ? <div className="overflow-x-auto border border-border">
      <table className="w-full min-w-[58rem] text-sm">
        <thead><tr className="border-b border-border text-text-muted">
          { ["Date", "Insider", "Role", "Form", "Code", "Shares", "Price", "After", "Filing"].map((label) => <th key={label} className="px-3 py-2 text-left font-medium">{label}</th>)}
        </tr></thead>
        <tbody>{visibleRows.map((row, index) => <tr key={`${row.accession}:${index}`} className="border-b border-border last:border-0">
          <td className="num px-3 py-2">{row.transaction_date ? formatDate(row.transaction_date) : formatDate(row.filed)}</td>
          <td className="px-3 py-2">{row.owner_name ?? `CIK ${row.reporting_owner_cik}`}</td>
          <td className="px-3 py-2 text-text-muted">{row.officer_title || [row.is_director && "Director", row.is_officer && "Officer", row.is_ten_percent_owner && "10% owner"].filter(Boolean).join(", ") || "—"}</td>
          <td className="px-3 py-2">{row.form}</td><td className="px-3 py-2" title="SEC transaction code">{row.transaction_code}{row.acquired_disposed ? ` · ${row.acquired_disposed}` : ""}</td>
          <td className="num px-3 py-2">{row.shares == null ? "—" : Number(row.shares).toLocaleString()}</td>
          <td className="num px-3 py-2">{row.price_per_share == null ? "—" : `$${Number(row.price_per_share).toFixed(2)}`}</td>
          <td className="num px-3 py-2">{row.shares_owned_after == null ? "—" : Number(row.shares_owned_after).toLocaleString()}</td>
          <td className="px-3 py-2"><a href={row.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">SEC <ExternalLinkIcon aria-hidden="true" className="size-3" /></a></td>
        </tr>)}</tbody>
      </table>
      <p className="px-3 py-2 text-xs text-text-muted">Showing at most 100 transaction rows. SEC filing data are reported by the filer and may include grants, tax withholding, gifts and derivative securities.</p>
    </div> : null}
    <div className="flex items-center gap-3">
      <button type="button" disabled={refreshing} className="text-xs text-accent hover:underline disabled:opacity-50"
        onClick={() => {
          setRefreshing(true); setRefreshMessage("");
          void getBrowserApiClient().POST("/jobs/insider-trades").then((result) => {
            setRefreshMessage(result.data ? `Refresh queued as job #${result.data.id}.` : "Could not queue the refresh job.");
          }).catch((error: unknown) => setRefreshMessage(error instanceof Error ? error.message : "Could not queue the refresh job."))
            .finally(() => setRefreshing(false));
        }}>
        {refreshing ? "Queueing refresh…" : "Refresh insider filings"}
      </button>
      {refreshMessage ? <span className="text-xs text-text-muted" role="status">{refreshMessage}</span> : null}
    </div>
  </Blueprint>;
}
