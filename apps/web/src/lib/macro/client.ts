import { COUNTRY_COMPARISON_FIXTURE, EXPOSURE_MAP_FIXTURE, MACRO_SERIES_FIXTURE, TRADE_SLICE_FIXTURE } from "./fixture";
import type { CountryComparison, HoldingExposureMap, MacroSeries, TradeSlice } from "./types";

export type MacroPageData = {
  series: readonly MacroSeries[];
  comparison: CountryComparison;
  trade: TradeSlice;
  exposure: HoldingExposureMap;
};

/** Seam for the macro/trade read APIs (Phase 7 `D7.2`/`D7.3`); resolves the typed fixture until they exist. */
export async function loadMacroPage(): Promise<MacroPageData> {
  return {
    series: MACRO_SERIES_FIXTURE,
    comparison: COUNTRY_COMPARISON_FIXTURE,
    trade: TRADE_SLICE_FIXTURE,
    exposure: EXPOSURE_MAP_FIXTURE,
  };
}
