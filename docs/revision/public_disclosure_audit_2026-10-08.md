# Public-disclosure datasets: audit, 2026-10-08

Audit of [the plan](../plans/public_disclosure_datasets_implementation_plan.md) against the code on the `quant` branch (HEAD `98724b1`) and the StewardAI production database. Method: the plan was read in full; production was queried read-only; three review passes (backend P0–P5, backend P6–P9, frontend) re-read the code for every finding and dropped what they could not substantiate. **✔** = re-checked by hand (code read, production query or reproduced case); unmarked = verified against the code by the review pass.

Baseline at audit time: 430 offline backend disclosure tests pass (127 DB tests skipped), 133 web disclosure tests pass, `tsc` and eslint clean. None of the defects below is covered by a test.

Status column: **fixed** (in the working tree on `quant`, not yet committed; the schema parts need migration `20261008200000_disclosure_audit_fixes`, applied to production on 2026-10-08, see section 8), **partly** (what remains is stated), **owner** (needs the owner: deploy, production writes, human labels), **open** (listed in the plan, still open).

## 1. Migrations and production state

| # | Finding | Status |
|---|---|---|
| 1.1 | All 13 disclosure migrations (`20261007100000`–`20261008120000`) are applied; no file changed after it was applied; key objects exist. ✔ | ok |
| 1.2 | Migration history carries apply-time versions, not the file versions; `supabase/manual/reconcile_migration_history.sql` not run. ✔ | owner |
| 1.3 | **Scheduled disclosure jobs never ran in production.** Each `disclosure_*` job has one row (manual run 2026-10-07 ~19:00 UTC); on 2026-10-08 other crons ran but none of the disclosure ones; `disclosure_source.last_checked_at` all 2026-10-07. The disclosure code is not on the main branch `claude/nice-noether-jbgj4d` (`quant` is 305 commits ahead), so the deployed worker most likely has no handlers. The plan's "Automated now" line is false in practice. ✔ | owner (deploy) |
| 1.4 | Production is thin: `extracted_claim`, `quality_review`, `disclosure_coverage`, `disclosure_entity_mapping`, `deal_event`, `public_estimate_snapshot`, `uk_tr1_notice` all 0 rows; newest observation `known_at` 2026-09-11. ✔ | follows 1.3 |
| 1.5 | The plan's `evidence_span` is `source_evidence` (naming only). | ok |

## 2. Accuracy gates and coverage report

| # | Finding | Status |
|---|---|---|
| 2.1 | **Model labels count as human.** `adjudication.measure` and `coverage_report.quality_rows` never filter by who labelled; all 179 production labels are `claude-model (not a human)`, so the insider-class gate (54/55, bar 98%) reads **met**. ✔ | fixed |
| 2.2 | "Save the unflagged suggestions as my labels" stores model output as the owner's label with no confirmation; the suggestion is shown even when "reveal" is off (not blind). ✔ | fixed |
| 2.3 | `quality_rows` counts correctly rejected planted false positives as errors and pools all samples; `measure_labels` excludes planted items, so the two disagree. ✔ | fixed |
| 2.4 | No gate for the 30 deal states (`GATES` has no such dataset); the register's "coded but not run" is wrong. ✔ | fixed |
| 2.5 | Relationship gate unreachable at the default `size=50` (43 real claims, needs 100); the sample includes already-rejected rows, biasing precision down. | fixed |
| 2.6 | `record_pair_review` accepts any two values of one issuer, so non-consecutive pairs can fill the 20-pair revision gate. | fixed |

## 3. Backend: high

| # | Finding | Status |
|---|---|---|
| 3.1 | `disclosure_observation_identity` omits `period_start`, unit and currency: a 10-Q's 3-month and 9-month values collide (one dropped; `asof` `distinct on` returns one). ✔ | fixed |
| 3.2 | `estimate_value_identity` omits `period_start`: FY2026 and FY2026Q4 share 2026-12-31 (reproduced) → UniqueViolation not caught by `collect_consensus` → job aborts. Extractor never enforces `low <= mean <= high`. Surprise/read code keys on `period_end` only. ✔ | fixed |
| 3.3 | `parse.py` rejects `ixt:num-dot-decimal` / `num-comma-decimal` (TR4 hyphenated names) as unsupported. ✔ | fixed |
| 3.4 | `deal_job.state_for` checks Item 1.02 before 2.01 (closings often carry 1.02); transition inserted without the state-machine check; `terminated` alert follows. ✔ | fixed |
| 3.5 | Surprise "release" time is the 10-Q/10-K filing date (or ingest time), so consensus published between the earnings release and the filing counts as pre-release. ✔ | fixed: the ingest job indexes every 8-K's items from the submissions listing into `sec_filing_item`; a surprise uses the first Item 2.02 8-K known within 75 days of the period end, else the period end, and says which (`release_basis`) |

## 4. Backend: medium and low

