# Prices, exchange sessions, corporate actions and returns

How the daily price series, the exchange calendar and the return artifacts fit together after the
prices-stream fixes (audit A1, A2, A15-A17, B21-B24, B26, D3.1, D3.4).

## Series and sources

Each source stores its bars under its **own provider symbol** in `price_bar`
(`cez.cz` Stooq, `CEZ.PR` Yahoo, the profile's `eodhd_symbol`, the profile's
`alpha_vantage_symbol`). The `source` CHECK admits `stooq`, `yahoo`, `eodhd`, `alpha_vantage`,
`twelve_data`.

* **Chosen series.** Every reader calls `price_series.choose_series`: of the stored adjusted series
  of a holding, the one with the newest bar; Stooq, Yahoo, EODHD, Alpha Vantage break ties in that
  order. A series is never stitched.
* **Fallbacks.** EODHD and Alpha Vantage run only for holdings still in the gap list, only with a
  confirmed listing and an explicit profile symbol. A gap is cleared **only if the fallback series
  is the one the readers now serve** (`prices._accept_fallbacks`); otherwise the gap stays, with
  the reason.
* **Bases.** `bar_type='adjusted'` is what readers serve (Stooq/Yahoo: split-adjusted; EODHD
  `adjusted_close` and Alpha Vantage `5. adjusted close` are rescaled onto the bar's open/high/low).
  `bar_type='raw'` holds the provider's unadjusted `close` (EODHD, Alpha Vantage only). Whether
  EODHD's plain `close` is split-adjusted is not verifiable offline, so total return checks every
  confirmed split against the raw closes and is unavailable when they do not show it.
* **Units.** `price_bar.currency` is the quote code the source reported (`GBX`, `GBp`, `EUR`);
  `quote_unit` is its canonical unit (`GBX` for both pence spellings). Stooq's is derived from the
  market suffix at write time; EODHD/Alpha Vantage report none, so it is inferred
  (`market.infer_quote_currency`: the instrument's pence code, else the exchange convention
  `XLON -> GBX`, recorded as `quote_unit_basis`; the convention is unverified against a live
  payload). Each bar also carries `listing_id`, `mic` and, where the source gives one,
  `source_timestamp`.

## Alpha Vantage budget (B24)

The 25-call day is account-wide (`account_free`). A price call reserves the account credit and the
endpoint credit in **one transaction** (`sources/credit_group.reserve_group`; a refused endpoint
credit never burns the account credit), leaves `ALPHA_VANTAGE_ACCOUNT_RESERVE` (5) calls to
Company Overview, and is refunded (`refund_provider_credits`) when the request never reached the
provider. HTTP-200 envelopes are typed: "premium endpoint" is `PAID_ONLY` (hard block), the
daily/per-minute limit text is `QUOTA_EXHAUSTED` (with `retry_at`), a bad key
`ACCESS_UNVERIFIED`, "Invalid API call" `NOT_COVERED` (per symbol, never an endpoint verdict).

## Canonical listings (A17)

`reference/listings.py` writes `security`, `security_listing`, `provider_identifier` (provider
symbol -> listing; this is how `cez.cz` / `CEZ.PR` find the listing whose ticker is `CEZ`) and the
`instrument_security_link` with match evidence, from the instrument (ISIN, currency, home MIC),
ESMA FIRDS (record on the home MIC, currency, lifecycle) and OpenFIGI (listing ticker on the home
exchange). The link is `confirmed` automatically only when all three agree; otherwise `suggested`
until the owner decides (`POST /holdings/{ticker}/listing/confirm`). An owner decision is never
overwritten by a re-derive. `price_history` derives listings before it stores bars.

## Exchange sessions (B21, B22)

Authority per day: a confirmed `exchange_session` row (`open`, `half_day` with its own
open/close, `closed` for an ad hoc closure), else a closure confirmed in the Jobs config, else a
weekday. Public holidays (Nager.Date) are suggestions only: an exchange that trades on a national
holiday needs no row, an ad hoc closure needs no holiday. `calendar.expected_sessions` is the
denominator of return coverage (never `max(sessions, bars)`); `intraday.market_open` follows the
rows, including half-days and daylight saving (sessions are anchored to the exchange's local
clock). `GET /exchange-sessions/{mic}/expected` shows the calendar in force.

## Corporate actions and returns (A15, A16, B23, B26)

* Confirmed actions are matched by listing id or any provider symbol of the holding, and the same
  action from two sources counts once (disagreeing values: total return unavailable).
* Dividends are split-adjusted by every later split, converted from their currency (`GBp`, `GBP`,
  `EUR`, ...) into the quote unit with the ex-date ECB fixing when the major currencies differ,
  multiplied by the ADS ratio when recorded per ordinary share (`raw.per = "ordinary_share"`),
  and reduced by the stated withholding rate. The fixing dates and the withholding assumption are
  stored in `details`.
* **Total return for Stooq and Yahoo series** (no raw closes exist for them): computed on the
  split-adjusted closes, `last * prod(1 + D/close_ex) / first - 1`, with the basis in
  `details.basis = split_adjusted_close_with_reinvested_dividends` (`reference/adjusted_total_return.py`,
  `RETURN_CALC_VERSION` 3). Dividends are the owner-confirmed ones (amounts "as declared" are
  brought onto the closes' basis by every later confirmed split; confirmed splits are never applied
  to the closes) plus, for a **Yahoo** series only, the dividends Yahoo lists in the same chart
  answer (`events=div,splits`; stored by `collect_yahoo` as `corporate_action` rows with
  `status='suggested'`, `source='yahoo'`, amounts split-adjusted; a confirmed or rejected row is
  never changed by Yahoo, a confirmed dividend wins its ex-date, a rejected one is a veto).
  Completeness is stated, not assumed: either the owner verified corporate-action coverage, or Yahoo's
  events were collected over the whole window (`coverage_status yahoo/dividend_events` records the
  range; a series stored before this existed is refetched once over its full history). The Yahoo
  basis is flagged as unofficial and unverified against the issuer in `details.warnings`. A confirmed
  split still visible in the closes, a spin-off/merger/delisting, conflicting sources, a dividend
  without an ex-date close, a missing FX fixing: unavailable with the reason. **Stooq publishes no
  dividend history**, so a Stooq series is computable only from owner-confirmed dividends with
  verified coverage and otherwise says so. `dividend_withholding_rate` in the holding profile (owner
  entered) reduces reinvested dividends; the `withholding_rate` query parameter overrides it;
  empty means gross.
* `price_raw` is the raw closes with no action applied; `price_adjusted` uses the adjusted bars;
  `total_return` needs raw closes, confirmed actions and owner-verified action coverage. A
  confirmed spin-off, merger, delisting or symbol change inside the window makes total return
  unavailable and marks the price rows incomplete in `missing_actions`.
* `stock_dividend` requires a ratio; a cash amount alone is rejected. Actions change status only
  through `POST /holdings/{ticker}/corporate-actions/{id}/decision` (confirm, reject, retract to
  `suggested`); a re-post never overrides a decided action.
* `returns_refresh` (job) stores the artifacts of the chosen series for 1m/3m/6m/1y windows as
  `price_return_series` rows versioned by `calc_version`. `GET /holdings/{ticker}/returns`
  computes the same figures and never writes.

## Per-exchange probes (D3.4)

`exchange_probes` asks Finnhub `/quote` and Twelve Data `/time_series` about a held symbol of
each exchange and records `coverage_status(provider, dataset, MIC)`: `not_covered` (the provider
knows nothing) is kept apart from `paid_only`/`access_unverified`/`quota_exhausted` (a technical
denial). A plan that excludes one exchange does not close the endpoint for the others.

## Not verifiable offline

EODHD/Alpha Vantage payloads were modelled on the documented formats, not recorded live:
EODHD's `close` split basis, LSE pence quoting at both providers, the exact wording of Alpha
Vantage's `Information` texts, Finnhub's empty-quote and 403 behaviour per exchange, and Twelve
Data's plan messages. Finnhub symbol suffixes for the probe are assumed to equal Yahoo's.
