import type { ReactNode } from "react";

import { AlertMessage } from "@/components/alert-message";
import { formatPercent } from "@/lib/format";
import { basisShares, BASIS_HELP } from "@/lib/xray/xray";
import { cn } from "@/lib/utils";

export { TD, TH, TH_LEFT, Toned } from "../performance/shared";

/** A weight as a percentage; the minus sign is a real sign, not a hyphen. */
export function Pct({ value, decimals = 1 }: { value: number | null | undefined; decimals?: number }) {
  if (value === null || value === undefined) return <span className="text-text-muted">—</span>;
  return <span className="num">{formatPercent(value, { decimals })}</span>;
}

/** A weight with a thin proportional bar under the number; the bar is decoration, the number is the value. */
export function WeightBar({ value, max, className }: { value: number; max: number; className?: string }) {
  const width = max > 0 ? Math.min(100, (Math.max(value, 0) / max) * 100) : 0;
  return (
    <div className={cn("min-w-24", className)}>
      <Pct value={value} />
      <div aria-hidden="true" className="mt-1 h-1 bg-border-soft">
        <div className="h-full bg-accent" style={{ width: `${width}%` }} />
      </div>
    </div>
  );
}

/** How a bucket was classified: reported, inferred or your entry, each with its share. */
export function BasisChips({ basis }: { basis: Record<string, number> }) {
  const shares = basisShares(basis);
  if (shares.length === 0) return <span className="text-xs text-text-faint">not a classification</span>;
  return (
    <ul className="flex flex-wrap gap-1">
      {shares.map((s) => (
        <li
          key={s.basis}
          title={BASIS_HELP[s.basis]}
          className="border border-border-soft px-1.5 py-px text-xs leading-4 text-text-muted"
        >
          {s.label} <span className="num text-text">{Math.round(s.share * 100)}%</span>
        </li>
      ))}
    </ul>
  );
}

export function Notes({ notes, title = "Read with these limits" }: { notes: readonly string[]; title?: string }) {
  if (notes.length === 0) return null;
  return (
    <details className="border border-border-soft px-3 py-2 text-sm">
      <summary className="cursor-pointer font-medium">{title}</summary>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-text-muted">
        {notes.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
    </details>
  );
}

export function Warnings({ warnings }: { warnings: readonly string[] }) {
  if (warnings.length === 0) return null;
  return (
    <div className="space-y-2">
      {warnings.map((w) => (
        <AlertMessage key={w} title="Read this first">
          {w}
        </AlertMessage>
      ))}
    </div>
  );
}

/** A number with a small label under it, the unit of every summary row on these pages. */
export function Fact({ label, children, note }: { label: string; children: ReactNode; note?: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className="mt-0.5 text-lg leading-tight font-semibold">{children}</dd>
      {note ? <p className="mt-0.5 text-xs text-text-muted">{note}</p> : null}
    </div>
  );
}