Point in time and replay
| # | Finding | Status |
|---|---|---|
| 4.1 | `asof.py` filters on the current `review_state`: a later verification/rejection rewrites past replays. | fixed |
| 4.2 | `deal_parties.confirm` updates `deal_participant.issuer_id` in place; earlier as-of reads see later matches; `reject` after `confirm` leaves it set. | fixed |
| 4.3 | Share-count lookups use `filed <= date` (known_at is end of day UTC); `insider_analysis` admits `filed is null` at any date. | fixed |
| 4.4 | Replay "since" uses stable ids for deals/ownership/insiders, so later state changes and amendments never surface. | fixed |

Insiders and alerts
| # | Finding | Status |
|---|---|---|
| 4.5 | Insider alert dedupe key uses split-adjusted shares/price (re-fires after a split), has no ticker or accession. ✔ | fixed |
| 4.6 | Materiality is per Form 4 line; a large sale in small lots never alerts. | fixed |
| 4.7 | Exercise-and-sale bundles sum Table I and Table II rows of one exercise, hiding up to 2× the sale. | fixed |
| 4.8 | PDMR dedupe collapses genuinely identical fills. | fixed |
| 4.9 | Controlling-stake alert treats `prior=None` as crossed; no verified filter. | fixed |
| 4.10 | Estimate-revision scan skips `visible()`; closed reviews reopen daily. | fixed |
| 4.11 | Relationship-change alerts fire every fiscal year for a continuing customer (diff by claim id). | fixed |
| 4.12 | `scan_all` catches only PostgresError/ValueError; debt-maturity scan has no time window; digest drops alerts past 30 without a note. | fixed |

Dependencies and statements
| # | Finding | Status |
|---|---|---|
| 4.13 | Concentration sums the same customer from a 10-K, 10-Q and 10-K/A for one fiscal year. | fixed |
| 4.14 | Scenario edges are not reduced to one period per pair; coverage sums across years. | fixed |
| 4.15 | `segment_bridge` mixes 3-month and 9-month durations. | fixed |
| 4.16 | `statement_read` ignores unit/currency; a zero repurchase reads as missing; note period end may be a forward-dated context. | fixed |

Deals
| # | Finding | Status |
|---|---|---|
| 4.17 | "$45.00 in cash per share" also stored as `cash_amount` 45. | fixed |
| 4.18 | "merger agreement" tested before "to acquire": acquisitions typed as mergers, both parties "partner". | fixed |
| 4.19 | A new term version stores only newly stated figures; earlier terms read as not stated / removed. | fixed |
| 4.20 | Every state's `effective_at` is the announcement date. | fixed |
| 4.21 | Document committed before deal rows; a failed deal insert is lost forever. | fixed |
| 4.22 | 14-day join window: a closing 8-K months later creates a second deal. | fixed |
| 4.23 | `context_from_facts` keeps the last row by `known_at` not `period_end`, ignores dimensions, can compare a target with itself. | fixed |
| 4.24 | Low: `"rate" in low` matches "Corporate"; `stake_pct` matches "5.25% interest"; first "$X per share" wins; date cast in session time zone. | fixed |

Screening, look-through, answer
| # | Finding | Status |
|---|---|---|
| 4.25 | `segment_surprise_pct` and `debt_maturity_coverage` can never produce a value. | fixed |
| 4.26 | `ratio_field` falls back to an older period when the latest denominator is ≤ 0. | fixed |
| 4.27 | `look_through.LATEST_FUND_SQL` doubles a fund's weight when two share classes are held. | fixed |
| 4.28 | `numbers_grounded` drops sign/parentheses and unit words, splits dates, reads "12,5" as 125. | fixed |

Jobs and migrations
| # | Finding | Status |
|---|---|---|
| 4.29 | A filing that always yields 0 observations holds a backfill slot for 30 days; failing ESEF versions retry forever. | fixed |
| 4.30 | ESEF `published_at` set to the registry add time instead of null. | fixed |
| 4.31 | Rollback `20261007190000…down.sql` deletes from append-only `quality_review_event` without lifting the guard. | fixed |
| 4.32 | `purge_source_documents` follows one level in arbitrary order; fails on deal/estimate rows. | fixed |
| 4.33 | `20261008100000…down.sql` does not undo the `screen_version_immutable` search path / `rls_auto_enable` revoke. | kept (deliberate: re-opening a security warning on rollback is wrong) |

## 5. Frontend

