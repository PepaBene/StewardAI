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
  `hs_revision`. `hs_concordance` holds, besides the 97 chapters, the **6-digit correspondence
  tables of the UN Statistics Division** (free, official, no key; migration
  `20261007160000_hs_concordance_subheadings`): HS2022 -> HS2017 (15,656 pairs) and HS2017 ->
  HS2012 (6,140 pairs), each row with its `source_url` and `source_sha256`. Reproduce or extend it
  with `python -m steward.reference.hs_concordance fetch HS2022 HS2017 --emit-sql seed.sql`
  (`--load` writes to `DATABASE_URL`); the parser checks every relationship label against the
  degree of the pairs before it writes anything. Revisions are not chained: a pair with no loaded
  table is "unknown", never "same".
- `reference.hs_concordance.equivalence(code, from, to)` answers for 2-, 4- and 6-digit codes:
  `identical` (a 1:1 row), `aggregate_identical` (a chapter or heading whose 6-digit rows all stay
  inside it, so its trade adds up the same although its subheadings were re-cut), `related` (split
  or merged: the target codes are listed, never summed) or `unknown`. The holding data context
  (`trade_exposure`) uses it: an exposure mapped in HS2022 reads HS2017 trade rows only through
  `identical`/`aggregate_identical`, labels each row's `revision_basis`, prefers the mapping's own
  revision for the same flow, and lists non-comparable revisions in `revision_notes`.
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


## ALFRED over many vintage dates (2026-10-03)

FRED refuses a real-time range holding more than 2000 vintage dates with HTTP 400 (live: `DFF` has
5138 since 2005; `CPIAUCSL` is accepted in one request). `store_fred_vintages` first asks for the
whole range; on that 400 it lists the series' vintage dates (`fred/series/vintagedates`) and fetches
chunks of 1800, merging a window that continues across a chunk boundary (clipped to the chunk start)
into one. Live check: 3928 `DFF` windows for ten years, no duplicate (observation, vintage).
ECB: all nine configured flow keys answered 200 live (one transient 504 on
`MIR/M.U2.B.A2C.AM.R.A.2250.EUR.N` that succeeded on retry). Point-in-time context compares
`exposure_mapping.created_at` as a timestamp (a mapping entered today is visible today).
