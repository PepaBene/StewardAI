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

No dataset has been measured yet. All thresholds above are unmet by definition until the adjudicated samples exist (`samples_manifest.json` has zero captured slots).
