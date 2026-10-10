# Quant risk plan: implementation audit (10 October 2026)

Scope: `docs/plans/quant_risk_implementation_plan.md` (Q0–Q8 and gaps G1–G104) against the code on branch `quant` at `e04fc1c` (everything since `fe33ba1`, the Morningstar merge). Read in full:
- `steward/quant/*`;
- `steward/portfolio/{exante,tail,replay,factors,factor_shock,idea_fit,position_pack,risk_limits,risk_snapshots,risk_tier,sizing,trade_risk,validation,concentration,drawdown_ladder,breaches,sync}.py`;
- `steward/decision/{allocator,expected_return,ranking,ranking_service,pretrade,service}.py`;
- `steward/journal/{process,switches,scorecard}.py`;
- `performance/evaluation.py`, `review/monthly.py`, the chat tools and the new API routes;
- the six quant migrations and their rollbacks;
- the web helpers `lib/risk/*`, the forms and the main risk views.

Method: every finding was traced from caller to callee in the code. Each one marked **confirmed** was checked by reading the code path, and a few were also run. **Plausible** means the code path exists, but whether it bites depends on live data or config. Candidate findings that turned out to be false are listed at the end, so they are not raised again.

Nothing was run against the live database or in a browser. No code was changed by this audit.

Severity scale:
- **High**: wrong alerts or wrong figures in normal use.
- **Medium**: a wrong or missing figure in a reachable case, or a plan claim that is not true.
- **Low**: an edge case, a mismatch with the documentation, or performance.

## Fix status

