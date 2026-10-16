"use client";

import { BookOpenIcon, TableIcon } from "lucide-react";
import Link from "next/link";
import { useId, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

/*
 * F26 ChartFooter: every chart's provenance line. A `Data` toggle swaps the picture for its table
 * twin (the same rows, P4), `Learn` links to the method, and the dataset, as-of and source are
 * written out. Wrap the chart and its table in <ChartWithFooter> so the toggle controls both.
 */

export function ChartWithFooter({
  chart,
  table,
  dataset,
  asOf,
  source,
  learnHref,
  defaultView = "chart",
  className,
}: {
  chart: ReactNode;
  table: ReactNode;
  dataset: string;
  asOf?: string | null;
  source?: string | null;
  learnHref?: string;
  defaultView?: "chart" | "table";
  className?: string;
}) {
  const [view, setView] = useState(defaultView);
  const id = useId();
  return (
    <div className={className}>
      <div id={id}>{view === "chart" ? chart : table}</div>
      <ChartFooter
        controls={id}
        showingTable={view === "table"}
        onToggle={() => setView(view === "chart" ? "table" : "chart")}
        dataset={dataset}
        asOf={asOf}
        source={source}
        learnHref={learnHref}
      />
    </div>
  );
}

export function ChartFooter({
  controls,
  showingTable,
  onToggle,
  dataset,
  asOf,
  source,
  learnHref,
  className,
}: {
  controls?: string;
  showingTable?: boolean;
  onToggle?: () => void;
  dataset: string;
  asOf?: string | null;
  source?: string | null;
  learnHref?: string;
  className?: string;
}) {
  return (
    <div className={cn("mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border-soft pt-2 text-[11px] text-text-muted", className)}>
      {onToggle ? (
        <button
          type="button"
          aria-controls={controls}
          aria-pressed={showingTable}
          onClick={onToggle}
          className="inline-flex items-center gap-1 font-medium text-accent-ink hover:underline"
        >
          <TableIcon aria-hidden="true" className="size-3.5" />
          {showingTable ? "Chart" : "Data"}
        </button>
      ) : null}
      {learnHref ? (
        <Link href={learnHref} className="inline-flex items-center gap-1 font-medium text-accent-ink hover:underline">
          <BookOpenIcon aria-hidden="true" className="size-3.5" />
          Learn
        </Link>
      ) : null}
      <span>{dataset}</span>
      {asOf ? <span>As of {asOf}</span> : null}
      {source ? <span>Source: {source}</span> : null}
    </div>
  );
}
