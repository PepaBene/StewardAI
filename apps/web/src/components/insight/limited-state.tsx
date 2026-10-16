import { InfoIcon } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/*
 * F33 LimitedState (visual spec B6): what a chart or panel shows instead of itself when it is
 * below its minimum data. One line, in the chart's place: what it needs, what it has, and what
 * fills it. Never an empty axis, an empty dashed box or a grid of "Unknown".
 */

export type MinData = {
  /** The minimum the visual needs. */
  min: number;
  /** What there is now. */
  have: number;
  /** "trading days", "holdings", "stored days"… */
  unit: string;
};

/** True when `have` meets `min`: the visual may draw. */
export function hasMinData({ min, have }: Pick<MinData, "min" | "have">): boolean {
  return Number.isFinite(have) && have >= min;
}

/** The standard sentence (visual spec D.3 `limited`): "Needs 60 trading days, has 10." */
export function limitedSentence({ min, have, unit }: MinData): string {
  return `Needs ${min.toLocaleString("en")} ${unit}, has ${Math.max(0, have).toLocaleString("en")}.`;
}

export function LimitedState({
  need,
  children,
  fixHref,
  fixLabel = "What fills it",
  since,
  className,
}: {
  /** Builds the standard sentence; omit and pass `children` for a custom one. */
  need?: MinData;
  children?: ReactNode;
  /** Where the missing data comes from (a job, an editor, a data-quality item). */
  fixHref?: string;
  fixLabel?: string;
  /** "Recording since 6 Oct" when the data is accumulating. */
  since?: string;
  className?: string;
}) {
  return (
    <div
      role="note"
      className={cn(
        "flex min-h-14 flex-wrap items-center gap-x-3 gap-y-1 rounded-lg bg-surface-muted px-4 py-3 text-[13px] text-text-muted",
        className,
      )}
    >
      <InfoIcon aria-hidden="true" className="size-4 shrink-0 text-text-faint" />
      <span>{children ?? (need ? limitedSentence(need) : null)}</span>
      {fixHref ? (
        <Link href={fixHref} className="font-medium text-accent-ink hover:underline">
          {fixLabel}
        </Link>
      ) : null}
      {since ? <span className="text-text-faint">{since}</span> : null}
    </div>
  );
}

/** Renders `children` when the minimum is met, else the LimitedState line. */
export function MinDataGuard({
  need,
  children,
  fixHref,
  since,
}: {
  need: MinData;
  children: ReactNode;
  fixHref?: string;
  since?: string;
}) {
  return hasMinData(need) ? <>{children}</> : <LimitedState need={need} fixHref={fixHref} since={since} />;
}
