import type { ReactNode } from "react";

import { Absent } from "@/components/company/primitives";
import { DataAgeBadge } from "@/components/freshness/data-age-badge";
import { formatMoney, formatPercent, gainLossTone } from "@/lib/format";
import { figureReason, type Figure } from "@/lib/performance/performance";
import { cn } from "@/lib/utils";

const TONE = { gain: "text-gain", loss: "text-loss", flat: "" } as const;

/** A signed return or money figure in the gain/loss colour, with the sign as text as well. */
export function Toned({ value, children }: { value: number | null | undefined; children: ReactNode }) {
  return <span className={cn("num", TONE[gainLossTone(value)])}>{children}</span>;
}

/** A computed return: the number, or why there is none. Qualified figures keep their warning. */
export function ReturnCell({ figure, decimals = 1 }: { figure: Figure | null | undefined; decimals?: number }) {
  if (!figure) return <span className="text-text-muted">—</span>;
  if (figure.value === null || figure.value === undefined) {
    return <Absent reason={`missing: ${figureReason(figure) ?? "not computable"}`} />;
  }
  const qualified = figure.status === "partial" && (figure.warnings ?? []).length > 0;
  return (
    <span title={qualified ? (figure.warnings ?? []).join(" ") : undefined}>
      <Toned value={figure.value}>{formatPercent(figure.value, { signed: true, decimals })}</Toned>
      {qualified ? <span className="ml-1 text-xs text-text-muted">qualified</span> : null}
    </span>
  );
}

export function PlainReturn({ value, decimals = 1 }: { value: number | null | undefined; decimals?: number }) {
  if (value === null || value === undefined) return <span className="text-text-muted">—</span>;
  return <Toned value={value}>{formatPercent(value, { signed: true, decimals })}</Toned>;
}

export function Money({
  value,
  currency,
  signed = false,
  decimals = 0,
}: {
  value: number | null | undefined;
  currency: string | null | undefined;
  signed?: boolean;
  decimals?: number;
}) {
  if (value === null || value === undefined || !currency) return <span className="text-text-muted">—</span>;
  const text = formatMoney(value, currency, { signed, decimals });
  return signed ? <Toned value={value}>{text}</Toned> : <span className="num">{text}</span>;
}

/** Percentage points, already in points: 3.1 -> "+3.1 pp". */
export function Points({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) return <span className="text-text-muted">—</span>;
  const text = `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toFixed(1)} pp`;
  return <Toned value={value}>{text}</Toned>;
}

export const TH = "px-3 py-1.5 text-right font-medium";
export const TH_LEFT = "px-3 py-1.5 text-left font-medium";
export const TD = "num px-3 py-1.5 text-right";

type FreshnessInput = {
  prices_as_of?: string | null;
  prices_age_days?: number | null;
  benchmark_as_of?: string | null;
  benchmark_age_days?: number | null;
  stale_after_days: number;
};

/** The age of the prices and rates under a chart, plus the benchmark when one is compared. */
export function PriceAge({ freshness, benchmark = false }: { freshness: FreshnessInput | null | undefined; benchmark?: boolean }) {
  if (!freshness) return null;
  return (
    <DataAgeBadge
      staleAfterDays={freshness.stale_after_days}
      items={[
        { label: "Prices and rates", date: freshness.prices_as_of, ageDays: freshness.prices_age_days },
        ...(benchmark ? [{ label: "Benchmark", date: freshness.benchmark_as_of, ageDays: freshness.benchmark_age_days }] : []),
      ]}
    />
  );
}

export function Formula({ lines, version }: { lines: string[]; version: string }) {
  return (
    <details className="border border-border-soft px-3 py-2 text-sm">
      <summary className="cursor-pointer font-medium">How these figures are calculated</summary>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-text-muted">
        {lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-text-muted">Method version: {version}</p>
    </details>
  );
}
