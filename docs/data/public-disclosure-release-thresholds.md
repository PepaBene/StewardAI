# Public-disclosure release thresholds

Plan P0.6, written **2026-10-07**, before any collector is enabled. A dataset is released to a screen or alert only when every row for it below is met on a manually adjudicated sample, reported with numerator and denominator. These are proposals for the owner to adjust; they are decided *before* the numbers exist so they cannot be fitted to results.

## Shared gates

| Gate | Threshold | How measured |
|---|---|---|
| Evidence precision | A material claim's cited span contains the stated value/passage | adjudicated sample per dataset |
| Identity match precision | Confirmed entity matches are correct; name-only matches are never confirmed | adjudicated sample; any wrong confirmed match blocks release until explained |
| Numeric reconciliation | Value, unit, scale, sign, currency, period and share class reproduce the filed figure | arithmetic checks plus sample |
| Point-in-time | Zero rows visible before their `known_at` | automated replay test on every release (`test_disclosure_db.py`) |
| Freshness | Latest known document per tracked issuer is within the source's publication lag plus one polling interval | coverage report |
| Zero-dollar | $0 spent; no source needing a paid plan, trial or bypass | cost ledger and access state |
| Access | Source `access_state = available` with a recorded test date | `disclosure_source` |

## Per-dataset release thresholds

| Dataset | Evidence precision | Identity precision | Numeric reconciliation | Adjudication sample (plan P9.3) | Coverage floor to publish a ranking/alert |
|---|---|---|---|---|---|
| Relationship claims (customer/supplier) | 95% | 98% confirmed named edges | percentages tie to denominator/period | 100 claims, including deliberate false-positive phrases | none; shown as partial, unknown remainder kept |
| Ownership / stakes | 98% | 98% | shares/percent tie to class and denominator | 50 owner/stake matches | do not publish "top owners" below 80% of the issuer's reported 13F holders sampled |
| Insider economic classes | 98% (code and footnote) | 98% | shares/price/post-holdings tie | 50 classifications | alerts only on verified discretionary open-market activity |
| Deal states and terms | 95% | 98% | amounts/currency/units tie to the document | 30 states/terms; chronology checked | none; missing terms shown as unknown |
| Consensus revisions | 98% (file, date, metric) | n/a | comparable basis only | 20 snapshot pairs | revision alerts and screens only after the 20 pairs pass |
| Financial note values | 98% | 98% | statement and segment arithmetic within documented tolerance | 30 note values | a value feeds a valuation or screen only if reconciled |
| Filing/document discovery (P1) | n/a | n/a | n/a | the three-cutoff replay on the corpus | every enabled issuer discovered incrementally with no missed accession in a 30-day shadow run |

## Adjudication process

- Two passes: one person reads the span and records the expected value before seeing the machine value; disagreements are listed with the reason.
- A sample is drawn per source/form, not only the easy cases; deliberate negatives are included.
- Results are stored as `quality_review` decisions with the original candidate, so a number can be re-derived.
- A threshold miss blocks release of that dataset only; others continue.

## Status

### Measured 2026-10-08 on the production database (labels by the model, not a person)

The owner asked the model to adjudicate with its own judgement. Labels are stored under sample key `prod-20261008-model`, reviewer `claude-model (not a human)`, every note starting `[model-adjudicated]`. The process above asks for a person, so **no gate below counts as passed for release**; the numbers say where the datasets stand and which defects to fix.

| Dataset | Labelled | Correct | Rate | Bar | Result |
|---|---|---|---|---|---|
| Relationship claims | 93 real + 7 planted | 86 of 93; 7 of 7 planted caught | 92.5% | 95%, 100 real claims | not met: below the bar and 93 of 100 real claims (production holds 101 claims in all) |
| Insider economic classes | 55 | 54 | 98.2% | 98%, 50 items | meets the bar on the model's reading; one dividend-reinvestment purchase filed under code P was classed an open-market buy |
| Ownership matches (13F CUSIP to ticker) | 24 | 24 | 100% | 98%, 50 items | not met: only 24 CUSIP and ticker pairs exist |
| Deal states, revision pairs, note values | 0 | n/a | n/a | n/a | not measurable: no deals stored, no consensus snapshots, no note sample drawn |

Relationship misses, each a defect to fix: three TED procurement awards matched "CISCO" (a French construction firm) to Cisco Systems; two "All Other" residual buckets stored as customers; one risk sentence ("If we lose Microsoft Corporation's support") stored as a supplier; one 2023 customer share stored for a year in which no customer reached 10%; one geographic share (non-U.S. customers) stored as a customer relationship.

### Zero-dollar check (P0.6)

Every source used in the 2026-10-08 runs is free: SEC EDGAR and company-facts, GLEIF, filings.xbrl.org, TED, and the public SEDAR+ search. No paid key was used; EDINET and Companies House need free keys the owner does not have, so those two lanes are not run.

### Capture manifest

`samples_manifest.json` has 30 slots checked by the model against the original source (10 Form 4 purchases and sales, 10 non-discretionary Form 4 rows, 10 SEDAR+ rows) and 20 marked unavailable (N-PORT: no held US-ISIN fund; consensus: no issuer source registered). 60 slots are still pending. A slot counts as captured only after a person reads it, so `captured` stays 0.
