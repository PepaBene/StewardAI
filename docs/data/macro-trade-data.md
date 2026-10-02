# Macro and trade data: storage semantics

Applies to `macro_observation`, `trade_observation`, `exposure_mapping` (code in
`apps/backend/src/steward/macro/`).

## Observations and revisions

- A fetch never re-inserts a value that is already stored for the same series, source, geography and
  period (trade: reporter, partner, flow, HS revision, HS code, period). A different value is the
  next `revision` with `known_at` = the fetch time, so point-in-time reads still see the old value
  before that time. `metadata.revised_from` holds the previous value.
- `vintage` is the provider's own label when it has one (Eurostat `updated`, FRED `realtime_start`,
  World Bank header `lastupdated`, ALFRED window start); otherwise the fetch date.
- ALFRED rows are the exception: each real-time window is a provider vintage, keyed by
  (period, window start). The request uses `output_type=1` (all windows) with the same `units`
  as the current-vintage request; `output_type=4` is initial-release-only and shows no revisions.
- Rows without a finite value (NaN, infinity, `.`) are never stored. Gaps are reported in
  `coverage_status` (World Bank: the missing years are listed in `reason`).
- D2.1 attributes (`unit_multiplier`, `seasonal_adjustment`, `transformation`, plus
  `release_calendar` and `geography_definition` in `macro_series_metadata`) come from SDMX
  attributes (`UNIT_MULT`, `ADJUSTMENT`, `TRANSFORMATION`), ECB csv columns, Eurostat filters and
  the series config; they are left empty rather than guessed.

## Geography and SDMX

- OECD/BIS requests are pinned to the configured country (`steward.macro.sdmx_keys`) and sent with
  `dimensionAtObservation=AllDimensions`; the geography of each row is read from the response
  (`REF_AREA`, `BORROWERS_CTY`, ...). BIS keys are checked against the documented dimension order
  for WS_TC, WS_DSR, WS_SPP and WS_LONG_CPI only; other flows are reported `key_unvalidated`.
- Comtrade reports UN M49 numbers. `trade_observation.reporter_iso` / `partner_iso` hold ISO
  alpha-2 (`W00` for World, NULL for regions) and are filled when the row is written (and, for older
  rows, by migration 20261005110000). Comtrade's own codes differ from ISO numeric for US (842),
  France (251), Italy (381), Norway (579), Switzerland (757) and India (699); both are mapped.

## Trade

- Flows: `export`, `import`, `re_export`, `re_import`. Quantity (`quantity`, `quantity_unit`) and
  net weight in kg (`net_weight`) are separate columns; a zero quantity with unit N/A is NULL.
- HS revisions are stored canonically (`HS2022`, not `H6`). `exposure_mapping.hs_code` requires
  `hs_revision`. `hs_concordance` is seeded at chapter level only (HS2017 <-> HS2022 chapters have
  the same numbering); 4/6-digit mappings need the WCO correlation tables
  (`steward.macro.hs.concordance_rows`) and are "unknown" until loaded.
- `trade_metrics` reports export and import growth and partner concentration separately, excludes
  the World aggregate from concentration, treats a missing value as missing, and compares only
  adjacent periods.

## Exposure

The 100% cap is per issuer and dimension: geography, HS product, and geography+HS cells. The
share attributed to an issuer is that of its best-covered dimension, never the sum of dimensions.
`GET /macro/context/{ticker}` returns no country indicators and `exposure_unknown=true` when no
mapping created on or before `as_of` exists, ranges use the latest vintage of each period, and
`series_effects` holds the exposure-weighted latest change per series with the covered weight.

## Not verified against live providers

Everything here is built on documented formats and recorded/hand-written fixtures. Unverified:
the BIS/OECD dimension orders outside the four BIS flows above, the SDMX-JSON version each API
actually serves for the `Accept` header, Comtrade's response fields beyond those used here
(no provider vintage is available per row), ALFRED paging on very long histories.