| # | Finding | Status |
|---|---|---|
| 5.1 | Pressing "Show…" again with the same inputs clears the data and never refetches (deals, estimates, network, insiders, ownership, look-through). ✔ | fixed |
| 5.2 | After a failed refetch the previous date's data is shown under the new date, or "Loading…" forever (insiders, ownership, deals, estimates, network, alerts, adjudication, pair reviews, holding alerts panel). ✔ | fixed |
| 5.3 | Errors shown as absence: evidence browser falls back to sample on error ✔; PDMR error reads "no notice" ✔; portfolio panels swallow HTTP errors. | fixed |
| 5.4 | Missing as zero: empty roles drawn as a 0 net bar; unknown scenario bounds "between 0% and 0%" ✔; coverage roll-up sums null denominators ✔; unknown refinanced amount = 0 ✔. | fixed |
| 5.5 | Alerts list capped at 200 by `created_at` (old unread drop out); holding panel caps at 20 with no "more" note. | fixed |
| 5.6 | TS copies diverge: `netFromTransactions` plan/bundled counts; `maturityCheck` rounding; `formatTerm` 0.07 → "7.0%"; sample `dilutionImpact`/`buybackImpact` NaN/Infinity; case-sensitive estimate metric matching; hardcoded estimate default. | fixed (the estimates page opens on the metric and period with the most consensus lines) |
| 5.7 | Forms: no double-click guard on mark-read / prelabel / accept; no-op insider override allowed; optimistic adjudication labels with no rollback; dataset switch races. | fixed |

## 6. Plan rows marked built that were not

| # | Claim | Status |
|---|---|---|
| 6.1 | "Automated now" — see 1.3. | owner |
| 6.2 | Deal-state and note-value gates "coded" — see 2.4. | fixed |
| 6.3 | "Only a person's labels count" — see 2.1. | fixed |
| 6.4 | Conditional fetch and incremental cursors (P1.2): `fetch.py` supports validators but no caller passes them; `disclosure_source.cursor` unused. | fixed: the SEC ingest keeps a per-CIK filing cursor plus the listing's validators; the deal listing, ESEF index and consensus pages go through `conditional.ConditionalFetch`, which remembers validators only after the response was fully handled. Archive documents and ESEF reports are immutable and skipped by id or hash, so they need no validators |
| 6.5 | Estimate evidence: `estimate_value.evidence_id` always null. | fixed |

## 7. Still open from the plan (not new)

Human read of every adjudication sample and manifest slot (0/110 captured); 30-day shadow run; browser pass over the 11 pages and P8 restyle; FCA HTTP 400; EDINET and Companies House keys; SEDAR+ session; consensus source URLs; TR-1/PSC/GLEIF collection; non-UK PDMR; release order shown, not enforced; `pyright`; migration-history reconcile. (The deal-party review page now exists: `/disclosures/deals/parties`.)

## 8. What the fixes changed (for review)

- Schema, `20261008200000_disclosure_audit_fixes` (rollback in `supabase/rollback/`): wider observation and estimate identities; `adjudicator_kind` on labels and their history (production's 179 model labels backfill to `model`); `deal_state` dataset; `disclosure_backfill_attempt`; append-only `deal_participant_decision`; recursive `purge_rows`.
- Gates read only `person` and `accepted_suggestion` labels; "accept unflagged" stores `accepted_suggestion`. After the migration the insider-class gate in production reads **not sampled**, which is the true state.
- Alerts: insider alerts are keyed `insider:{ticker}:{insider}:{date}:{reason}` and summed per person, day and direction. Alerts already stored under the old keys are recognised by accession, so they do not fire again.
- `scan_all` runs each scan on its own; `failed` counts failed scans, not tickers.
- Tests: `tests/test_disclosure_audit_fixes.py`, `test_disclosure_audit_deals.py`, `test_disclosure_audit_screening.py`, web `src/lib/disclosure/audit-fixes.test.ts`. Four older tests were updated where they encoded the audited behaviour (ratio fall-back, relationship rows without a basis or label, deal rows without `known_at`, one failure per ticker).
- The migration also adds `sec_filing_item` (8-K items, audit 3.5).
- Production: the migration's preconditions were checked read-only on 2026-10-08 (every constraint, index, trigger and function it touches exists under the expected name; all 179 labels match the model-author pattern; 0 deal-party decisions). The first apply call failed with an MCP session error and changed nothing; the second applied it on 2026-10-08. Verified after: both new tables and the `purge_rows` function exist, `adjudicator_kind` is on both label tables, all 179 labels read `model` (so the insider gate now reads not sampled), the backfill wrote no history rows, and all 10,765 observations are intact. The migration history row carries the apply-time version, not `20261008200000` (see 1.2).
- DB-backed tests ran on a local Postgres 17 on 2026-10-08. They found five failures, all fixed:
  - Three tests asserted the audited behaviour: a decision made after the cutoff showing in the past (4.1), and the old sample size (2.5). They were updated.
  - A surprise sourced from the company's own earnings-release document now counts that document as the release, like an Item 2.02 8-K.
  - Rollback files: `20261008100000…120000` lacked the required header. `20261008100000` left the `hs_concordance` columns behind and now drops them only while they are empty. The new rollback now restores `purge_source_documents` byte for byte.
  - The rollback-lifecycle test now proves every rollback, the new one included.
  - New DB tests: `tests/test_disclosure_audit_fixes_db.py`.
