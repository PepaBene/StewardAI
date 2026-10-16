"use client";

import { CircleCheckIcon, CircleDashedIcon, CircleHelpIcon, CircleXIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { AXIS_LABEL, sortChecks, STATE_LABEL, stateCounts, type Check, type CheckState, type MarkAxis } from "@/lib/insight/insight";
import { cn } from "@/lib/utils";

/*
 * F19 CheckList: a row per check with a state icon, the backend's sentence and the section the
 * check reads. Four states, always icon plus word: colour is never the only cue.
 */

const ICON: Record<CheckState, typeof CircleCheckIcon> = {
  pass: CircleCheckIcon,
  fail: CircleXIcon,
  na: CircleDashedIcon,
  unknown: CircleHelpIcon,
};

const TONE: Record<CheckState, string> = {
  pass: "text-gain",
  fail: "text-loss",
  na: "text-text-faint",
  unknown: "text-warning-signal",
};

export function CheckStateIcon({ state, className }: { state: CheckState; className?: string }) {
  const Icon = ICON[state];
  return <Icon aria-hidden="true" className={cn("size-4 shrink-0", TONE[state], className)} />;
}

function sectionHref(ticker: string | undefined, ref: string | null | undefined): string | null {
  if (!ref) return null;
  if (ref.startsWith("/")) return ref;
  return ticker ? `/companies/${encodeURIComponent(ticker)}/${ref}` : null;
}

export function CheckRow({ check, ticker }: { check: Check; ticker?: string }) {
  const href = sectionHref(ticker, check.section_ref);
  return (
    <li className="flex gap-2.5 border-t border-border-soft py-2 first:border-t-0">
      <CheckStateIcon state={check.state} className="mt-0.5" />
      <div className="min-w-0 flex-1">
        <p className="text-sm leading-snug">
          <span className="sr-only">{STATE_LABEL[check.state]}: </span>
          {check.sentence}
        </p>
        <p className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-text-muted">
          <span>{check.label}</span>
          {check.basis ? <span>{check.basis}</span> : null}
          {check.informational ? <span>For information, not scored</span> : null}
          {href ? (
            <Link href={href} className="text-accent-ink underline underline-offset-2">
              See the figures
            </Link>
          ) : null}
        </p>
      </div>
    </li>
  );
}

/** "4 of 5 applicable" summary line plus the rows. */
export function CheckList({
  checks,
  ticker,
  title,
  failedFirst = false,
  limit,
  className,
}: {
  checks: readonly Check[];
  ticker?: string;
  title?: string;
  failedFirst?: boolean;
  /** Show only the first n rows (the modal shows them all). */
  limit?: number;
  className?: string;
}) {
  const rows = failedFirst ? sortChecks(checks) : [...checks];
  const counts = stateCounts(checks);
  const applicable = counts.pass + counts.fail;
  const shown = limit ? rows.slice(0, limit) : rows;
  return (
    <div className={className}>
      {title ? <h3 className="text-sm font-semibold">{title}</h3> : null}
      <p className="text-xs text-text-muted">
        <span className="num font-medium text-text">
          {counts.pass} of {applicable}
        </span>{" "}
        applicable checks pass
        {counts.unknown ? `, ${counts.unknown} unknown` : ""}
        {counts.na ? `, ${counts.na} not applicable` : ""}
      </p>
      <ul className="mt-1">
        {shown.map((c) => (
          <CheckRow key={c.id} check={c} ticker={ticker} />
        ))}
      </ul>
      {limit && rows.length > limit ? <p className="text-xs text-text-muted">{rows.length - limit} more in all checks</p> : null}
    </div>
  );
}

/** "See all checks": every axis and the risk list in a modal, failed first within each. */
export function AllChecksDialog({
  checks,
  risks,
  ticker,
  name,
  open,
  onClose,
  initialAxis,
}: {
  checks: readonly Check[];
  risks: readonly Check[];
  ticker: string;
  name: string;
  open: boolean;
  onClose: () => void;
  initialAxis?: MarkAxis | "risk";
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [axis, setAxis] = useState<MarkAxis | "risk" | "all">(initialAxis ?? "all");
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  const tabs: (MarkAxis | "risk" | "all")[] = ["all", "value", "future", "past", "health", "dividend", "risk"];
  const groups = (axis === "all" ? (["value", "future", "past", "health", "dividend", "risk"] as const) : [axis]).map((a) => ({
    axis: a,
    rows: sortChecks(a === "risk" ? risks : checks.filter((c) => c.axis === a)),
  }));
  return (
    <dialog
      ref={ref}
      aria-labelledby="all-checks-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      className="m-auto max-h-[90dvh] w-[min(48rem,calc(100vw-2rem))] overflow-y-auto border border-border bg-surface-raised p-0 text-text shadow-lg backdrop:bg-surface/80"
    >
      {open ? (
        <div className="p-5">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 id="all-checks-title" className="text-[22px] leading-tight font-semibold">
                All checks for {name}
              </h2>
              <p className="text-sm text-text-muted">Failed checks first. Each sentence names the figure and the limit it was held to.</p>
            </div>
            <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
              <XIcon aria-hidden="true" />
            </Button>
          </div>
          <div role="tablist" aria-label="Axis" className="mt-3 flex flex-wrap gap-1.5">
            {tabs.map((t) => (
              <button
                key={t}
                type="button"
                role="tab"
                aria-selected={axis === t}
                onClick={() => setAxis(t)}
                className={cn(
                  "border px-2.5 py-1 text-xs",
                  axis === t ? "border-accent bg-accent-soft text-accent-ink" : "border-border text-text-muted hover:text-text",
                )}
              >
                {t === "all" ? "All" : t === "risk" ? "Risks" : AXIS_LABEL[t]}
              </button>
            ))}
          </div>
          <div className="mt-4 space-y-5">
            {groups.map((g) => (
              <CheckList
                key={g.axis}
                checks={g.rows}
                ticker={ticker}
                title={g.axis === "risk" ? "Risk checks" : AXIS_LABEL[g.axis]}
              />
            ))}
          </div>
        </div>
      ) : null}
    </dialog>
  );
}
