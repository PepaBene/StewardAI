import { CORRELATION_FIXTURE, DIAGNOSTICS_FIXTURE, LISTINGS_FIXTURE, VENUE_CALENDARS } from "./fixture";
import type { CorrelationReport, ListingSeries, PriceDiagnostics, VenueCalendar } from "./types";

export type MarketsPageData = {
  listings: readonly ListingSeries[];
  diagnostics: Readonly<Record<string, PriceDiagnostics>>;
  calendars: Readonly<Record<string, VenueCalendar>>;
  correlation: CorrelationReport;
};

/** Seam for the price-coverage read APIs (`D3.7`, `D7.2`); resolves the typed fixture until they exist. */
export async function loadMarketsPage(): Promise<MarketsPageData> {
  return { listings: LISTINGS_FIXTURE, diagnostics: DIAGNOSTICS_FIXTURE, calendars: VENUE_CALENDARS, correlation: CORRELATION_FIXTURE };
}
