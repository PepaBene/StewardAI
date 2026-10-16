import type { HoldingOwnership, SharesOutstandingOut } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { denominatorText, formatOutstandingPct, outstandingView } from "@/lib/reference/shares-outstanding";
import { cn } from "@/lib/utils";

function ratioSource(so: SharesOutstandingOut): string {
  const from = so.ads_ratio_source === "profile" ? "holding profile" : "default";
  const stated = so.ads_ratio_reported !== null && so.ads_ratio_reported !== undefined;
  return stated && Number(so.ads_ratio_reported) === Number(so.ads_ratio) ? `${from}; the 20-F states the same` : from;
}

/**
 * 13F holders' aggregate share of shares outstanding, what it measures ("US 13F filers'
 * holdings of the US line — a floor" for ADRs) and the denominator's source; or why the
 * holding has no 13F figures at all (non-US listings, UCITS ETFs).
 */
export function OutstandingNote({ data, className }: { data: HoldingOwnership | null; className?: string }) {
  const view = outstandingView(data);
  if (!view) return null;
  if (view.kind === "not_applicable") {
    return <p className={cn("text-xs text-text-muted", className)}>{view.reason}</p>;
  }
  if (view.kind === "no_denominator") {
    return (
      <p className={cn("text-xs text-text-muted", className)}>
        % of shares outstanding: no SEC shares-outstanding figure stored yet (the SEC XBRL job reads it).
      </p>
    );
  }
  return (
    <div className={cn("space-y-0.5 text-xs text-text-muted", className)}>
      <p>
        <span className="num font-semibold text-text">{formatOutstandingPct(view.pct)}</span> of shares outstanding held
        by 13F filers · {view.basis}.
      </p>
      <p>
        Denominator: {denominatorText(view.so, formatDate)}
        {view.ratio ? ` · ${view.ratio} (${ratioSource(view.so)})` : ""}.
      </p>
      {(view.so.notes ?? []).map((n) => (
        <p key={n}>{n}</p>
      ))}
    </div>
  );
}