R1 to R35 are all addressed (not committed), with regression tests in `tests/test_quant_audit_fixes.py` and `tests/test_tail.py`. Migration `quant_audit_fixes` was applied to the live StewardAI database (10 October 2026). The strategy files carry the D5 values, but the stored strategy version on the live database was not changed: save the file's values through the app to switch the limits on.
- R28: the over-cap report divides by the invested value when `weight_basis` is "invested". Ex-ante rows already carry both `weight` and `weight_of_invested`.
- R33: `start_size` builds the probe book (with the new stock's series) once; each bisection step only changes the weights (`book_after(..., known=...)`).
- R34: `max_es_95_month` 0.12, `max_drawdown_probability` 0.25 and `drawdown_probability_years` 2 are in `strategy.json` and `strategy-import.json`, and principle 4(i) names them.
- R27: `RiskLimits` rejects `drawdown_warnings` that fall in the same whole percent.
- R29: the factors page backfills over the same 156-week window as the ex-ante model, then regresses on the last 104 weeks.
- R30: the back-test weights simple returns (`expm1`), as the tail report does.
- R31: fills of one ticker, side and day are added up before pairing; the future-date check uses `local_today()`; the stored close's currency is kept even when the owner gives the price.
- R32: `tail_report(contributions_only=True)` for the thesis risk pack (no tail lines, no simulation). The replay is still run per open.
- R35: the plan's "Still open" paragraph and the G103 note are corrected; unticked Q0/Q1/Q3.2/Q3.3 boxes are noted, not ticked.
- R1/R14: `record_breaches(unmeasured=...)` keeps open breaches whose measurement failed (`risk_limits.unmeasured_keys`); the book load, ex-ante, validation, tail and snapshot steps are separate; a failed `risk_tiers` step falls back to the stored tiers.
- R2: `bootstrap.simulate(start_drawdown=...)`; the tail report passes the book's drawdown from its one-year peak and shows it as `start_drawdown`.
- R3: `trade_risk.findings_for` runs the tail inputs (`full=False`) when an ES or drawdown-probability limit is set; the stale `NOT_MODELLED` line is reworded.
- R4: `es_95_month` is the larger of historical and filtered historical (the documented definition); the web headline sentence drops Cornish-Fisher too.
- R5/R26: migration `20261027100000_quant_audit_fixes.sql` keys snapshots on a content hash; NaN is written as null.
- R6: the allocator uses `price_in_unit`.
- R7: `BadZipFile` and `ValueError` are caught per file.
- R8: same migration drops the `authenticated` insert on `switch_log`.
- R9: `switch_effect` and `position_risk` resolve tickers.
- R10/R11: the partial run is guarded, and the action is judged on its own gap and hurdle.
- R12: `pretrade.expected_return` and the thesis `bear_case` use `price_in_unit`.
- R13: `wants_book` includes `max_stock_risk_share` and `thesis_volatility_multiple`.
- R15: a stock with no fundamentals row is a stock when the broker lists it as `STOCK`; it can no longer be "core" (growth, with the reason).
- R16: a derived tier that differs from the stored one is listed in the `risk_tiers` step detail as `tier_changes`.
- R17: each tail line carries `independent_tail`, and a warning names figures resting on fewer than 5 independent tail periods.
- R18: Cornish-Fisher returns no estimate when its quantile is not monotone.
- R19: FHS decay uses the 26-week half-life of the covariance model; the bootstrap block is 4 weeks; "one month = 4 weeks" is named in the report's not-modelled list. Method versions bumped (`tail-2`, `bootstrap-2`, `tail-report-2`), so earlier saved reports keep their numbers.
- R20: Kelly annualises a sell-target thesis over the ranking's horizon (catalyst date, else two years).
- R21: a volatility of exactly 0 gives a reason.
- R22: the snapshot route uses `IdPath`.
- R23: chat `deposit_allocation` and `risk_whatif` have schema bounds and the same runtime bounds as the routes; a bad value returns an error, not a `TypeError`.
- R24: `trade_risk` warns about a sale above the holding and buys above cash plus deposit plus proceeds.
- R25: the average deposit divides by the months the account has existed (1 to 12) and counts only base-currency deposits.
- Not applied to any database. `openapi.json` and the web `schema.d.ts` are regenerated (one new field, `start_drawdown`).

## Summary

| # | Sev | Area | Finding | Status |
| --- | --- | --- | --- | --- |
| R1 | High | Sync / breaches | A failed or skipped measurement clears open risk breaches; the next good sync re-opens and re-alerts | Confirmed |
| R2 | Medium | Tail (Q2.2/Q2.4) | Drawdown probability starts every path at a fresh peak and ignores the book's current drawdown | Confirmed |
| R3 | Medium | Pre-trade (Q5.1) | Before/after, start size and allocator never test the ES and drawdown-probability limits; plan says done | Confirmed |
| R4 | Medium | Tail limit | `es_95_month` takes the max over three methods incl. Cornish-Fisher; the config field says historical + filtered only | Confirmed |
| R5 | Medium | Snapshots (rule 5) | One snapshot per kind per week: later reports in the same week are dropped; sync copy lacks realized vol | Confirmed |
| R6 | Medium | Allocator | GBX price vs GBP target is not scaled: return ≈ −99 %, candidate never bought | Confirmed |
| R7 | Medium | Factor job | A non-zip response raises `BadZipFile`, not caught; job aborts and the remaining files are skipped | Confirmed |
| R8 | Medium | Security | `switch_log` grants `insert` to `authenticated`, undoing the append-only hardening of `20261023100000` | Confirmed |
| R9 | Medium | Chat | `switch_effect` / `switch_pair` / `position_risk` skip `resolve_tickers`; lower-case tickers fail | Confirmed |
| R10 | Low-Med | Switch scan | Partial switch: hurdle and gap shown from the full-size run, tax from the partial run; action not recomputed | Confirmed |
| R11 | Low | Switch scan | The partial-size re-run is outside the try/except; one failure errors the whole scan | Confirmed |
| R12 | Low-Med | Currency | G83 only half-closed: pre-trade `expected_return` and `bear_case` still compare currency strings | Confirmed |
| R13 | Low-Med | Limits | `wants_book` omits `max_stock_risk_share` and `thesis_volatility_multiple` (both on by default) | Confirmed |
| R14 | Low | Limits | One try block for book inputs, validation and tail: one exception loses ES/drawdown findings silently | Confirmed |
| R15 | Low-Med | Tiers | Stock with no fundamentals row is treated as a fund → can be auto-tiered "core" | Confirmed |
| R16 | Low | Tiers | With `auto_tiers` on, the D6 tier-criterion check can never fire (tier derived from the same rule) | Confirmed |
| R17 | Low | Tail | 99 % and 1-month figures rest on overlapping windows; `observations` overstates the sample, no warning | Confirmed |
| R18 | Low | Tail | Cornish-Fisher quantile is not checked for monotonicity at high skew/kurtosis | Plausible |
| R19 | Low | Constants | FHS λ 0.94 (weekly half-life ≈ 11 wk) vs model 26 wk; bootstrap block 8 wk vs plan ~4 wk; "month" = 4 weeks | Confirmed |
| R20 | Low | Sizing | Kelly bound annualises a sell-target thesis over `horizon_years or 3`, the ranking over the catalyst date or 2 years | Confirmed |
| R21 | Low | Ranking | Volatility exactly 0 gives no score and no reason | Confirmed |
| R22 | Low | API | `GET /portfolio/risk/snapshots/{snapshot_id}` unbounded int → 500 on overflow | Confirmed |
| R23 | Low | Chat | `deposit_allocation` / `risk_whatif` lack schema bounds; a `None` value raises an uncaught `TypeError` | Confirmed |
| R24 | Low | Trade risk | Sell above holding or buy above cash is clipped or allowed silently (weights > 1, negative cash) | Confirmed |
| R25 | Low | Concentration | Monthly deposit = last-365-days sum ÷ 12 regardless of account age; no FX conversion | Plausible |
| R26 | Low | Snapshots | A NaN in a report makes `::jsonb` fail; the snapshot is dropped with only a log line | Plausible |
| R27 | Low | Ladder | Drawdown-warning keys `round(rung*100)` collide for close rungs (0.075/0.08) | Plausible |
| R28 | Low | Weight basis | Ex-ante and concentration ignore `weight_basis` (G16 marked closed) | Plausible |
| R29 | Low | Factors | Factor fit uses a 104-week window, ex-ante 156: same holding "real" on one page, "proxied" on the other | Confirmed |
| R30 | Low | Validation | Back-test portfolio series is a weighted sum of log returns; tail uses simple returns | Confirmed |
| R31 | Low | Journal | Switch detection pairs one sell fill with one buy fill; split fills missed. `dt.date.today()` vs `local_today()` | Plausible |
| R32 | Perf | Thesis pack | `position_pack` reruns the full replay (2008+ history) and three 10k-path bootstraps per call | Plausible |
| R33 | Perf | Sizing | Each bisection probe rebuilds the whole return matrix and report (~18 full builds per request) | Confirmed |
| R34 | Plan | Strategy files | D5 values (`max_es_95_month` 0.12, `max_drawdown_probability` 0.25) are not in the strategy files | Confirmed |
| R35 | Plan | Plan document | Several status lines contradict each other or the code | Confirmed |

## Findings in detail

### R1 (high). A failed measurement clears open breaches, then re-alerts

- Where: `portfolio/breaches.py:258-263`, `portfolio/risk_limits.py:551-560` and `597-600`, `portfolio/sync.py:140-153`.
- Defect: when a measurement fails or comes back with too few weeks, `gather_risk_inputs` records it only in `inputs.unavailable`, which the sync writes to the step detail. `record_breaches` then closes every open row whose key is missing from this run's findings: `for key, row_id in open_rows.items(): if key not in current: ... cleared_at`.
- Scenario: `risk:volatility_forecast` is open. On the next sync the book load raises, for example on a price-history gap. The finding is not produced, so the breach is cleared. On the sync after that the measurement works again, a new breach row opens and the owner gets the same alert a second time.
- The same happens when the `risk_tiers` step fails (`sync.py:152`). `strategy` stays untiered for that run, so the tier-budget findings and any rule over the "risk" dimension are evaluated with no tiers. They clear or misfire. There is no fallback to `with_saved_tiers`.
- The realized-volatility path had this pattern before the quant work. The quant work adds about ten more finding keys that inherit it.
- Fix: in `record_breaches`, keep an open row whose limit family is in `unavailable`. Fall back to the stored tiers when `risk_tiers` fails.

### R2 (medium). The drawdown probability ignores the current drawdown

- Where: `quant/bootstrap.py:112` (`growth = cumprod(...)` starting at 1), `portfolio/tail.py:209-211`, `risk_limits.py:780-795`.
- Defect: every simulated path starts at 1.0 and treats today as the peak. The limit it feeds, principle 4g, measures the 20 % fall from the one-year peak.
- Scenario: the book is already 12 % below its one-year peak. A further 9 % fall breaches the real limit. The simulation still asks for a 20 % fall from today. P(drawdown ≥ 20 % in 2 years) is understated exactly when it matters, and so is the `risk:drawdown_probability` finding.
- Fix: seed each path with today's running peak, starting at `1 / (1 − current_drawdown)`, or report both the "from today" and the "from the 1-year peak" figures.

### R3 (medium). Pre-trade, start size and allocator do not test the ES or drawdown limits

- Where: `portfolio/trade_risk.py:40-43` and `139-145`, `portfolio/sizing.py:172-190`.
- Defect: `findings_for` calls only `_book_inputs`, never `_tail_inputs`. `NOT_MODELLED` still says ES and drawdown probability are "not in the risk model yet (plan phase Q2)". Meanwhile the plan marks Q5.1 [x], lists ES and drawdown probability in it, and marks G43 closed.
- Scenario: a buy that lifts the one-month ES above `max_es_95_month` passes the pre-trade check ("No new or deeper ex-ante risk-limit breach"). The next sync then opens `risk:es`. `start_size` and the deposit allocator can suggest the same size.
- Fix: run `tail_report(after_book, full=False)` inside `findings_for` and merge in `_tail_inputs`. Drop the stale `NOT_MODELLED` line. Or correct the plan.

### R4 (medium). `es_95_month` includes Cornish-Fisher

- Where: `portfolio/tail.py:188-189`, and `config/strategy.py:253-258` for the field description.
- Defect: the code is `month = [x.es for x in out.lines if x.horizon_weeks == 4 and x.level == 0.95]; es_95_month = max(month)`. That covers all three methods. The config field says "from the larger of the historical and filtered-historical estimates".
- Scenario: Cornish-Fisher on about 150 overlapping 4-week windows with a noisy kurtosis estimate, and no monotonicity check (R18), can exceed both other estimates. It then raises the severity-2 `risk:es` finding on its own.
- The web helper `lib/risk/tail.ts:worstLine` also takes the max over all three methods. So the page and the limit agree with each other; both disagree with the documented definition.
- Fix: decide which is intended, then align the code, the field description and the web helper.

### R5 (medium). Snapshots keep only the first report of each week

- Where: `portfolio/risk_snapshots.py:44-48` (`on conflict (kind, scope, as_of, method_version) do nothing`) and `risk_limits.py:591-596`.
- Defect: `as_of` is the last Wednesday of the weekly grid. The first save in a week wins: that is the sync's copy, which also has `realized_volatility=None`. Every later report in the same week is dropped, including one after a trade, a deposit or a price move, and the page's fuller report.
- Effect: engineering rule 5 ("snapshot what the owner saw") is not met for anything after the first save of the week.
- Fix: add a book fingerprint (snapshot date or total value) to the unique key, or upsert by day.

### R6 (medium). The allocator mis-scales GBX prices against GBP targets

- Where: `decision/allocator.py:529-536`.
- Defect: the guard compares `normalise_currency(...)[0]`, so GBX and GBP count as equal. The return is then `value / price − 1` with no scaling, about −99 %. The candidate falls under the hurdle and is never bought.
- The G83 fix (`price_in_unit`) was applied to the ranking and to Kelly, not here.
- Fix: use `price_in_unit(price, currency, target.currency)`, and treat None as not comparable.

### R7 (medium). The factor job aborts on a non-zip response

- Where: `quant/french_store.py:77-84`.
- Defect: `except (httpx.HTTPError, FrenchFormatError, OSError)` does not catch `zipfile.BadZipFile`, which derives directly from `Exception` (checked). `ctx.step` re-raises, so the loop stops.
- Scenario: the Dartmouth server returns a 200 with an HTML maintenance page for one file. The job fails, and every file after it in `FILES` is skipped. This contradicts the module's promise that "one file failing leaves the others".
- Fix: add `zipfile.BadZipFile` to the caught exceptions, or catch `Exception` per file.

### R8 (medium). `switch_log` can be written directly by a signed-in user

- Where: `supabase/migrations/20261025100000_switch_log.sql:27-30`.
- Defect: the migration grants `select, insert` to `authenticated` and adds `switch_log_owner_insert`. Migration `20261023100000_audit_fixes.sql:3-18` had revoked this path on every append-only table ("written only through the API").
- Scenario: an insert through PostgREST skips the checks in `journal/switches.py:85-98`: the future-date check, the price lookup and the ranking snapshot.
- Fix: grant only `select` to `authenticated` and drop the insert policy. Add the matching rollback note.

### R9 (medium). Chat switch and position tools skip ticker resolution

- Where: `chat/analytics_tools.py:410` (`switch_effect` passes `sell.strip()`), `decision/ranking_service.py:342-352` (`positions.get(sell)`), and `analytics_tools.py:224` (`position_risk`).
- Defect: Trading212 tickers are case-sensitive (`CSPXl_EQ`). "What does switching aapl_us_eq for X do" returns "aapl_us_eq is not held". `risk_whatif`, `start_size` and the REST routes do resolve tickers.
- Fix: call `resolve_tickers` on both legs, and in `position_risk`.

### R10 (low-medium). A partial switch shows a stale hurdle

- Where: `decision/ranking_service.py:438-476`.
- Defect: for a partial action the pre-trade check is re-run at the smaller size and `values` is refreshed. `gap` and `hurdle` are still the full-size ones, while `tax` and `costs` come from the partial run.
- Scenario: the partial sale falls under the annual tax exemption, so tax is 0, yet the hurdle shown still contains the full-size tax. `switch_action` is not re-evaluated with the lower hurdle.
- Fix: re-read `gap` and `hurdle` from the new `values` and recompute the action.

### R11 (low). The partial-size re-run is unguarded

- Where: `ranking_service.py:443`.
- Defect: only the first `_run` is inside try/except. An exception on the re-run propagates out of `switch_suggestions`, so `GET /theses/switches` and `rotation_candidates` fail for every pair, not just this one.

### R12 (low-medium). G83 is only half-closed

- Where: `decision/pretrade.py:204` (`expected_return`) and `decision/service.py:645-649` (`bear_case`).
- Defect: both still compare currency strings exactly.
- Scenario: the ranking row of a GBX-quoted stock with a GBP target shows returns. The switch scan for the same stock reports `switch.gap` and `buy.hurdle` as unavailable and says "hold".
- Fix: use `price_in_unit` in both places.

### R13 (low-medium). Two default-on limits never load the book

- Where: `risk_limits.py:563-572` and `602-603`.
- Defect: `wants_book` lists neither `max_stock_risk_share` (default 0.08) nor `thesis_volatility_multiple` (default 1.5). `_thesis_volatility` also needs `book is not None`.
- Scenario: a strategy without the other book limits, with `auto_tiers` off, silently gets no per-stock risk-share finding and no thesis-volatility finding, and no "unavailable" note either. The repo strategy sets `max_volatility`, so this is dormant today.

### R14 (low). One exception loses several findings

- Where: `risk_limits.py:578-600`.
- Defect: `_book_inputs`, `validate_book` and `_tail_inputs` share one try block. If validation raises, ES and the drawdown probability are never computed. They are not added to `unavailable`, so through R1 their open breaches clear. Meanwhile the four ex-ante names are marked failed even though they were computed.
- Fix: use separate try blocks, and name `es` and `drawdown_probability` in the handler.

### R15 (low-medium). A stock with no fundamentals is treated as a fund

- Where: `portfolio/risk_tier.py:62-64` and `91-96`.
- Defect: `stock = facts is not None and facts.is_stock`. A stock with no Alpha Vantage or SEC row is judged as a fund, by volatility alone.
- Scenario: a small European stock with 22 % volatility and no fundamentals is auto-tiered "core". That loosens its tier caps and its tier budget.
- Fix: decide the stock/fund question from the instrument type (`ETF`), as the allocator does, and give a stock without facts no tier, or "growth", with a reason.

### R16 (low). With `auto_tiers` on, the D6 check cannot fire

- Where: `risk_tier.derive_tier` and `exante.tier_checks`, which use the same thresholds and volatility.
- Defect: a holding that crosses 35 % volatility re-tiers itself to speculative. The "flag for review, the owner reclassifies" step in D6 then only works for tiers the owner entered by hand.
- Note: `strategy/strategy.json` has `"auto_tiers": true`, while plan line 419 says it is off on the live database (strategy v65).
- Fix: when a derived tier changes between syncs, raise a note so the change is visible.

### R17 (low). The 99 % and 1-month tail figures rest on thin, overlapping samples

- Where: `quant/tail.py:46-66` and `portfolio/tail.py:167-189`.
- Defect: 156 weekly returns give about 153 overlapping 4-week windows, but only about 38 independent ones. The 99 % ES is the mean of about 2 windows, usually from the same episode. `observations` reports 153 and there is no warning.
- Fix: report the independent count (n ÷ horizon) and add a warning when the expected tail count is below about 5.

### R18 (low, plausible). Cornish-Fisher can become non-monotone

- Where: `quant/tail.py:95-122`.
- Defect: the expansion is only valid in a bounded skew/kurtosis domain. Outside it the quantile function folds over. The ES clamp `max(es, var, 0)` hides the symptom, not the cause.
- Fix: check the domain, or that the quantile is monotone on the grid. If it fails, drop the CF line with a reason. This matters because of R4.

### R19 (low). Constants differ from the plan

- `quant/tail.py:26`: `EWMA_LAMBDA = 0.94` is RiskMetrics' daily decay, applied to weekly data (half-life about 11 weeks). The plan, the covariance model and the validation use 26 weeks (λ 0.974). So FHS and the back-test test different models.
- `quant/bootstrap.py:22`: the mean block is 8 weeks, where the plan says about 20 trading days, roughly 4 weeks.
- `portfolio/tail.py:27`: "one month" is 4 weeks, not 4.33. Under √t scaling this understates the ES by about 4 %.

### R20 (low). Kelly and the ranking annualise over different horizons

- Where: `portfolio/sizing.py:297-301`.
- Defect: a thesis with only a sell target is annualised over `strategy.horizon_years or 3`, even when it has a `catalyst_date`. The ranking uses the catalyst date, or 2 years when unset.
- Effect: the same thesis gets two different annualised returns on two pages.

### R21 (low). A volatility of 0 gives no score and no reason

- Where: `ranking_service.py:169`.
- Defect: `elif vol:` has no `else`, so the row has no score and no reason. The Q8 exit check requires that a missing figure says why.

### R22 (low). Unbounded snapshot id

- Where: `api/routes.py:204-205`.
- Defect: `snapshot_id: int` is not bounded, unlike `IdPath` elsewhere in the file. A huge id overflows int8 in asyncpg and returns a 500.

### R23 (low). Chat tool inputs are less validated than the routes

- Where: `chat/analytics_tools.py:272-290` and `340`.
- Defect: `deposit_allocation` has no `exclusiveMinimum` or maximum, where the REST route has `gt=0, le=1e9`. `risk_whatif` has no `minItems` and no bound on `deposit`. A `None` value in `float(t["value_base"])` raises `TypeError`, which `except (KeyError, ValueError)` does not catch.

### R24 (low). Impossible trades are clipped silently

- Where: `portfolio/trade_risk.py:114-125`.
- Defect: selling more than is held becomes "sell all" with no note. A buy larger than cash plus deposit pushes position weights above 1, which means negative cash at zero volatility, again with no warning.

### R25 (low, plausible). The average monthly deposit can be wrong

- Where: `portfolio/concentration.py:148-159`.
- Defect: the deposit is the last 365 days' sum divided by 12. For an account younger than a year this understates it, and the dilution path then shows too long a wait. The deposit amount is not converted to the base currency.

### R26 (low, plausible). A NaN drops the snapshot

- Where: `risk_snapshots.py:52-56`.
- Defect: `json.dumps` writes `NaN`, which Postgres `::jsonb` rejects. The exception is swallowed and the snapshot silently missing.
- Fix: use `allow_nan=False` and sanitise NaN to null.

### R27 (low, plausible). Drawdown-warning keys can collide

- Where: `risk_limits.py:324`.
- Defect: `round(rung * 100)` maps 0.075 and 0.08 to the same key, so one finding hides the other.
- Fix: validate distinct integer-percent rungs in the config.

### R28 (low, plausible). The weight basis is ignored (G16 marked closed)

- Where: `portfolio/concentration.py:101` and `exante.py`.
- Defect: both always use total-value weights. With `weight_basis: invested`, the risk pages and the drift rules show weights on different bases. This is dormant while the live strategy uses "total".

### R29 (low). Factors and ex-ante can disagree on a holding's status

- Where: `portfolio/factors.py:224` and `253`.
- Defect: the factors code backfills within 104 weeks, the ex-ante model within 156. A holding with 104 to 155 weeks of history is "real" on the Factors page and "proxied" on Today's risk.

### R30 (low). The back-test sums log returns

- Where: `portfolio/validation.py:97`.
- Defect: the series is `log_returns @ weights`, where the tail report uses `expm1` first (simple returns). The difference is small at weekly frequency, but the two modules test slightly different series.

### R31 (low, plausible). Switch-log edge cases

- `journal/switches.py:164` pairs one sell fill with one buy fill. A Trading212 order filled in several rows fails the 50–115 % test and the switch is missed.
- `journal/switches.py:88` checks future dates with `dt.date.today()`, where the ranking uses `local_today()`.
- A switch logged with an explicit price has `sold_currency = None`, so `scorecard._change` skips the currency check.

### R32 (performance, plausible). The thesis risk pack is expensive

- Where: `portfolio/position_pack.py:161` and `166`.
- Defect: each open of a held card reruns `stress_replay` (2008-onward history of every series, plus a snapshot write) and a full `tail_report` (three 10,000-path bootstraps) to read one number.
- Fix: cache per book, or call `tail_report(full=False)`.

### R33 (performance). Sizing rebuilds everything at every probe

- Where: `portfolio/sizing.py:187-190` and `trade_risk.py:126-135`.
- Defect: every bisection probe on a new ticker rebuilds the return matrix with per-day FX, then the backfill, the report and the findings. That is about 18 full builds per request.
- Fix: build the matrix once and vary only the weights.

### R34 (plan). D5 values are not in the strategy files

- Defect: `strategy/strategy.json` and `strategy-import.json` set neither `max_es_95_month` (D5: 0.12) nor `max_drawdown_probability` (0.25), and `drawdown_probability_years` is not set either.
- The plan's D4/D5 note says to add them when Q2.4 is implemented, and principle 4 in the strategy notes does not name them.
- Plan line 419 records them as deliberately unset on the live strategy. That conflicts with the accepted decision D5 and should be decided explicitly.

### R35 (plan). Contradictions in the plan document

- Line 423 "Still open" says "Q2 (Expected Shortfall and drawdown probability) is not started", G43 is still open and the `switch_log` migration is "not applied". Lines 404–409 say Q2.1–2.3 are built, G43 is closed and `switch_log` is applied.
- G103 is "closed" at line 419 and then, at line 420, the monthly rows "are still in `price_bar`… not deleted".
- Q3.2 and Q3.3 are unchecked `[ ]`, although G30 and G31 are listed as closed in code (factor shocks, saved scenarios). The Q0 and Q1 task boxes are all unchecked although they are built.
- G43 is closed, yet `trade_risk.NOT_MODELLED` still says Q2 is not built (R3). G83 is closed, but R6 and R12 remain. G16 is closed, but see R28.
- `strategy.json` has `auto_tiers: true`; the plan says it is off live (R16).

## Checked and found correct

These were examined and are not defects. Do not re-raise them.

- **Return matrix:** FX is taken on the grid day for carried closes (the CZK value today), and the Wednesday grid is right. Missing levels give NaN, not zero.
- **Proxy backfill:** it leaves no NaN in a column marked real or proxied. Holdings are never proxy candidates (only `benchmark` and `proxy:*` keys are), so a holding cannot be mislabelled "real" while still having gaps.
- **Covariance:**
  - Ledoit-Wolf is pinned to PyPortfolioOpt output; the mixed t / t−1 divisors match that reference.
  - EWMA weights are normalised and zero-mean. Annualisation is ×52 for variance and √52 for volatility throughout.
  - Euler shares sum to 1, and ENB entropy uses eigen-exposures whose shares sum to w′Σw.
- **Back-test (Q6.1):**
  - `rolling_ewma_sigma` has no look-ahead.
  - Normalising the back-test weights to the invested part is harmless, because hits and the calibration ratio are scale-invariant.
  - A univariate EWMA on the portfolio series equals w′Σ_EWMA w for constant weights.
  - `var_backtest` returning `passed=True` with fewer than two weeks is unreachable: the caller requires 26 tested weeks.
- **Statistical formulas:**
  - Kupiec and Christoffersen handle the edge cases of 0 hits and no clusters.
  - `chi2_sf` matches the closed forms for 1 and 2 degrees of freedom.
  - The PSR and MinTRL formulas match Bailey and López de Prado.
  - Brinson-Fachler reconciles exactly.
- **Stress replay:** `after = before + Σ pnl` holds, and the proxy beta is applied in log space.
- **Factor regressions:** the Newey-West sandwich and the Bartlett weights are correct. The weekly factor window (end − 7, end] matches the Wednesday-to-Wednesday returns.
- **Decision math (Q8):**
  - Expected return, annualisation (3-month floor, 2-year default, catalyst in the past), the asymmetry ratio, Kelly at ¼, the CAPM equilibrium and the conviction-to-confidence mapping are all correct.
  - The scenario validator (bear < base < bull, probabilities summing to 1 within 1e-6) is correct, and `exclude` is rejected on a single-ticker rule.
  - `switch_action` matches the plan, and the equal-risk size is capped at the sale proceeds (G86, documented).
- **Allocator:** the cash reserve holds, remaining cash never goes negative, the halving loop terminates, and the `_worse` baseline is "deposit left in cash".
- **Caching:** the shared `LoadedBook` cache is never mutated (`book_after` and `book_with_idea` copy it).
- **SQL:** each `ON CONFLICT` target matches its unique key, and the tail migration drops the right constraint name.
- **Frontend:**
  - `formatPercent` takes fractions, so nothing is multiplied by 100 twice. The `?? []` guards are present on the new list fields.
  - The strategy and theses form round-trips keep null vs default and convert percentages to fractions correctly.

## Rejected candidate findings (false positives)

- "The web page's bad-month ES differs from the limit's." It does not: both take the max over all three methods. The real defect is that both disagree with the documented definition (R4).
- "A proxy-candidate holding keeps NaNs while marked real." Not reachable, because holdings are never candidates.
- "The validation back-test passes with no data." Not reachable, because of the caller's guard at `validation.py:101`.
- "Normalised weights distort the back-test." They do not: both sides of the hit test scale together.
- "The Ledoit-Wolf divisor mismatch (t vs t−1) is a bug." It is not: it is pinned to the reference output by a golden test.

## Suggested order of fixes

1. R1 and R14: alerts that clear and re-fire are owner-visible noise and erode trust in every limit.
2. R2, R3 and R4: the Q2 limits are not measuring what the strategy says.
3. R6, R12 and R9: currency and ticker correctness in decisions.
4. R8: restore the append-only rule on `switch_log`.
5. R7 and R5: robustness of the factor job and of the snapshots.
6. R35 and R34: fix the plan document and decide the D5 values explicitly.
7. The rest as they come up; R32 and R33 if the pages feel slow.
