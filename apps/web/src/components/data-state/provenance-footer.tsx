import { ExternalLinkIcon } from "lucide-react";

import { coverageShare, describeCoverage, type DataProvenance } from "@/lib/data-state/provenance";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

import { DataStateBadge } from "./data-state-badge";

/**
 * The line under any new figure or chart: state, source (linked), as-of date, method and the
 * coverage denominator. `knownAt` appears only when it differs from the as-of date, because
 * that gap is what a point-in-time query cares about.
 */
export function ProvenanceFooter({ provenance, className }: { provenance: DataProvenance; className?: string }) {
  const { source, sourceUrl, asOf, knownAt, method, state, reason, coverage } = provenance;
  const share = coverageShare(coverage);
  return (
    <div className={cn("flex flex-col gap-1.5 border-t border-border-soft pt-2 text-xs text-text-muted", className)}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <DataStateBadge state={state} reason={reason} />
        <span>
          Source:{" "}
          {sourceUrl ? (
            <a
              href={sourceUrl}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-flex items-center gap-0.5 text-accent-ink hover:underline"
            >
              {source}
              <ExternalLinkIcon aria-hidden="true" className="size-3" />
              <span className="sr-only">(opens in a new tab)</span>
            </a>
          ) : (
            <span className="text-text">{source}</span>
          )}
        </span>
        <span className="num">
          {asOf ? (
            <>
              As of <time dateTime={asOf}>{formatDate(asOf)}</time>
            </>
          ) : (
            "No as-of date"
          )}
          {knownAt && asOf && knownAt.slice(0, 10) !== asOf.slice(0, 10) ? (
            <>
              {" "}
              · known since <time dateTime={knownAt}>{formatDate(knownAt)}</time>
            </>
          ) : null}
        </span>
        {coverage ? (
          <span className="inline-flex items-center gap-1.5">
            <span className="num">{describeCoverage(coverage)}</span>
            {share !== null ? (
              <span
                role="meter"
                aria-label="Coverage"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(share * 100)}
                className="inline-block h-1.5 w-14 bg-border-soft"
              >
                <span className="block h-full bg-accent" style={{ width: `${share * 100}%` }} />
              </span>
            ) : null}
          </span>
        ) : null}
      </div>
      {method ? <p>Method: {method}</p> : null}
      {reason ? <p className="text-text">{reason}</p> : null}
    </div>
  );
}
