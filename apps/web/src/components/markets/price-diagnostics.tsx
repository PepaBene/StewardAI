import { DataStateBadge } from "@/components/data-state";
import { HBar } from "@/components/charts";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { formatPct } from "@/lib/charts/format";
import { formatDay } from "@/lib/charts/time";
import { seriesCompleteness } from "@/lib/markets/logic";
import type { ListingOverview, PriceDiagnostic } from "@/lib/markets/types";

/**
 * Per-series price diagnostics (GET /holdings/{ticker}/prices/diagnostics): one row per stored
 * series (source and bar type) with exchange, quote unit, last session, observed vs expected
 * sessions on the exchange calendar, missing days and cross-source disagreements. The series the
 * readers serve is marked. The canonical listing (GET /holdings/{ticker}/listing) is shown when
 * one has been derived.
 */
export function PriceDiagnosticsPanel({
  ticker,
  rows,
  listing,
  listingNote,
}: {
  ticker: string;
  rows: readonly PriceDiagnostic[];
  listing: ListingOverview | null;
  /** Why there is no listing to show (not derived yet, or unreadable). */
  listingNote?: string | null;
}) {
  const chosen = rows.find((r) => r.chosen && r.bar_type === "adjusted") ?? rows.find((r) => r.chosen) ?? null;
  const share = chosen ? seriesCompleteness(chosen) : null;
  const facts: [string, string][] = chosen
    ? [
        ["Series served", `${chosen.source} · ${chosen.symbol} (${chosen.bar_type} bars)`],
        ["Exchange (MIC)", chosen.mic ?? "not reported"],
        ["Quote currency / unit", `${chosen.currency ?? "not reported"} / ${chosen.quote_unit ?? "not reported"}`],
        ["Last session", chosen.last_session ? formatDay(chosen.last_session) : "none"],
        ["Sessions behind", chosen.stale_sessions === null || chosen.stale_sessions === undefined ? "not assessed" : `${chosen.stale_sessions} expected session${chosen.stale_sessions === 1 ? "" : "s"} after the last bar`],
        ["Calendar basis", chosen.calendar_basis ?? "not reported"],
      ]
    : [];

  return (
    <Blueprint as="section" aria-labelledby="price-diag-heading" className="flex min-w-0 flex-col gap-3 px-5 py-5">
      <PanelHeading kicker="Price source diagnostics" title={<span id="price-diag-heading">{ticker}: where this price comes from</span>} />
      {rows.length === 0 ? (
        <p role="status" className="border border-dashed border-border px-3 py-3 text-sm text-text-muted">
          <DataStateBadge state="not_covered" /> <span className="ml-2">No price series is stored for this holding, or none of its provider symbols resolves.</span>
        </p>
      ) : (
        <>
          <dl className="m-0 grid grid-cols-1 gap-x-8 gap-y-2 text-sm md:grid-cols-2">
            {facts.map(([k, v]) => (
              <div key={k} className="flex min-w-0 flex-col border-b border-border-soft pb-1.5">
                <dt className="text-xs text-text-muted">{k}</dt>
                <dd className="m-0 break-words">{v}</dd>
              </div>
            ))}
          </dl>
          <div className="flex flex-col gap-1">
            <div className="flex items-baseline justify-between text-sm">
              <span className="font-medium">Sessions held (served series)</span>
              <span className="num">{chosen && share !== null ? `${chosen.expected_sessions - chosen.missing_days} of ${chosen.expected_sessions} (${formatPct(share, 1)})` : "no denominator"}</span>
            </div>
            <HBar label="Share of expected sessions with a bar" value={share} max={1} tone={share !== null && share < 0.98 ? "warning" : "accent"} />
            <p className="text-xs text-text-muted">Judged against the exchange&apos;s session calendar, not against the bars that happen to exist.</p>
          </div>
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[40rem] text-xs">
              <caption className="sr-only">Every stored price series of {ticker}</caption>
              <thead>
                <tr className="border-b border-border text-[11px] tracking-[0.08em] text-text-muted uppercase">
                  {["Source", "Symbol", "Bars", "Unit", "Last session", "Sessions", "Missing", "Differ >2%", "Access"].map((c, i) => (
                    <th key={c} scope="col" className={i < 2 ? "px-2 py-1.5 text-left font-medium" : "px-2 py-1.5 text-right font-medium"}>
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={`${r.symbol}-${r.source}-${r.bar_type}`} className="border-b border-border-soft last:border-0">
                    <th scope="row" className="px-2 py-1.5 text-left font-normal">
                      {r.source}
                      {r.chosen ? <span className="ml-1.5 border border-text px-1 text-[10px] font-semibold">SERVED</span> : null}
                    </th>
                    <td className="px-2 py-1.5">{r.symbol}</td>
                    <td className="px-2 py-1.5 text-right">{r.bar_type}</td>
                    <td className="num px-2 py-1.5 text-right">{r.quote_unit ?? r.currency ?? "n/a"}</td>
                    <td className="num px-2 py-1.5 text-right">{r.last_session ? formatDay(r.last_session) : "none"}</td>
                    <td className="num px-2 py-1.5 text-right">
                      {r.observed_sessions}/{r.expected_sessions}
                    </td>
                    <td className="num px-2 py-1.5 text-right">{r.missing_days}</td>
                    <td className="num px-2 py-1.5 text-right">{r.disagreements}</td>
                    <td className="px-2 py-1.5 text-right">{r.access_state}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-text-muted">
            Disagreements compare the last 20 sessions of one source with another source&apos;s series of the same listing in the major currency (pence are converted first); the backend returns the count, not the individual prices.
          </p>
        </>
      )}
      <section aria-labelledby="listing-heading" className="flex flex-col gap-1.5">
        <h3 id="listing-heading" className="font-heading text-lg font-semibold">
          Canonical listing
        </h3>
        {listing ? (
          <p className="m-0 text-sm">
            {listing.listing ? `${listing.listing.ticker ?? "?"} on ${listing.listing.mic ?? "unknown MIC"}, ${listing.listing.currency ?? "currency not reported"}${listing.listing.isin ? `, ISIN ${listing.listing.isin}` : ""}` : "No listing is attached."} · match{" "}
            <strong className="font-medium">{listing.status}</strong>
            {listing.score !== null ? ` (score ${listing.score})` : ""}
            {listing.providerSymbols.length > 0 ? ` · provider symbols: ${listing.providerSymbols.map((s) => `${s.provider} ${s.identifier}`).join(", ")}` : ""}
          </p>
        ) : (
          <p className="m-0 text-sm text-text-muted">{listingNote ?? "No listing derived for this holding yet."}</p>
        )}
      </section>
    </Blueprint>
  );
}
