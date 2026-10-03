"use client";

import Link from "next/link";
import { LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import { useCallback, useState } from "react";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Blueprint } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { jobHref } from "@/lib/jobs/job-detail";

function money(value: number | string | null | undefined) { return value == null ? "—" : new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 }).format(Number(value)); }

export function InvestorsView() {
  const [pending, setPending] = useState(false);
  const [jobId, setJobId] = useState<number | null>(null);
  const [jobError, setJobError] = useState("");
  const fetcher = useCallback(() => getBrowserApiClient().GET("/investors"), []);
  const { state } = useApiResource(fetcher);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  return <div className="space-y-5">
    <PageHeader title="13F investors" description="Track selected institutional managers using their SEC Form 13F filings." />
    <Blueprint className="space-y-4 px-[22px] py-5">
      <p className="text-sm text-text-muted">13F reports show certain US securities held at quarter end, filed up to 45 days later. Reported value changes are not portfolio returns.</p>
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" variant="outline" disabled={pending} onClick={() => {
          setPending(true); setJobError("");
          void getBrowserApiClient().POST("/jobs/ownership-13f").then((result) => {
            if (result.data) setJobId(result.data.id);
            else setJobError("The 13F load could not be queued.");
          }).catch((error: unknown) => setJobError(error instanceof Error ? error.message : "The 13F load could not be queued."))
            .finally(() => setPending(false));
        }}>
          {pending ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <RefreshCwIcon aria-hidden="true" />}
          Load SEC 13F data
        </Button>
        {jobId !== null ? <Link href={jobHref(jobId)} className="text-xs text-accent-ink hover:underline" role="status">Queued as job #{jobId}</Link> : null}
        {jobError ? <span className="text-xs text-danger" role="alert">{jobError}</span> : null}
      </div>
      {state.kind === "error" ? <p className="text-sm text-danger">Could not load tracked investors: {state.detail}</p> : null}
      {state.kind === "loading" ? <p className="text-sm text-text-muted">Loading managers…</p> : null}
      {data?.length === 0 ? <EmptyState title="No managers configured">Add managers in <Link className="text-accent-ink hover:underline" href="/config/jobs">Jobs configuration</Link>, then run the 13F ownership job to load filings.</EmptyState> : null}
      {data && data.length > 0 ? <div className="overflow-x-auto border border-border"><table className="w-full min-w-[42rem] text-sm">
        <thead><tr className="border-b border-border text-text-muted">{["Manager", "Latest quarter", "Filed", "Reported value", "Prior reported value", "Positions"].map((v) => <th key={v} className="px-3 py-2 text-left font-medium">{v}</th>)}</tr></thead>
        <tbody>{data.map((manager) => <tr key={manager.cik} className="border-b border-border last:border-0"><th scope="row" className="px-3 py-2 text-left font-medium"><Link className="text-accent-ink hover:underline" href={`/investors/${manager.cik}`}>{manager.name}</Link></th><td className="num px-3 py-2">{manager.period ? formatDate(manager.period) : "No filing loaded"}</td><td className="num px-3 py-2">{manager.filed ? formatDate(manager.filed) : "—"}</td><td className="num px-3 py-2">{money(manager.reported_value_usd)}</td><td className="num px-3 py-2" title={manager.earlier_period ? `Quarter ended ${formatDate(manager.earlier_period)}` : undefined}>{money(manager.previous_value_usd)}</td><td className="num px-3 py-2">{manager.positions}</td></tr>)}</tbody>
      </table></div> : null}
    </Blueprint>
  </div>;
}
