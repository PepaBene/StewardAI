import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { HBar } from "@/components/charts";
import { formatPct } from "@/lib/charts/format";
import { formatDay } from "@/lib/charts/time";
import { classifySessions, completeness, describeQuote, disagreementPct, looksLikeUnitError } from "@/lib/markets/logic";
import type { ListingSeries, PriceDiagnostics, VenueCalendar } from "@/lib/markets/types";

/**
 * Holding-view price diagnostics (D3.7): source of record and alternatives, exchange, quote
 * currency and unit, adjustment basis, last session, disagreements and days missing, with a
 * return-window completeness score. A disagreement that looks like a 100× unit mix-up says so.
 */
export function PriceDiagnosticsPanel({
  listing,
  diagnostics,
  calendar,
}: {
  listing: ListingSeries;
  diagnostics: PriceDiagnostics;
  calendar: VenueCalendar;
}) {
  const cells = classifySessions(calendar, listing.raw.map((b) => b.date));
  const share = completeness(cells);
  const missing = cells.filter((c) => c.status === "missing_bar").length;
  const last = listing.raw[listing.raw.length - 1];

  const facts: [string, string][] = [
    ["Source of record", diagnostics.sourceOfRecord],
    ["Compared against", diagnostics.alternatives.length ? diagnostics.alternatives.join(", ") : "No alternative source covers this listing"],
    ["Exchange", `${diagnostics.exchange} (${diagnostics.mic})`],
    ["Quote currency and unit", last ? describeQuote(last.close, diagnostics.quoteCurrency) + " at last close" : diagnostics.quoteCurrency],
    ["Adjustment basis", diagnostics.adjustmentBasis],
    ["Last session", diagnostics.lastSession ? formatDay(diagnostics.lastSession) : "none"],
    ["Open days without a bar", `${missing} in the window`],
  ];

  return (
    <Blueprint as="section" aria-labelledby="price-diag-heading" className="flex flex-col gap-3 px-5 py-5">
      <PanelHeading kicker="Price source diagnostics" title={<span id="price-diag-heading">{listing.ticker}: where this price comes from</span>} />
      <dl className="m-0 grid grid-cols-1 gap-x-8 gap-y-2 text-sm md:grid-cols-2">
        {facts.map(([k, v]) => (
          <div key={k} className="flex flex-col border-b border-border-soft pb-1.5">
            <dt className="text-xs text-text-muted">{k}</dt>
            <dd className="m-0">{v}</dd>
          </div>
        ))}
      </dl>

      <div className="flex flex-col gap-1">
        <div className="flex items-baseline justify-between text-sm">
          <span className="font-medium">Return-window completeness</span>
          <span className="num">{share === null ? "n/a" : formatPct(share, 1)}</span>
        </div>
        <HBar label="Share of open sessions with a bar" value={share} max={1} tone={share !== null && share < 0.98 ? "warning" : "accent"} />
        <p className="text-xs text-text-muted">
          A return window is labelled complete only at 100% of open sessions, and a partial window shows this share beside its figure.
        </p>
      </div>

      <section aria-labelledby="disagreements-heading" className="flex flex-col gap-1.5">
        <h3 id="disagreements-heading" className="font-heading text-lg font-semibold">
          Disagreements with other sources
        </h3>
        {diagnostics.disagreements.length === 0 ? (
          <p className="text-sm text-text-muted">No close differs from the source of record beyond the tolerance.</p>
        ) : (
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-sm">
            {diagnostics.disagreements.map((d) => {
              const unit = looksLikeUnitError(d);
              const pct = disagreementPct(d);
              return (
                <li key={`${d.provider}-${d.date}`} className="border-l-[3px] border-l-disputed bg-disputed-subtle px-3 py-1.5">
                  <span className="font-medium">{d.provider}</span> on <span className="num">{formatDay(d.date)}</span>: primary{" "}
                  <span className="num">{d.primary.toLocaleString("en-US")}</span> vs <span className="num">{d.other.toLocaleString("en-US")}</span> ({d.currency}
                  {pct !== null && !unit ? `, ${formatPct(pct, 2, true)}` : ""}).{" "}
                  {unit ? (
                    <strong className="font-semibold">This is a unit mix-up (pence versus pounds), not a price difference. The value is rejected, not averaged.</strong>
                  ) : (
                    "Both values are kept; the source of record is shown."
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </Blueprint>
  );
}
