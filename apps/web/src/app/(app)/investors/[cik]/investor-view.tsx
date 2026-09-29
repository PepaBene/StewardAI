"use client";

import Link from "next/link";
import { useCallback } from "react";

import { AlertMessage } from "@/components/alert-message";
import { PageHeader } from "@/components/page-header";
import { Blueprint } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";

function money(value: number | string | null | undefined) { return value == null ? "—" : new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(Number(value)); }

export function InvestorView({ cik }: { cik: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/investors/{cik}", { params: { path: { cik } } }), [cik]);
  const { state } = useApiResource(fetcher);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  return <div className="space-y-5">
    <Link href="/investors" className="text-sm text-accent hover:underline">← All 13F investors</Link>
    {data ? <PageHeader title={data.name} description={`SEC CIK ${data.cik} · Quarter ended ${data.period ? formatDate(data.period) : "—"} · filed ${data.filed ? formatDate(data.filed) : "—"}`} /> : <PageHeader title="13F manager" description="Loading manager filing…" />}
    {state.kind === "error" ? <AlertMessage title={`Could not load manager: ${state.title}`}>{state.detail}</AlertMessage> : null}
    {data ? <>
      <Blueprint className="grid gap-4 px-[22px] py-5 sm:grid-cols-3">
        <div><p className="text-xs text-text-muted">Reported value</p><p className="num mt-1 text-lg font-medium">{money(data.reported_value_usd)}</p></div>
        <div><p className="text-xs text-text-muted">Reported positions</p><p className="num mt-1 text-lg font-medium">{data.positions}</p></div>
        <div><p className="text-xs text-text-muted">Prior quarter reported value</p><p className="num mt-1 text-lg font-medium">{money(data.previous_value_usd)}</p></div>
      </Blueprint>
      <Blueprint className="space-y-3 px-[22px] py-5">
        <h2 className="font-medium">Largest disclosed common-stock positions</h2>
        <p className="text-xs text-text-muted">13F filings disclose selected reportable securities at quarter end and may be filed up to 45 days later. Values can move because of market prices, transactions, and reporting scope; they are not returns.</p>
        {data.positions_detail.length ? <div className="overflow-x-auto border border-border"><table className="w-full min-w-[38rem] text-sm">
          <thead><tr className="border-b border-border text-text-muted">{["Issuer", "Class", "CUSIP", "Shares", "Reported value"].map((v) => <th key={v} className="px-3 py-2 text-left font-medium">{v}</th>)}</tr></thead>
          <tbody>{data.positions_detail.map((row) => <tr key={`${row.cusip}:${row.title_of_class}`} className="border-b border-border last:border-0"><th className="px-3 py-2 text-left font-medium">{row.issuer_name}</th><td className="px-3 py-2">{row.title_of_class}</td><td className="num px-3 py-2">{row.cusip}</td><td className="num px-3 py-2">{row.shares == null ? "—" : Number(row.shares).toLocaleString()}</td><td className="num px-3 py-2">{money(row.value_usd)}</td></tr>)}</tbody>
        </table></div> : <p className="text-sm text-text-muted">No eligible stock positions have been loaded yet.</p>}
      </Blueprint>
      <p className="text-xs text-text-muted">{data.performance_note}</p>
    </> : null}
  </div>;
}
