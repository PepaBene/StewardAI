"use client";

import { ExternalLinkIcon } from "lucide-react";
import { useCallback, useState } from "react";

import { StackedBar } from "@/components/charts";
import { CATEGORICAL_BG } from "@/components/charts/shared";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient, type HoldingSegments } from "@/lib/api/client";
import { formatPct } from "@/lib/charts/format";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  AS_REPORTED_NOTE,
  AXIS_LABELS,
  availableAxes,
  chartRows,
  formatRevenue,
  periodCaption,
  sourceCaption,
  type SegmentAxis,
} from "@/lib/reference/segments";
import { cn } from "@/lib/utils";

type Basis = "annual" | "quarter";
const BASIS_LABELS: Record<Basis, string> = {
  annual: "Annual",
  quarter: "Quarter",
};

/** GET /holdings/{ticker}/segments, for one period basis (null = the backend's default). */
export function useHoldingSegments(ticker: string, basis: Basis | null = null) {
  const fetcher = useCallback(
    () =>
      getBrowserApiClient().GET("/holdings/{ticker}/segments", {
        params: { path: { ticker }, query: basis ? { basis } : {} },
      }),
    [ticker, basis],
  );
  return useApiResource(fetcher);
}

function Switch<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly { id: T; label: string }[];
  value: T;
  onChange: (id: T) => void;
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex border border-border text-xs font-semibold">
      {options.map((o, i) => (
        <button
          key={o.id}
          type="button"
          aria-pressed={value === o.id}
          onClick={() => onChange(o.id)}
          className={cn(
            "px-2.5 py-1.5",
            i > 0 && "border-l border-border",
            value === o.id ? "bg-accent text-accent-foreground" : "text-text hover:bg-surface-muted",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/**
 * Revenue by region / segment / product for a holding, as the company reports it: a 100%
 * bar of the latest period and the figures behind it, with the period, the source filing
 * (or "entered manually") and the "as reported" caveat. A switch picks the breakdown and,
 * where both are stored, annual or quarterly figures.
 */
export function SegmentsPanel({ ticker }: { ticker: string }) {
  const [basis, setBasis] = useState<Basis | null>(null);
  const [axis, setAxis] = useState<SegmentAxis | null>(null);
  const { state } = useHoldingSegments(ticker, basis);
  const data: HoldingSegments | null =
    state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const axes = availableAxes(data);
  const shown: SegmentAxis | null = axis && axes.includes(axis) ? axis : (axes[0] ?? null);
  const rows = data && shown ? data[shown] : [];
  const drawn = chartRows(rows);
  const period = data ? periodCaption(data, formatDate) : null;
  const source = data ? sourceCaption(data, formatDate) : null;
  const filingUrl = data?.source?.filing_url ?? null;

  return (
    <Blueprint aria-labelledby="segments-heading" className="flex min-w-0 flex-col gap-3 px-[22px] py-5">
      <PanelHeading
        kicker="Revenue mix · as reported"
        title={<span id="segments-heading">Revenue by region / segment</span>}
        action={
          data && data.available_bases.length > 1 && data.basis ? (
            <Switch
              label="Period"
              options={data.available_bases.map((b) => ({
                id: b,
                label: BASIS_LABELS[b],
              }))}
              value={data.basis}
              onChange={(b) => setBasis(b)}
            />
          ) : null
        }
      />
      {state.kind === "loading" && !data ? <p className="text-sm text-text-muted">Loading…</p> : null}
      {state.kind === "error" && !data ? (
        <p className="text-sm text-text-muted">
          Revenue breakdown unavailable: {state.title}
          {state.detail ? ` — ${state.detail}` : ""}.
        </p>
      ) : null}
      {data && axes.length === 0 ? (
        <p className="text-sm text-text-muted">
          {data.notes?.[0] ?? "No revenue breakdown is stored for this holding."}
        </p>
      ) : null}
      {data && shown ? (
        <div className="grid gap-x-8 gap-y-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <div className="flex min-w-0 flex-col gap-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              {axes.length > 1 ? (
                <Switch
                  label="Breakdown"
                  options={axes.map((a) => ({ id: a, label: AXIS_LABELS[a] }))}
                  value={shown}
                  onChange={(a) => setAxis(a)}
                />
              ) : (
                <span className="text-xs font-semibold">{AXIS_LABELS[shown]}</span>
              )}
              {period ? <span className="num text-xs text-text-muted">{period}</span> : null}
            </div>
            <StackedBar
              segments={drawn}
              legend={false}
              title={`${AXIS_LABELS[shown]}, share of ${data.period?.label ?? "the period"}`}
            />
            <FootNotes data={data} source={source} filingUrl={filingUrl} />
          </div>
          <ul className="flex flex-col text-[13px]">
            {rows.map((r) => {
              const index = drawn.findIndex((d) => d.label === r.label);
              return (
                <li
                  key={`${r.member ?? r.label}`}
                  className={cn(
                    "grid grid-cols-[0.75rem_minmax(0,1fr)_auto_3.5rem] items-center gap-2 border-b border-border-soft py-1.5 last:border-0",
                    r.subtotal && "text-text-muted",
                  )}
                >
                  <span
                    aria-hidden="true"
                    className={cn("size-2.5", index >= 0 && index < 8 ? CATEGORICAL_BG[Math.min(index, 7)] : "")}
                  />
                  <span className="truncate" title={r.member ?? undefined}>
                    {r.label}
                    {r.subtotal ? <span className="ml-1 text-xs">(subtotal, not drawn)</span> : null}
                  </span>
                  <span className="num text-right" title={`${r.value} ${data.currency ?? ""}`}>
                    {formatRevenue(r.value, data.currency)}
                  </span>
                  <span className="num text-right text-text-muted">
                    {typeof r.share === "number" ? formatPct(r.share, 1) : "—"}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </Blueprint>
  );
}

function FootNotes({
  data,
  source,
  filingUrl,
}: {
  data: HoldingSegments;
  source: string | null;
  filingUrl: string | null;
}) {
  return (
    <div className="space-y-0.5 text-xs text-text-muted">
      {data.total_revenue !== null && data.total_revenue !== undefined ? (
        <p>
          Total revenue {formatRevenue(data.total_revenue, data.currency)}; shares are of the breakdown&rsquo;s sum.
        </p>
      ) : null}
      <p>{AS_REPORTED_NOTE}</p>
      {source ? (
        <p className="flex flex-wrap items-center gap-1">
          Source: {source}
          {filingUrl ? (
            <a
              href={filingUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-0.5 text-accent-ink hover:underline"
            >
              filing
              <ExternalLinkIcon aria-hidden="true" className="size-3" />
              <span className="sr-only">{data.source?.accession} on SEC EDGAR</span>
            </a>
          ) : null}
        </p>
      ) : null}
      {(data.notes ?? []).map((n) => (
        <p key={n}>{n}</p>
      ))}
    </div>
  );
}
