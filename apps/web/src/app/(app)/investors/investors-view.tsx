"use client";

import { LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useMemo, useState } from "react";

import { DivergingBar } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { CONCENTRATION_LABEL, concentrationTier, signedPercent, valueChange, type Concentration } from "@/lib/investors/investors";
import { jobHref } from "@/lib/jobs/job-detail";
import { cn } from "@/lib/utils";

function money(value: number | string | null | undefined) {
  return value == null ? "—" : new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 }).format(Number(value));
}

const BARS: Record<Concentration, number> = { concentrated: 1, focused: 2, broad: 3 };

/** A three-step mark: the more filled steps, the broader the book. The tier is also written out. */
export function ConcentrationMark({ positions }: { positions: number }) {
  const tier = concentrationTier(positions);
  return (
    <span role="img" aria-label={CONCENTRATION_LABEL[tier]} title={CONCENTRATION_LABEL[tier]} className="inline-flex h-5 items-end gap-0.5">
      {[1, 2, 3].map((i) => (
        <span key={i} className={cn("w-1 rounded-sm", i <= BARS[tier] ? "bg-accent" : "bg-border")} style={{ height: `${i * 6 + 2}px` }} />
      ))}
    </span>
  );
}

export function InvestorsView() {
  const [pending, setPending] = useState(false);
  const [jobId, setJobId] = useState<number | null>(null);
  const [jobError, setJobError] = useState("");
  const fetcher = useCallback(() => getBrowserApiClient().GET("/investors"), []);
  const { state } = useApiResource(fetcher);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const bars = useMemo(
    () =>
      (data ?? []).flatMap((m) => {
        const change = valueChange(m.reported_value_usd, m.previous_value_usd);
        return change === null ? [] : [{ label: m.name, value: change * 100 }];
      }),
    [data],
  );

  return (
    <div className="space-y-5">
      <PageHeader title="13F investors" description="What well-known managers reported owning at the end of each quarter, from their SEC Form 13F filings." />

      <div className="flex flex-wrap items-center gap-3">
        <Button
          size="sm"
          variant="outline"
          disabled={pending}
          onClick={() => {
            setPending(true);
            setJobError("");
            void getBrowserApiClient()
              .POST("/jobs/ownership-13f")
              .then((result) => {
                if (result.data) setJobId(result.data.id);
                else setJobError("The 13F load could not be started. Try again in a minute.");
              })
              .catch((error: unknown) => setJobError(error instanceof Error ? error.message : "The 13F load could not be started."))
              .finally(() => setPending(false));
          }}
        >
          {pending ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <RefreshCwIcon aria-hidden="true" />}
          Load new 13F filings
        </Button>
        {jobId !== null ? (
          <Link href={jobHref(jobId)} className="text-xs text-accent-ink hover:underline" role="status">
            Loading started. Follow its progress
          </Link>
        ) : null}
        {jobError ? (
          <span className="text-xs text-loss" role="alert">
            {jobError}
          </span>
        ) : null}
      </div>

      {state.kind === "error" ? <p className="text-sm text-loss">Could not load managers: {state.detail}</p> : null}
      {state.kind === "loading" ? <p className="text-sm text-text-muted">Loading managers…</p> : null}
      {data?.length === 0 ? (
        <EmptyState title="No managers yet">
          Choose managers in{" "}
          <Link className="text-accent-ink hover:underline" href="/config/jobs">
            job settings
          </Link>
          , then load new 13F filings.
        </EmptyState>
      ) : null}

      {bars.length > 0 ? (
        <section aria-labelledby="change-heading" className="blueprint space-y-2 px-5 py-4">
          <h2 id="change-heading" className="font-heading text-lg leading-tight font-semibold">
            Value in your holdings against the previous quarter
          </h2>
          <DivergingBar rows={bars} colorMode="direction" label="Change in reported value since the previous quarter, per manager" formatValue={(v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(1)}%`} />
          <p className="text-xs text-text-muted">Reported values move with prices and trading. They are not investment returns.</p>
        </section>
      ) : null}

      {data && data.length > 0 ? (
        <section aria-labelledby="managers-heading" className="space-y-2">
          <h2 id="managers-heading" className="font-heading text-lg leading-tight font-semibold">
            Managers
          </h2>
          <div className="overflow-x-auto rounded-xl border border-border bg-surface-raised">
            <table className="w-full min-w-[40rem] text-sm">
              <caption className="sr-only">Managers with their latest 13F filing</caption>
              <thead>
                <tr className="border-b border-border text-xs text-text-muted">
                  <th scope="col" className="px-3 py-2 text-left font-medium">
                    Manager
                  </th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">
                    Quarter
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    Value in your holdings
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    Your holdings owned
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    Change
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.map((m) => {
                  const change = valueChange(m.reported_value_usd, m.previous_value_usd);
                  return (
                    <tr key={m.cik} className="border-b border-border last:border-0">
                      <th scope="row" className="px-3 py-2 text-left font-normal">
                        <span className="flex items-center gap-2.5">
                          {m.book_positions != null ? <ConcentrationMark positions={m.book_positions} /> : null}
                          <Link className="font-medium hover:text-accent-ink hover:underline" href={`/investors/${m.cik}`}>
                            {m.name}
                          </Link>
                        </span>
                      </th>
                      <td className="num px-3 py-2" title={m.filed ? `Filed ${formatDate(m.filed)}` : undefined}>
                        {m.period ? formatDate(m.period) : "No filing loaded"}
                      </td>
                      <td className="num px-3 py-2 text-right">{money(m.reported_value_usd)}</td>
                      <td className="num px-3 py-2 text-right">{m.positions}</td>
                      <td className={cn("num px-3 py-2 text-right", change === null ? "text-text-muted" : change > 0 ? "text-gain" : change < 0 ? "text-loss" : "")} title={m.earlier_period ? `Compared with the quarter ended ${formatDate(m.earlier_period)}` : undefined}>
                        {signedPercent(change)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-text-muted">Value and count cover only the manager’s positions in companies you hold, not its whole book. The bars beside a name, where the filing states its size, show how concentrated the whole book is.</p>
        </section>
      ) : null}

      <footer className="text-xs text-text-muted">13F reports list certain US securities held at quarter end and are filed up to 45 days later.</footer>
    </div>
  );
}
