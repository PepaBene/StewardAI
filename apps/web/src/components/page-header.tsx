import type * as React from "react";

import { cn } from "@/lib/utils";

/**
 * The header every screen opens with: a kicker naming the nav cluster (e.g. "Portfolio",
 * "Research & Verification"), the condensed page title, a one-line description, and an
 * optional right-hand slot for snapshot meta ("Snapshot 27 Sep 2026 · ČNB rates") or
 * page actions. Wraps below md.
 */
export function PageHeader({
  kicker,
  title,
  description,
  aside,
  className,
}: {
  kicker?: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  /** Right-aligned: freshness meta, tabs or action buttons. */
  aside?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-wrap items-end justify-between gap-4", className)}>
      <div className="min-w-0">
        {kicker ? <div className="kicker">{kicker}</div> : null}
        <h1 className="page-title mt-1 mb-1.5">{title}</h1>
        {description ? <div className="text-sm text-text-muted">{description}</div> : null}
      </div>
      {aside ? <div className="flex flex-wrap items-center gap-2 text-[13px] text-text-muted">{aside}</div> : null}
    </div>
  );
}
