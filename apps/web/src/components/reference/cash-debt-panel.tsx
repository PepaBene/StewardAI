"use client";

import { TriangleAlertIcon } from "lucide-react";
import { useCallback, useMemo } from "react";

import { BarChart, WaterfallChart } from "@/components/charts";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatCompact, formatPct } from "@/lib/charts/format";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { parseCashConversion, parseDebtMaturities } from "@/lib/reference/cash-debt";
import { RECONCILIATION_LABEL, type Reconciliation } from "@/lib/reference/financial-history";

/**
 * Two as-filed views from /holdings/{ticker}/financial-history: how net income turns into free
 * cash flow, and when the debt falls due. Both use one period, one basis and one currency as
 * filed. A line the issuer did not file is named as missing, never drawn as zero.
 */
export function CashDebtPanel({ ticker }: { ticker: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/financial-history", { params: { path: { ticker }, query: {} } }),
    [ticker],
  );
  const { state } = useApiResource(fetcher);
  const raw = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const cash = useMemo(() => parseCashConversion(raw), [raw]);
  const debt = useMemo(() => parseDebtMaturities(raw), [raw]);

  return (
    <Blueprint aria-labelledby="cash-debt-heading" className="flex min-w-0 flex-col gap-4 px-[22px] py-5">
      <PanelHeading kicker="Cash and debt · as filed" title={<span id="cash-debt-heading">Cash conversion and debt maturities</span>} />
      {state.kind === "loading" && !raw ? <p className="text-sm text-text-muted">Loading…</p> : null}
      {state.kind === "error" && !raw ? (
        <p className="text-sm text-text-muted">
          Cash and debt unavailable: {state.title}
          {state.detail ? `. ${state.detail}` : ""}
        </p>
      ) : null}
      {raw && !cash && !debt ? (
        <p className="max-w-[68ch] text-sm text-text-muted">
          No cash-flow bridge or debt schedule is stored for this holding. Both need the same filing to carry net income
          and operating cash flow, or a tagged debt maturity table.
        </p>
      ) : null}

      <div className="grid gap-x-8 gap-y-5 lg:grid-cols-2">
        {cash ? (
          <section className="min-w-0 space-y-2" aria-labelledby="cash-sub">
            <h3 id="cash-sub" className="text-sm font-semibold">
              From net income to free cash flow
            </h3>
            <p className="num text-xs text-text-muted">
              Year ending {formatDate(cash.periodEnd)}, {cash.currency ?? "currency not stated"}, {cash.basis || "basis not stated"}. The
              dashed step is the balancing figure, worked out from the filed totals.
            </p>
            <WaterfallChart
              steps={cash.steps}
              label={`Net income to free cash flow, year ending ${cash.periodEnd}`}
              formatValue={(v) => formatCompact(v)}
            />
            <p className="num text-sm">
              {cash.conversion === null
                ? cash.freeCashFlow === null
                  ? "Free cash flow is not available because capital expenditure is not filed."
                  : "Conversion is not meaningful: net income is not positive."
                : `Free cash flow is ${formatPct(cash.conversion, 0)} of net income.`}
            </p>
            {cash.missing.length > 0 ? <p className="text-xs text-text-muted">Not filed for this period: {cash.missing.join(", ")}.</p> : null}
            <Recon state={cash.reconciliation} />
          </section>
        ) : null}

        {debt ? (
          <section className="min-w-0 space-y-2" aria-labelledby="debt-sub">
            <h3 id="debt-sub" className="text-sm font-semibold">
              When the debt falls due
            </h3>
            <p className="num text-xs text-text-muted">
              Principal due after {formatDate(debt.periodEnd)}, {debt.currency ?? "currency not stated"}, {debt.basis || "basis not stated"}.
            </p>
            <BarChart
              data={debt.buckets.map((b) => ({ label: b.label, value: b.value }))}
              label={`Debt principal due by year after ${debt.periodEnd}`}
              formatValue={(v) => formatCompact(v)}
              height={180}
            />
            <p className="num text-sm">Filed total {formatCompact(debt.total)}.</p>
            {debt.missing.length > 0 ? (
              <p className="text-xs text-text-muted">
                No filed amount for {debt.missing.join(", ")}; those are gaps, not zero, so the total may be understated.
              </p>
            ) : null}
            <Recon state={debt.reconciliation} />
          </section>
        ) : null}
      </div>
    </Blueprint>
  );
}

function Recon({ state }: { state: Reconciliation }) {
  if (state === "reconciled") return null;
  return (
    <p className="flex items-center gap-1 text-xs text-text-muted">
      <TriangleAlertIcon aria-hidden="true" className="size-3.5 text-warning-signal" />
      {RECONCILIATION_LABEL[state]}
    </p>
  );
}
